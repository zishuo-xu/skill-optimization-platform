"""Global configuration for the Skill optimization platform.

All runtime data lives under ``SKILLOPT_DATA_DIR`` (default ``<repo>/data``) so the
application can be started from any working directory and tests can use isolated
temporary data roots.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
REPO_ROOT = APP_ROOT.parent
WEB_DIR = REPO_ROOT / "web"
SAMPLES_DIR = APP_ROOT / "samples"

DATA_DIR = Path(os.environ.get("SKILLOPT_DATA_DIR", REPO_ROOT / "data")).resolve()
DB_PATH = Path(os.environ.get("SKILLOPT_DB_PATH", DATA_DIR / "app.db")).resolve()

SKILLS_DIR = DATA_DIR / "skills"
ZIPS_DIR = DATA_DIR / "zips"
TRACE_DIR = DATA_DIR / "trace"
EXPORT_DIR = DATA_DIR / "exports"
TMP_DIR = DATA_DIR / "tmp"

API_PREFIX = "/api/v3"
SCORER_VERSION = "score-v1"
ACCEPTANCE_RULES_VERSION = "accept-v1"
OPTIMIZER_PROTOCOL_VERSION = "optimizer-v1"
PROMPT_TEMPLATE_ID = "business-system-prompt-v1"

# Optional pricing for reporting actual usage; no spending limits are enforced.
PRICE_PER_1K_INPUT_TOKENS = float(os.environ.get("SKILLOPT_PRICE_INPUT_1K", "0") or 0)
PRICE_PER_1K_OUTPUT_TOKENS = float(os.environ.get("SKILLOPT_PRICE_OUTPUT_1K", "0") or 0)

HOST = os.environ.get("SKILLOPT_HOST", "127.0.0.1")
PORT = int(os.environ.get("SKILLOPT_PORT", "8765"))

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
ACTIVE_RUN_STATUSES = {"queued", "running", "cancel_requested"}


def ensure_dirs() -> None:
    for path in (DATA_DIR, SKILLS_DIR, ZIPS_DIR, TRACE_DIR, EXPORT_DIR, TMP_DIR):
        path.mkdir(parents=True, exist_ok=True)
