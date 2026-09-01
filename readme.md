# Lead Forge

A lead-generation engine. Define an ICP (business category, location, industry, size, roles, keywords) and a background **deep agent** (LangChain `deepagents`, with a configurable Groq / Gemini / OpenCode Go backend) discovers real businesses, crawls their websites, extracts and verifies contact points, deduplicates, scores, and returns a shortlist of qualified leads.

## Stack (monorepo)

| Layer            | Tech                                              |
| ---------------- | ------------------------------------------------- |
| Frontend         | Next.js 16 (App Router), TypeScript, Tailwind     |
| Backend          | FastAPI + Python (managed with `uv`)              |
| Database         | SQLite (data **and** job queue)                   |
| Background jobs  | Standalone worker polling a `jobs` table          |
| Agent            | LangChain `deepagents` + Groq / Gemini / OpenCode Go |
| Data sources     | Overpass/OSM, Nominatim, SearXNG web search (optional), company website crawl, DNS (MX) |

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
#    set the LLM key for your LLM_PROVIDER (e.g. GROQ_API_KEY) and JWT_SECRET
#    optionally start local web search: make searxng  (then SEARXNG_ENDPOINT=http://localhost:8080)

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
3. The agent discovers businesses (Overpass/Nominatim + SearXNG web search when
   configured), crawls their websites,
   verifies emails (format + DNS MX) and phones, deduplicates, scores, and saves
   shortlisted leads — stopping once it has the target count (default 10) of leads
   each with ≥1 verified contact point, or when sources are exhausted.
4. The dashboard/detail page shows job progress and the shortlisted leads.

## Attribution

Business data is sourced from OpenStreetMap; the UI attributes
© OpenStreetMap contributors (ODbL).

## Notes

- A single worker process is expected; the SQLite queue uses optimistic row-claiming.
- The agent's chat backend is selected by `LLM_PROVIDER` (default `groq`; also
  `gemini`, `opencode-openai`, `opencode-anthropic`). Each provider needs its key:
  `GROQ_API_KEY`, `GEMINI_API_KEY` (Google AI Studio), or `OPENCODE_API_KEY`
  (OpenCode Go). Without the key for the configured provider, jobs fail with an
  API-key error. `LLM_MODEL` is the provider-specific model id (e.g.
  `openai/gpt-oss-20b`, `gemini-2.5-flash`, `deepseek-v4-pro`, `qwen3.7-plus`).
- Set `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` to trace each job's agent run to LangSmith
  (project `leadforge`), tagged with `discovery_id` / `job_id` / `user_id`.
- `SEARXNG_ENDPOINT` enables free web search (SearXNG metasearch). Leave blank to
  disable; web results are merged into `search_businesses` and cover categories OSM
  has no tag for. Public instances block non-browser clients (anti-bot JS challenges,
  429s), so run your own with `make searxng` (Docker on `localhost:8080`) and set
  `SEARXNG_ENDPOINT=http://localhost:8080`. A configured but unreachable instance
  degrades results to OSM-only and is surfaced in the worker logs and the agent's
  `progress` report.
- `ENABLE_OSM` (default `true`) disables the Overpass/Nominatim sources globally
  when set to `false`, making every `search_businesses` call web-only (requires
  `SEARXNG_ENDPOINT`). The save-time location guard stays active in web-only mode.
- V1 discovers physical businesses via OSM; pure-digital/SaaS lead discovery (web search,
  Common Crawl, registries) is out of scope for now.
