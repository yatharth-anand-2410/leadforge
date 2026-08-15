# Lead Forge — Implementation Plan

Phased, sequential build. Each phase produces a runnable increment.

## Phase 0 — Repo scaffold, tooling, phase doc
- [x] Folder structure, `Makefile`, `.gitignore`, `.env.example`, root `README.md`.
- [x] Backend `uv` project + FastAPI `/health`.
- [x] Frontend Next.js scaffold (TS, Tailwind).
- [x] This doc.

## Phase 1 — Backend foundation: DB + auth
- [x] `config.py`, `db.py` (SQLAlchemy + SQLite WAL).
- [x] Models: `user`, `discovery`, `job`, `lead`; schemas.
- [x] `auth.py` (bcrypt + JWT), `routers/auth.py`, router mounting.
- Accept: register → login → `/auth/me` returns the user.

## Phase 2 — Job queue + worker skeleton
- [x] `worker/queue.py` (claim/complete/fail), `worker/worker.py` no-op loop.
- [x] Jobs on `POST /discoveries`; `/jobs/{id}`, `/discoveries/{id}/jobs`, re-run.
- Accept: create discovery → job queued → worker completes placeholder.

## Phase 3 — Data-source & verification services
- [x] `services/overpass.py`, `nominatim.py`, `website.py`, `verify.py`, `dedupe.py`, `scoring.py` + tests.
- Accept: pytest green; manual Overpass call returns clinics in Bengaluru.

## Phase 4 — Deep agent (orchestrator + tools)
- [x] `agent/prompts.py`, `agent/tools.py`, `agent/agent.py` (`create_deep_agent` + `ChatGroq`).
- Accept: headless run returns ≤10 leads with verified contacts.

## Phase 5 — Wire agent into worker (backend e2e)
- [x] Worker runs agent; `save_lead`/`progress` persist; re-run; error capture.
- Accept: API create discovery → worker → `GET leads` returns verified shortlist.

## Phase 6 — Frontend: auth + dashboard + setup form
- [x] `lib/api.ts`, auth context, `/login`, `/register`, `/`, `/discoveries/new`.
- Accept: register/login; submit form queues a job.

## Phase 7 — Frontend: discovery detail + leads table
- [x] `/discoveries/[id]` SWR polling, leads table, shortlist filter, re-run.
- Accept: watch a job progress and render verified leads.

## Phase 8 — Hardening, tests, docs, attribution
- [x] Rate-limit pacing, iteration/timeout guards, error handling.
- [x] OSM attribution footer, README finalize, `make test` green.
- Accept: `make api && make worker && make web` works end-to-end.
