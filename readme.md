# Lead Forge

A lead-generation engine. Define an ICP (business category, location, industry, size, roles, keywords) and a background **deep agent** (LangChain `deepagents` on Groq) discovers real businesses, crawls their websites, extracts and verifies contact points, deduplicates, scores, and returns a shortlist of qualified leads.

## Stack (monorepo)

| Layer            | Tech                                              |
| ---------------- | ------------------------------------------------- |
| Frontend         | Next.js 16 (App Router), TypeScript, Tailwind     |
| Backend          | FastAPI + Python (managed with `uv`)              |
| Database         | SQLite (data **and** job queue)                   |
| Background jobs  | Standalone worker polling a `jobs` table          |
| Agent            | LangChain `deepagents` + Groq (free tier)         |
| Data sources     | Overpass/OSM, Nominatim, company website crawl, DNS (MX) |

## Layout

```
backend/   FastAPI app, worker, agent, services, tests
frontend/  Next.js app (App Router)
docs/      Implementation plan
```

## Quickstart

```bash
# 1. Backend deps
make install            # uv sync --extra dev

# 2. Configure
cp backend/.env.example backend/.env
#    set GROQ_API_KEY (required for the agent) and JWT_SECRET

# 3. Frontend deps
cd frontend && npm install && cd ..

# 4. Run (three terminals)
make api                # FastAPI  -> http://localhost:8000
make worker             # job worker
make web                # Next.js  -> http://localhost:3000
```

Then open http://localhost:3000, register, and create a discovery.

## Tests

```bash
make test               # backend pytest suite (uv run pytest)
```

## How it works

1. A user creates a **discovery** (ICP definition); this enqueues a `jobs` row.
2. The **worker** claims the job and runs the **deep agent**.
3. The agent discovers businesses (Overpass/Nominatim), crawls their websites,
   verifies emails (format + DNS MX) and phones, deduplicates, scores, and saves
   shortlisted leads — stopping once it has the target count (default 10) of leads
   each with ≥1 verified contact point, or when sources are exhausted.
4. The dashboard/detail page shows job progress and the shortlisted leads.

## Attribution

Business data is sourced from OpenStreetMap; the UI attributes
© OpenStreetMap contributors (ODbL).

## Notes

- A single worker process is expected; the SQLite queue uses optimistic row-claiming.
- The agent requires `GROQ_API_KEY`. Without it, jobs will fail with an API-key error.
- V1 discovers physical businesses via OSM; pure-digital/SaaS lead discovery (web search,
  Common Crawl, registries) is out of scope for now.
