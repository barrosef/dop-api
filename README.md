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

Os `.proto` são a **fonte da verdade** e vivem no `dop-core` (`api/proto`). Aqui
só se **gera**:

```bash
make proto                                  # usa ../dop-core/api/proto
DOP_CORE_PROTO=/caminho/api/proto make proto
```

A saída vai para `app/coreclient/gen/` e **não se edita à mão**.

> **Armadilha resolvida:** `protoc` gera `from dop.v1 import common_pb2`, que só
> resolveria com `gen/` na raiz do `sys.path`. Em vez de mexer no `sys.path` em
> tempo de execução — que quebra de formas difíceis de depurar, e de maneira
> diferente sob pytest e sob uvicorn — o script reescreve o import para
> `from app.coreclient.gen.dop.v1 import ...`. É determinístico, aparece no
> diff, e o resto do app importa como qualquer outro módulo. O `ruff` ignora o
> diretório gerado (`extend-exclude` no `pyproject.toml`).

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

Coleções Bruno prontas em `docs/api/` do meta-repositório: pegam o token no
emulador e já saem chamando o BFF autenticado.
