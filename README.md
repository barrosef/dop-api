# dop-api

The DOP platform's BFF. Two doors in, a single core behind them:

- **REST + SSE** → the cockpit (`dop-app`)
- **gRPC** → `dop-cli` and the sandboxes' agents

## The rule that defines this process

**The BFF has no database** (ADR-0016). No connection to Postgres — not even
"just for a quick query". Two owners of the schema is how the boundary dies in
three weeks. When it needs state, it **calls the core**, which writes the state
and the event in the same transaction.

What lives here: protocol translation, resolving the active account, signed
upload URLs and the conversation with the models (`AgentRuntime`).

## One use case, two transports

REST and gRPC are **adapters**, not implementations. The rule lives once, in
`app/usecases/`, and both ports call the SAME function:

```
app/routers/identity.py   ─┐
                           ├─→  app/usecases/identity.py  ─→  app/coreclient/
app/grpcapi/identity.py   ─┘
```

The cross-cutting decorators (`@log`, `@account_scoped`, `@require_role`) live
in the **use case**, not in the adapter — authorization pinned to the router
would hold for REST alone, and the gRPC door would be born open.

| end | authentication | active account | error |
|---|---|---|---|
| REST | the `authorization` header | the `x-account-id` header | an HTTP status |
| gRPC | the `authorization` metadata | the `x-account-id` metadata | a gRPC status |

In both cases the one that fills in the `AuthContext` is the same
`CoreResolver`, and the one that writes a 5xx detail is the same `_detail_for`.
`tests/test_grpc_identity.py` has a whole class (`TestParityBetweenTransports`)
that asks for the same thing through both ports and compares — it is the alarm
that fires if anybody reimplements a use case in an adapter.

> **A trap already solved:** in `grpc.aio`, a ContextVar set inside
> `intercept_service` does **not** reach the servicer — the interceptor only
> returns the handler, which the library runs later, in another context. That is
> why `app/grpcapi/interceptors.py`'s interceptors **wrap**
> `handler.unary_unary` instead of preparing the ground before the continuation.

The gRPC port comes up in the same process as FastAPI, driven by the lifespan
(`grpc_port`, `grpc_enabled` in `app/settings.py`), after the channel to the
core and before it on shutdown — gracefully, so as not to cut a write in
flight.

## Cross-cutting concerns by decorator

The pattern: **a ContextVar filled in by middleware, decorators that read the
context**. The handler takes no auth or logging parameter — the cross-cutting
concern stays invisible in the business code.

| decorator | effect |
|---|---|
| `@log(level=, mask=[])` | entry, exit, error and duration; automatic secret masking |
| `@public` | exempts from authentication (registered by a route sweep at boot) |
| `@account_scoped` | requires an active account and **refuses a request without one** — SP-0's rule |
| `@require_role("admin")` | a role in the active account |
| `@require_grant("use")` | a grant over a resource (ADR-0013) |

They stack in the order log → scope → permission:

```python
@router.get("/accounts/current/members")
@log
@account_scoped
@require_role("owner", "admin")
async def list_members() -> list[dict]: ...
```

## Logging

Structured JSON, with the **same canonical fields as the core's** — `ts`,
`level`, `component`, `request_id`, `account_id`, `duration_ms`. An aggregated
log is only useful if both ends speak the same language.

The automatic mask covers `password`, `token`, `secret`, `authorization`,
`api_key`, `private_key`, `client_secret`, `id_token`, `cpf`, `cnpj` — at any
depth of the event.

> **A trap already solved:** a logger bound at import freezes the default
> configuration (`configure()` runs in the lifespan) and the lines come out
> outside the format, silently. Always `get_logger()` at the point of use.

## Contracts (.proto) and generated stubs

There are **two** contracts, and the difference between them is intentional:

| contract | where it lives | who consumes it | shape |
|---|---|---|---|
| `dop.v1` | `dop-core/api/proto` | the BFF | normalized, granular |
| `dop.bff.v1` | `api/proto/` **in this repo** | the app, `dop-cli`, the agents | aggregated, per screen |

The core keeps the state, so its model reflects the database. The edge serves
the screen: `Me` answers in one call what in the core would take `EnsureUser` +
`ListAccounts` + `ListMemberships`. The **vocabulary**, that one is the same —
`Role`, `Kind` and `Status` repeat the name and the number of `dop.v1`'s enums,
because a parallel dictionary is a translation bug waiting to happen.

The edge's contract has **no `CallContext`**: identity comes from the token, in
the `authorization` metadata, and the active account from `x-account-id`. An
identity field in the body would be a second source of truth the server would be
obliged to ignore.

```bash
make proto                                  # dop.v1     → app/coreclient/gen/
make proto-bff                              # dop.bff.v1 → app/grpcapi/gen/
make proto-all                              # both
DOP_CORE_PROTO=/path/api/proto make proto
```

Neither output **is edited by hand**.

> **A trap already solved:** `protoc` generates `from dop.v1 import common_pb2`
> (and `from dop.bff.v1 import identity_pb2`), which would only resolve with
> `gen/` at the root of `sys.path`. Rather than touching `sys.path` at runtime —
> which breaks in ways that are hard to debug, and differently under pytest and
> under uvicorn — the script rewrites the import to
> `from app.coreclient.gen.dop.v1 import ...`. It is deterministic, it shows up
> in the diff, and the rest of the app imports it like any other module. Both
> scripts do the same, each for its own package. `ruff` ignores the generated
> directories (`extend-exclude` in `pyproject.toml`).

Whoever talks to the core goes through `app/coreclient/`:

| module | role |
|---|---|
| `client.py` | the single channel, retry, deadline and the context metadata |
| `stubs.py` | the stub factory — **the single point** a test replaces |
| `convert.py` | the proto's enum ↔ the edge's string, and the `CallContext` |
| `resolver.py` | `(principal, account_id) → (user_id, role, grants)`, by asking the core |

The `resolver` runs in `AuthMiddleware`: it calls `EnsureUser` (idempotent, on
every login), confirms the membership in the requested account through
`ListAccounts` — **no membership is a 403** — and discovers the role in
`ListMemberships`. Resource grants come back empty while `ResourceService` does
not expose a per-user query; owner and admin keep their implicit `manage`.

## Development

```bash
uv sync
uv run pytest -q
FIREBASE_AUTH_EMULATOR_HOST=localhost:9099 uv run uvicorn app.main:app --reload
```

`uvicorn` brings both ports up: HTTP on 8000 and gRPC on 9095 (`GRPC_PORT` in
the environment or `grpc_port` in `.env`). To bring up REST only,
`GRPC_ENABLED=false`.

Ready-made Bruno collections in the meta-repository's `docs/api/`: they fetch
the token from the emulator and go straight to calling the BFF authenticated.
