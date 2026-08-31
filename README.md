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

## Desenvolvimento

```bash
uv sync
uv run pytest -q
FIREBASE_AUTH_EMULATOR_HOST=localhost:9099 uv run uvicorn app.main:app --reload
```

Coleções Bruno prontas em `docs/api/` do meta-repositório: pegam o token no
emulador e já saem chamando o BFF autenticado.
