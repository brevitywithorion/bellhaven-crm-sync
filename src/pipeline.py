from __future__ import annotations

import argparse
from datetime import datetime, timezone

from . import config
from .crm import CRM
from .match import build_proposals
from .scraper import scrape
from .store import load_json, save_json, should_skip


def snapshot_accounts(crm: CRM) -> list[dict]:
    rows = crm.list_accounts()
    save_json(config.ACCOUNTS_PATH, rows)
    return rows


def run_match(locations=None, accounts=None) -> list[dict]:
    locations = locations or load_json(config.WEBSITE_PATH, [])
    accounts = accounts or load_json(config.ACCOUNTS_PATH, [])
    if not locations:
        raise SystemExit("No website locations. Run: python -m src.pipeline scrape")
    if not accounts:
        raise SystemExit("No CRM snapshot. Run: python -m src.pipeline snapshot")
    raw = build_proposals(locations, accounts)
    pending, skipped = [], []
    for p in raw:
        if p.get("skip_review") and not p["actions"]:
            skipped.append({**p, "decision": "noop"})
            continue
        skip, prior = should_skip(p["key"], p["fingerprint"])
        if skip:
            skipped.append({**p, "decision": prior})
            continue
        pending.append(p)
    save_json(
        config.PROPOSALS_PATH,
        {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "website_count": len(locations),
            "account_count": len(accounts),
            "pending": pending,
            "skipped_already_decided_or_clean": skipped,
        },
    )
    return pending


def apply_actions(crm: CRM, actions: list[dict]) -> list[dict]:
    results = []
    successor_id = None
    for action in actions:
        method = action["method"].upper()
        body = dict(action.get("body") or {})
        for k, v in list(body.items()):
            if v == "$successor_id":
                if not successor_id:
                    raise RuntimeError("CHOW successor id missing; create must run first")
                body[k] = successor_id
        if method == "POST" and action["path"] == "/accounts":
            resp = crm.create_account(body)
            successor_id = resp.get("account_id")
            results.append({"action": action, "response": resp})
        elif method == "PATCH":
            account_id = action["path"].rstrip("/").split("/")[-1]
            resp = crm.patch_account(account_id, body)
            results.append({"action": action, "response": resp})
        else:
            raise RuntimeError(f"Unsupported action {action}")
    return results


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Bellhaven website → CRM pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scrape")
    sub.add_parser("snapshot")
    sub.add_parser("match")
    p_run = sub.add_parser("run")
    p_run.add_argument("--skip-scrape", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "scrape":
        rows = scrape()
        print(f"scraped {len(rows)} locations → {config.WEBSITE_PATH}")
        return
    if args.cmd == "snapshot":
        rows = snapshot_accounts(CRM())
        print(f"snapshot {len(rows)} accounts → {config.ACCOUNTS_PATH}")
        return
    if args.cmd == "match":
        pending = run_match()
        print(f"{len(pending)} proposals need review → {config.PROPOSALS_PATH}")
        for p in pending:
            print(f"  [{p['kind']:16}] {p['confidence']:6} {p['title']}")
        return
    if args.cmd == "run":
        if not args.skip_scrape:
            print(f"scraped {len(scrape())} locations")
        crm = CRM()
        print("token:", crm.me())
        print(f"snapshot {len(snapshot_accounts(crm))} accounts")
        pending = run_match()
        print(f"{len(pending)} proposals need review")
        for p in pending:
            print(f"  [{p['kind']:16}] {p['confidence']:6} {p['title']}")


if __name__ == "__main__":
    main()
