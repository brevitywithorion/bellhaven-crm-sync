from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from . import config
from .crm import CRM
from .pipeline import apply_actions
from .store import load_json, record_decision, save_json

TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"


def _doc() -> dict:
    return load_json(config.PROPOSALS_PATH, {"pending": [], "skipped_already_decided_or_clean": []})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[review]", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        raw = json.dumps(payload, default=str).encode()
        self._send(code, raw, "application/json")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, TEMPLATE.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/proposals":
            doc = _doc()
            self._json(200, {"generated_at": doc.get("generated_at"), "pending": doc.get("pending") or [], "skipped": doc.get("skipped_already_decided_or_clean") or []})
            return
        self._json(404, {"detail": "not found"})

    def do_POST(self):
        path = unquote(self.path.split("?", 1)[0])
        prefix, suffix = "/api/proposals/", "/decide"
        if not (path.startswith(prefix) and path.endswith(suffix)):
            self._json(404, {"detail": "not found"})
            return
        key = path[len(prefix):-len(suffix)]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self._json(400, {"detail": "invalid json"})
            return
        decision = (payload.get("decision") or "").lower()
        if decision not in {"approved", "rejected"}:
            self._json(400, {"detail": "decision must be approved or rejected"})
            return
        doc = _doc()
        prop = next((p for p in doc.get("pending") or [] if p["key"] == key), None)
        if not prop:
            self._json(404, {"detail": "proposal not in pending queue"})
            return
        results = []
        try:
            if decision == "approved" and prop.get("actions"):
                results = apply_actions(CRM(), prop["actions"])
        except Exception as exc:
            self._json(500, {"detail": str(exc)})
            return
        record_decision(prop["key"], decision, prop["fingerprint"], note=payload.get("note") or "")
        doc["pending"] = [p for p in doc["pending"] if p["key"] != key]
        doc.setdefault("resolved", []).append({**prop, "decision": decision, "apply_results": results})
        save_json(config.PROPOSALS_PATH, doc)
        self._json(200, {"ok": True, "decision": decision, "results": results})


def main() -> None:
    host = os.environ.get("REVIEW_HOST", "127.0.0.1")
    port = int(os.environ.get("REVIEW_PORT", "5055"))
    print(f"Review queue → http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
