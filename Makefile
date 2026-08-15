.PHONY: install api worker web test

install:
	cd backend && uv sync --extra dev

api:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

worker:
	cd backend && uv run python -m app.worker.worker

web:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest
