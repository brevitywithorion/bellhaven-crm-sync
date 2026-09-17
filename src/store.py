from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        text = path.read_text()
    except OSError:
        return default
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def save_json(path, payload) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        path.write_text(text)


def fingerprint(actions: list[dict], extra: Any = None) -> str:
    blob = json.dumps({"actions": actions, "extra": extra}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def load_decisions() -> dict:
    return load_json(config.DECISIONS_PATH, {"decisions": {}})


def save_decisions(doc: dict) -> None:
    save_json(config.DECISIONS_PATH, doc)


def record_decision(key: str, status: str, fingerprint_val: str, note: str = "") -> None:
    doc = load_decisions()
    doc.setdefault("decisions", {})[key] = {
        "status": status,
        "fingerprint": fingerprint_val,
        "note": note,
        "at": _now(),
    }
    save_decisions(doc)


def should_skip(key: str, fingerprint_val: str) -> tuple[bool, str]:
    prev = load_decisions().get("decisions", {}).get(key)
    if not prev:
        return False, ""
    if prev.get("fingerprint") == fingerprint_val and prev.get("status") in {"approved", "rejected"}:
        return True, prev["status"]
    return False, prev.get("status") or ""
