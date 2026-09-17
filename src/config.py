from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

SITE_BASE = os.environ.get("SITE_BASE", "https://analyst-assessment-production.up.railway.app").rstrip("/")
CRM_BASE = os.environ.get("CRM_BASE", "https://analyst-assessment-production.up.railway.app/api/v1").rstrip("/")

BELLHAVEN_PARENT_NAME_HINT = "Bellhaven Senior Living (Parent Account)"

WEBSITE_PATH = DATA / "website_communities.json"
ACCOUNTS_PATH = DATA / "accounts_snapshot.json"
PROPOSALS_PATH = DATA / "proposals.json"
DECISIONS_PATH = DATA / "decisions.json"

CARE_MAP = {
    "assisted living": "Assisted Living",
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "short-term rehabilitation and nursing": "Skilled Nursing",
    "memory support": "Memory Care",
    "assisted living memory support": "Assisted Living",
    "memory care": "Memory Care",
    "skilled nursing": "Skilled Nursing",
    "independent living": "Independent Living",
}


def crm_token() -> str:
    token = os.environ.get("CRM_TOKEN", "").strip()
    env_file = ROOT / ".env"
    if not token and env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("CRM_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not token:
        raise SystemExit(
            "CRM_TOKEN is not set. Export it or put CRM_TOKEN=... in .env"
        )
    return token
