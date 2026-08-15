.PHONY: install api worker worker-dev web test

install:
	cd backend && uv sync --extra dev

api:
	cd backend && uv run uvicorn app.main:app --reload --reload-include ".env" --port 8000

worker:
	cd backend && uv run python -m app.worker.worker

worker-dev:
	cd backend && uv run python -m app.worker.worker_dev

web:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest
