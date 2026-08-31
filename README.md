# dop-api

BFF da plataforma DOP. Duas portas de entrada, um só núcleo atrás:

- **REST + SSE** → o cockpit (`dop-app`)
- **gRPC** → o `dop-cli` e os agentes dos sandboxes

## A regra que define este processo

**O BFF não tem banco** (ADR-0016). Nenhuma conexão ao Postgres — nem "só para
uma consulta rápida". Dois donos do schema é como a fronteira morre em três
semanas. Quando precisa de estado, ele **chama o core**, que grava estado e
evento na mesma transação.

Aqui vivem: tradução de protocolo, resolução de conta ativa, URLs assinadas de
upload e a conversa com os modelos (`AgentRuntime`).

## Um caso de uso, dois transportes

REST e gRPC são **adaptadores**, não implementações. A regra vive uma vez só,
em `app/usecases/`, e as duas portas chamam a MESMA função:

```
app/routers/identity.py   ─┐
                           ├─→  app/usecases/identity.py  ─→  app/coreclient/
app/grpcapi/identity.py   ─┘
```

Os decorators transversais (`@log`, `@account_scoped`, `@require_role`) ficam no
**caso de uso**, não no adaptador — autorização presa ao router valeria só para
o REST, e a porta gRPC nasceria aberta.

| ponta | autenticação | conta ativa | erro |
|---|---|---|---|
| REST | cabeçalho `authorization` | cabeçalho `x-account-id` | status HTTP |
| gRPC | metadado `authorization` | metadado `x-account-id` | status gRPC |

Nos dois casos quem preenche o `AuthContext` é o mesmo `CoreResolver`, e quem
redige detalhe de 5xx é o mesmo `_detail_for`. `tests/test_grpc_identity.py`
tem uma classe inteira (`TestParidadeEntreTransportes`) que pede a mesma coisa
pelas duas portas e compara — é o alarme que dispara se alguém reimplementar um
caso de uso num adaptador.

> **Armadilha resolvida:** em `grpc.aio`, ContextVar definido dentro de
> `intercept_service` **não** chega ao servicer — o interceptor só devolve o
> handler, que a biblioteca executa depois, noutro contexto. Por isso os
> interceptores de `app/grpcapi/interceptors.py` **envolvem**
> `handler.unary_unary` em vez de preparar o terreno antes da continuação.

A porta gRPC sobe no mesmo processo do FastAPI, controlada pelo lifespan
(`grpc_port`, `grpc_enabled` em `app/settings.py`), depois do canal com o núcleo
e antes dele no encerramento — gracioso, para não cortar escrita em voo.

**Streaming ainda não existe nesta porta**, de propósito: o núcleo está
ganhando os RPCs de stream agora, e a borda os expõe depois.

## Transversais por decorator

O padrão: **ContextVar preenchido por middleware, decorators que leem o
contexto**. O handler não recebe parâmetro de auth nem de log — o transversal
fica invisível no código de negócio.

| decorator | efeito |
|---|---|
| `@log(level=, mask=[])` | entrada, saída, erro e duração; máscara automática de segredos |
| `@public` | isenta de autenticação (registrado por varredura de rotas no boot) |
| `@account_scoped` | exige conta ativa e **recusa requisição sem ela** — regra do SP-0 |
| `@require_role("admin")` | papel na conta ativa |
| `@require_grant("use")` | concessão sobre um recurso (ADR-0013) |

Empilham-se na ordem log → escopo → permissão:

```python
@router.get("/accounts/current/members")
@log
@account_scoped
@require_role("owner", "admin")
async def list_members() -> list[dict]: ...
```

## Log

JSON estruturado, com os **mesmos campos canônicos do core** — `ts`, `level`,
`component`, `request_id`, `account_id`, `duration_ms`. Log agregado só é útil
se as duas pontas falarem a mesma língua.

Máscara automática cobre `password`, `token`, `secret`, `authorization`,
`api_key`, `private_key`, `client_secret`, `id_token`, `cpf`, `cnpj` — em
qualquer profundidade do evento.

> **Armadilha resolvida:** logger vinculado no import congela a configuração
> padrão (o `configure()` roda no lifespan) e as linhas saem fora do formato,
> silenciosamente. Sempre `get_logger()` no ponto de uso.

## Contratos (.proto) e stubs gerados

São **dois** contratos, e a diferença entre eles é intencional:

| contrato | onde vive | quem consome | forma |
|---|---|---|---|
| `dop.v1` | `dop-core/api/proto` | o BFF | normalizado, granular |
| `dop.bff.v1` | `api/proto/` **deste repo** | app, `dop-cli`, agentes | agregado, por tela |

O núcleo guarda o estado, então o modelo dele reflete o banco. A borda serve
tela: `Me` responde numa chamada o que no núcleo exigiria `EnsureUser` +
`ListAccounts` + `ListMemberships`. O **vocabulário**, esse é o mesmo — `Role`,
`Kind` e `Status` repetem nome e número dos enums de `dop.v1`, porque
dicionário paralelo é bug de tradução esperando acontecer.

O contrato da borda **não tem `CallContext`**: identidade vem do token, no
metadado `authorization`, e conta ativa do `x-account-id`. Campo de identidade
no corpo seria uma segunda fonte de verdade que o servidor teria obrigação de
ignorar.

```bash
make proto                                  # dop.v1     → app/coreclient/gen/
make proto-bff                              # dop.bff.v1 → app/grpcapi/gen/
make proto-all                              # as duas
DOP_CORE_PROTO=/caminho/api/proto make proto
```

Nenhuma das duas saídas **se edita à mão**.

> **Armadilha resolvida:** `protoc` gera `from dop.v1 import common_pb2` (e
> `from dop.bff.v1 import identity_pb2`), que só resolveria com `gen/` na raiz
> do `sys.path`. Em vez de mexer no `sys.path` em
> tempo de execução — que quebra de formas difíceis de depurar, e de maneira
> diferente sob pytest e sob uvicorn — o script reescreve o import para
> `from app.coreclient.gen.dop.v1 import ...`. É determinístico, aparece no
> diff, e o resto do app importa como qualquer outro módulo. Os dois scripts
> fazem o mesmo, cada um para o seu pacote. O `ruff` ignora os diretórios
> gerados (`extend-exclude` no `pyproject.toml`).

Quem fala com o núcleo passa por `app/coreclient/`:

| módulo | papel |
|---|---|
| `client.py` | canal único, retry, deadline e os metadados de contexto |
| `stubs.py` | fábrica dos stubs — **o ponto único** que o teste substitui |
| `convert.py` | enum do proto ↔ string da borda, e o `CallContext` |
| `resolver.py` | `(principal, account_id) → (user_id, role, grants)`, perguntando ao core |

O `resolver` roda no `AuthMiddleware`: chama `EnsureUser` (idempotente, em todo
login), confirma o vínculo com a conta pedida via `ListAccounts` — **sem vínculo
é 403** — e descobre o papel em `ListMemberships`. Concessões de recurso vêm
vazias enquanto o `ResourceService` não expuser consulta por usuário; owner e
admin seguem com `manage` implícito.

## Desenvolvimento

```bash
uv sync
uv run pytest -q
FIREBASE_AUTH_EMULATOR_HOST=localhost:9099 uv run uvicorn app.main:app --reload
```

O `uvicorn` sobe as duas portas: HTTP em 8000 e gRPC em 9095 (`GRPC_PORT` no
ambiente ou `grpc_port` no `.env`). Para subir só o REST, `GRPC_ENABLED=false`.

Coleções Bruno prontas em `docs/api/` do meta-repositório: pegam o token no
emulador e já saem chamando o BFF autenticado.
