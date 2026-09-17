from __future__ import annotations

import time
from typing import Any

import requests

from . import config


class CRM:
    def __init__(self, token: str | None = None, base: str | None = None):
        self.base = (base or config.CRM_BASE).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token or config.crm_token()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _req(self, method: str, path: str, **kwargs) -> Any:
        url = path if path.startswith("http") else f"{self.base}{path}"
        last_err: Exception | None = None
        for attempt in range(5):
            r = self.session.request(method, url, timeout=30, **kwargs)
            if r.status_code in (429, 500, 502, 503, 504) or not r.content:
                last_err = RuntimeError(
                    f"{method} {url} -> {r.status_code} empty={not r.content} {r.text[:200]}"
                )
                time.sleep(0.7 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {url} -> {r.status_code} {r.text}")
            return r.json()
        raise last_err or RuntimeError(f"{method} {url} failed")

    def me(self) -> dict:
        return self._req("GET", "/me")

    def list_accounts(self) -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            payload = self._req("GET", f"/accounts?page={page}&page_size=50")
            chunk = payload.get("data") or []
            rows.extend(chunk)
            total = payload.get("total", len(rows))
            if not chunk or len(rows) >= total:
                break
            page += 1
        return rows

    def get_account(self, account_id: str) -> dict:
        return self._req("GET", f"/accounts/{account_id}")

    def create_account(self, body: dict) -> dict:
        return self._req("POST", "/accounts", json=body)

    def patch_account(self, account_id: str, body: dict) -> dict:
        return self._req("PATCH", f"/accounts/{account_id}", json=body)

    def list_contacts(self) -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            payload = self._req("GET", f"/contacts?page={page}&page_size=50")
            chunk = payload.get("data") or []
            rows.extend(chunk)
            total = payload.get("total", len(rows))
            if not chunk or len(rows) >= total:
                break
            page += 1
        return rows
