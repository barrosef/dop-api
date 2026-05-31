# dop-api — núcleo + API HTTP do DOP

Núcleo do produto DOP e sua **API HTTP**. É a **única fonte de verdade**: acessa a
workspace e as ferramentas (git / azure / jira / docker), **detém o estado** e
**hospeda/dirige o Claude**.

A lógica de negócio que hoje vive na **dop-cli** (`git/`, `platform/`, `runtime/`,
`core/`) **migra para cá**; a CLI 1.0 passa a ser **cliente fino** desta API.

- **Contrato preliminar** da API: interface `DopApi` + `types.ts` em
  `docs/prd/dop-1.0-mvp/replit-frontend-prompt.md` (repositório raiz `dop`).
- **Stack:** a definir (Python). Sem deploy em nuvem / sem CI-CD nesta fase.
- **Atores:** backend = Claude (arquitetura/integração/implementação).

Status: 🚧 em construção (1.0 MVP).
