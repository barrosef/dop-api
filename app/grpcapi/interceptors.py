"""Interceptors do servidor gRPC — os MESMOS transversais do REST, outro transporte.

Cada um espelha um middleware que já existe:

    LoggingInterceptor  ↔  app/platform/logging/middleware.LoggingMiddleware
    AuthInterceptor     ↔  app/platform/security/middleware.AuthMiddleware
    ErrorInterceptor    ↔  app/platform/errors.grpc_exception_handler

A ordem em que entram no servidor é a mesma da pilha HTTP: log por fora (abre o
contexto e mede tudo, inclusive a falha de autenticação), erro no meio, auth por
dentro. O que muda é só a mecânica.

    Armadilha resolvida: em `grpc.aio`, ContextVar definido dentro de
    `intercept_service` NÃO chega ao handler — o interceptor só devolve o
    handler, que a biblioteca executa depois. Por isso todo interceptor aqui
    ENVOLVE o comportamento (`handler.unary_unary`) em vez de simplesmente
    preparar o terreno antes de chamar a continuação. É esse envelope que faz
    `auth_ctx` e `request_ctx` valerem dentro do servicer, como no REST.

O envelope de STREAMING (`handler.unary_stream`) tem duas exigências a mais, e
errar qualquer uma faz o RPC falhar de um jeito difícil de ligar à causa:

1. **Tem que ser função GERADORA.** O `grpc.aio` decide como executar o handler
   olhando para ele: `iscoroutinefunction` verdadeiro significa o estilo
   leitor/escritor (`await context.write(...)`, retorna None); falso significa
   gerador assíncrono. Um envelope escrito como `async def` sem `yield` seria
   classificado como o primeiro estilo, e o servidor jamais enviaria os itens
   que o servicer produziu — sem erro nenhum, só um stream vazio. Por isso todo
   `_envolve_stream` daqui contém `yield`.

2. **O ContextVar vive DURANTE o stream inteiro.** No unário, `set`/`reset`
   cercam um `await`. Aqui cercam um `async for` que dura o quanto o cliente
   ficar conectado — e o `reset` acontece na finalização do gerador, que pode
   ser feita pelo coletor de geradores assíncronos, noutro contexto. Daí o
   `reset` ser tolerante a falha: o contexto da task do RPC morre com ela de
   qualquer forma, e derrubar o encerramento por causa disso seria trocar um
   detalhe de higiene por um erro visível.
"""

import contextlib
import time
import uuid
from collections.abc import Callable

import grpc
from fastapi import HTTPException
from grpc.aio import AioRpcError
from pydantic import ValidationError

from app.platform.context import AuthContext, auth_ctx, request_ctx
from app.platform.errors import as_grpc, as_grpc_from_http
from app.platform.logging.config import FIELD_DURATION_MS, get_logger
from app.platform.security.firebase import FirebaseVerifier, InvalidToken


class _Interceptor(grpc.aio.ServerInterceptor):
    """Base: envolve unário E server-streaming, cada um com o seu envelope.

    Os dois casos precisam de envelopes DIFERENTES (o de streaming é uma função
    geradora, ver o docstring do módulo), mas a decisão de qual usar é sempre a
    mesma — então mora aqui, uma vez, e não em cada interceptor.

    `stream_unary` e `stream_stream` passam intactos: o contrato da borda não
    expõe nenhum, e um envelope escrito às cegas para eles seria código não
    exercido fingindo cobertura.
    """

    def _envolve(self, inner: Callable, details) -> Callable:  # pragma: no cover - abstrato
        raise NotImplementedError

    def _envolve_stream(self, inner: Callable, details) -> Callable:  # pragma: no cover
        raise NotImplementedError

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None:
            return handler
        if handler.unary_unary is not None:
            return grpc.unary_unary_rpc_method_handler(
                self._envolve(handler.unary_unary, handler_call_details),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        if handler.unary_stream is not None:
            return grpc.unary_stream_rpc_method_handler(
                self._envolve_stream(handler.unary_stream, handler_call_details),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        return handler


def _reset(var, token) -> None:
    """Devolve o ContextVar ao valor anterior, tolerando finalização alheia.

    Ver o item 2 do docstring do módulo: o `finally` de um gerador assíncrono
    pode rodar num contexto diferente daquele que criou o token, e aí o `reset`
    levanta ValueError. Isso é higiene, não correção — a task do RPC (e o
    contexto dela) termina junto.
    """
    with contextlib.suppress(ValueError):
        var.reset(token)


def _metadados(context) -> dict[str, str]:
    return {k: v for k, v in (context.invocation_metadata() or ())}


class LoggingInterceptor(_Interceptor):
    """Abre o contexto de log da chamada — campos canônicos idênticos aos do REST.

    `path` é o método gRPC completo e `method` é "grpc": é o que permite filtrar
    por transporte num log agregado onde as duas portas escrevem no mesmo lugar.
    A máscara de segredo não precisa ser repetida aqui — ela é um processor do
    structlog, então já cobre tudo que sai por `get_logger()`.
    """

    def _envolve(self, inner, details):
        async def behavior(request, context):
            md = _metadados(context)
            # Aceita o request_id de quem chamou (rastro entre serviços) ou cria um.
            request_id = md.get("x-request-id") or uuid.uuid4().hex
            token = request_ctx.set(
                {
                    "request_id": request_id,
                    "path": details.method,
                    "method": "grpc",
                    "client_ip": context.peer(),
                }
            )
            start = time.perf_counter()
            logger = get_logger()
            try:
                response = await inner(request, context)
            except Exception as exc:
                logger.error(
                    "request falhou",
                    path=details.method,
                    method="grpc",
                    status=str(context.code()),
                    error=str(exc),
                    **{FIELD_DURATION_MS: round((time.perf_counter() - start) * 1000)},
                )
                request_ctx.reset(token)
                raise
            logger.info(
                "request",
                path=details.method,
                method="grpc",
                status=str(context.code() or grpc.StatusCode.OK),
                **{FIELD_DURATION_MS: round((time.perf_counter() - start) * 1000)},
            )
            request_ctx.reset(token)
            return response

        return behavior


    def _envolve_stream(self, inner, details):
        """Mesmo contexto de log; o que muda é O QUE se mede.

        Num unário, `duration_ms` é o tempo de processar uma mensagem. Num
        stream, é a duração da CONEXÃO — e por isso há duas linhas, não uma:
        "stream aberto" na abertura (senão uma assinatura de seis horas só
        apareceria no log seis horas depois, e ninguém saberia que ela existe) e
        "stream" no encerramento, com a duração e quantos itens saíram.
        """

        async def behavior(request, context):
            md = _metadados(context)
            request_id = md.get("x-request-id") or uuid.uuid4().hex
            token = request_ctx.set(
                {
                    "request_id": request_id,
                    "path": details.method,
                    "method": "grpc",
                    "client_ip": context.peer(),
                }
            )
            start = time.perf_counter()
            logger = get_logger()
            enviados = 0
            logger.info("stream aberto", path=details.method, method="grpc")
            try:
                async for response in inner(request, context):
                    enviados += 1
                    yield response
            except Exception as exc:
                logger.error(
                    "stream falhou",
                    path=details.method,
                    method="grpc",
                    status=str(context.code()),
                    error=str(exc),
                    sent=enviados,
                    **{FIELD_DURATION_MS: round((time.perf_counter() - start) * 1000)},
                )
                raise
            else:
                logger.info(
                    "stream",
                    path=details.method,
                    method="grpc",
                    status=str(context.code() or grpc.StatusCode.OK),
                    sent=enviados,
                    **{FIELD_DURATION_MS: round((time.perf_counter() - start) * 1000)},
                )
            finally:
                # Também cobre o cliente que desiste no meio: o gerador é
                # finalizado, o `finally` roda, e o contexto não fica pendurado.
                _reset(request_ctx, token)

        return behavior


class ErrorInterceptor(_Interceptor):
    """Traduz exceção em status gRPC — a mesma semântica que o REST devolve.

    Erro do núcleo ATRAVESSA com o status dele (NOT_FOUND continua NOT_FOUND) e
    com o detalhe passado pelo redator que o REST já usa: detalhe de 5xx não
    vaza por nenhuma das duas portas.
    """

    def _envolve(self, inner, details):
        async def behavior(request, context):
            try:
                return await inner(request, context)
            except AioRpcError as exc:
                code, detail = as_grpc(exc)
            except HTTPException as exc:
                # Os decorators de segurança falam HTTPException — nasceram no
                # REST e continuam falando dele. Traduzir na saída é o que
                # permite reusá-los sem tocar no comportamento HTTP já testado.
                code, detail = as_grpc_from_http(exc)
            except ValidationError as exc:
                # Mensagem malformada é INVALID_ARGUMENT, o equivalente gRPC do
                # 422 que o FastAPI devolve para o mesmo caso.
                code, detail = grpc.StatusCode.INVALID_ARGUMENT, str(exc)
            except Exception as exc:
                # Falha nossa, não do cliente: registra inteira no log e devolve
                # genérica na rede, pela mesma razão do redator de 5xx.
                get_logger().error("erro não tratado no servicer", error=str(exc))
                code, detail = grpc.StatusCode.INTERNAL, "erro interno"
            await context.abort(code, detail)

        return behavior


    def _envolve_stream(self, inner, details):
        """Erro DEPOIS do primeiro item ainda é traduzível — e é aqui que se faz.

        A diferença para o HTTP é grande e vale registrar: no REST, status já
        enviado não se troca, e por isso `app/routers/stream.py` precisa
        converter o erro num evento SSE. Em gRPC o status viaja nos TRAILERS, no
        fim da chamada — então `context.abort` continua funcionando mesmo com
        metade do stream já entregue, e o cliente recebe o código certo em vez
        de um stream que acaba sem explicação.
        """

        async def behavior(request, context):
            try:
                async for response in inner(request, context):
                    yield response
                return
            except AioRpcError as exc:
                code, detail = as_grpc(exc)
            except HTTPException as exc:
                code, detail = as_grpc_from_http(exc)
            except ValidationError as exc:
                code, detail = grpc.StatusCode.INVALID_ARGUMENT, str(exc)
            except Exception as exc:
                get_logger().error("erro não tratado no stream", error=str(exc))
                code, detail = grpc.StatusCode.INTERNAL, "erro interno"
            await context.abort(code, detail)

        return behavior


class AuthInterceptor(_Interceptor):
    """Verifica o token e resolve a conta ativa — o AuthMiddleware, em gRPC.

    Preenche o MESMO `auth_ctx` que o REST preenche, com o MESMO resolver. Os
    decorators (@account_scoped, @require_role, @require_grant) leem dali e não
    têm como saber por qual porta a chamada entrou — que é exatamente o ponto.
    """

    def __init__(self, verifier: FirebaseVerifier, resolver=None):
        self.verifier = verifier
        # resolver(principal, account_id) -> (user_id, role, grants)
        # Quem decide papel e concessões é o CORE (ADR-0016); o BFF pergunta.
        self.resolver = resolver

    def _envolve(self, inner, details):
        # @public no método do servicer isenta de autenticação, como no REST.
        # functools.wraps propaga o marcador através de @log e dos demais
        # decorators, então a ordem em que ele é empilhado não importa.
        if getattr(inner, "__is_public__", False):
            return inner

        async def behavior(request, context):
            # Os dois envelopes (unário e stream) resolvem identidade pelo MESMO
            # par de métodos: duplicar a leitura do token aqui seria a forma
            # clássica de as duas portas divergirem sem ninguém notar.
            principal, account_id = await self._autentica(_metadados(context))
            user_id, role, grants = await self._resolve(principal, account_id)
            token = auth_ctx.set(
                AuthContext(
                    principal=principal,
                    user_id=user_id,
                    account_id=account_id,
                    role=role,
                    grants=grants,
                )
            )
            try:
                return await inner(request, context)
            finally:
                auth_ctx.reset(token)

        return behavior

    def _envolve_stream(self, inner, details):
        """Autenticação resolvida UMA vez, na abertura do stream.

        Revalidar a cada item seria caro (duas chamadas ao núcleo por evento) e
        também errado: quem decide o tempo de vida da sessão é a validade do
        token, verificada quando a conexão é feita — como acontece com uma
        conexão HTTP comum, que não reautentica a cada byte enviado. Cliente com
        token expirando reconecta; é o que o SSE já faz sozinho.
        """
        if getattr(inner, "__is_public__", False):
            return inner

        async def behavior(request, context):
            principal, account_id = await self._autentica(_metadados(context))
            user_id, role, grants = await self._resolve(principal, account_id)
            token = auth_ctx.set(
                AuthContext(
                    principal=principal,
                    user_id=user_id,
                    account_id=account_id,
                    role=role,
                    grants=grants,
                )
            )
            try:
                async for response in inner(request, context):
                    yield response
            finally:
                _reset(auth_ctx, token)

        return behavior

    async def _autentica(self, md: dict[str, str]):
        """Token → Principal, e conta ativa do metadado. Comum às duas formas."""
        # Identidade vem do TOKEN, não do corpo da mensagem. É por isso que
        # nenhum request do contrato da borda tem CallContext.
        raw = md.get("authorization", "")
        if not raw:
            raise HTTPException(status_code=401, detail="Não autenticado")
        try:
            principal = await self.verifier.verify(raw)
        except InvalidToken as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        # Conta ativa vem do metadado — é o seletor da interface, o mesmo
        # cabeçalho x-account-id do REST.
        return principal, md.get("x-account-id", "")

    async def _resolve(self, principal, account_id: str):
        if self.resolver is None:
            return "", "", {}
        try:
            return await self.resolver(principal, account_id)
        except HTTPException:
            # Já traduzida (e redigida) pelo `as_http` do resolver:
            # segue para o ErrorInterceptor virar status gRPC.
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Falha ao resolver a conta") from exc
