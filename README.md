# Company Brain

Production-grade intelligence layer for unified enterprise search and LLM
orchestration on Microsoft Azure. See `docs/superpowers/specs/` for the
architecture spec and `docs/superpowers/plans/` for implementation plans.

## Phase 1 (current)

Grounded Q&A end-to-end against real Azure services. See
`docs/superpowers/plans/2026-05-28-company-brain-phase1-mvp-qa.md`.

## Layout

- `brain-api/` — Python FastAPI monolith (Zone 4 intelligence layer)
- `web/` — Next.js 14 chat UI with Entra SSO
- `infra/` — Azure provisioning (`az` CLI in v1; Bicep later)
- `docs/` — specs and plans
