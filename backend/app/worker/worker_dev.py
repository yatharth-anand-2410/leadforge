"""Dev runner: auto-restarts the worker when code or ``.env`` changes.

The production worker (``python -m app.worker.worker``) polls for jobs in a
``while True`` loop and never exits, and it reads settings once at import time
(``app/config.py``). Editing ``.env`` therefore has no effect on a running
worker. This module wraps the worker in ``watchfiles`` so that any change under
``app/`` or to ``.env`` / ``pyproject.toml`` kills and respawns it in a fresh
interpreter, which re-reads ``.env`` and re-imports ``config.py``.

Run with: ``uv run python -m app.worker.worker_dev`` (or ``make worker-dev``).

The SQLite DB files (``leadforge.db*``) live at the backend root and are
intentionally *not* watched -- the worker writes to them on every job and
progress update, which would otherwise cause constant restarts.
"""

from __future__ import annotations

from pathlib import Path

from watchfiles import Change, run_process

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _log_changes(changes: set[tuple[Change, str]]) -> None:
    files = ", ".join(sorted(Path(path).name for _, path in changes))
    print(f"[worker-dev] change detected ({files}); restarting worker...")


def main() -> None:
    run_process(
        BACKEND_DIR / "app",
        BACKEND_DIR / ".env",
        BACKEND_DIR / "pyproject.toml",
        target="python -m app.worker.worker",
        target_type="command",
        callback=_log_changes,
    )


if __name__ == "__main__":
    main()
