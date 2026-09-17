from __future__ import annotations

import argparse
from datetime import datetime, timezone

from . import config
from .crm import CRM
from .match import build_proposals, needs_chow
from .normalize import norm_zip
from .scraper import scrape
from .store import load_json, save_json, should_skip


def snapshot_accounts(crm: CRM) -> list[dict]:
    rows = crm.list_accounts()
    save_json(config.ACCOUNTS_PATH, rows)
    return rows


def run_match(locations: list[dict] | None = None, accounts: list[dict] | None = None) -> list[dict]:
    locations = locations or load_json(config.WEBSITE_PATH, [])
    accounts = accounts or load_json(config.ACCOUNTS_PATH, [])
    if not locations:
        raise SystemExit("No website locations. Run: python -m src.pipeline scrape")
    if not accounts:
        raise SystemExit("No CRM snapshot. Run: python -m src.pipeline snapshot")

    raw = build_proposals(locations, accounts)
    pending = []
    skipped = []
    for p in raw:
        if p.get("skip_review") and not p["actions"]:
            skipped.append({**p, "decision": "noop"})
            continue
        skip, prior = should_skip(p["key"], p["fingerprint"])
        if skip:
            skipped.append({**p, "decision": prior})
            continue
        pending.append(p)

    doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "website_count": len(locations),
        "account_count": len(accounts),
        "pending": pending,
        "skipped_already_decided_or_clean": skipped,
    }
    save_json(config.PROPOSALS_PATH, doc)
    return pending


def resolve_chow_actions(crm: CRM, actions: list[dict]) -> list[dict]:
    """Re-check the live row before a CHOW write.

    Match-time proposals can go stale. On approve we:
      1. refuse a second CHOW if chow_current_account is already set
      2. fall back to in-place re-parent if revenue AND AR are no longer both > 0
      3. otherwise create the successor and point the old row at it
    """
    out: list[dict] = []
    i = 0
    while i < len(actions):
        action = actions[i]
        nxt = actions[i + 1] if i + 1 < len(actions) else None
        chow_pair = (
            nxt is not None
            and action.get("method", "").upper() == "POST"
            and action.get("path") == "/accounts"
            and (
                action.get("role") == "chow_successor"
                or "chow_current_account" in (nxt.get("body") or {})
            )
        )
        if not chow_pair:
            out.append(action)
            i += 1
            continue

        old_id = nxt["path"].rstrip("/").split("/")[-1]
        live = crm.get_account(old_id)
        existing = live.get("chow_current_account") or ""
        if existing:
            out.append(
                {
                    "method": "SKIP",
                    "path": f"/accounts/{old_id}",
                    "role": "chow_refused_second",
                    "body": {},
                    "reason": (
                        f"{old_id} already has chow_current_account={existing}; "
                        "refusing a second CHOW."
                    ),
                }
            )
            i += 2
            continue

        create_body = dict(action.get("body") or {})
        if not needs_chow(live):
            patch = {"parent_id": create_body.get("parent_id")}
            for k in (
                "name",
                "billing_street",
                "billing_city",
                "billing_state",
                "billing_zip",
                "care_type",
                "phone",
            ):
                if create_body.get(k):
                    patch[k] = create_body[k]
            patch["note"] = (
                "Re-parented in place at approve time. Live row no longer trips CHOW "
                f"(rev={live.get('lifetime_revenue')} ar={live.get('outstanding_ar')})."
            )
            out.append(
                {
                    "method": "PATCH",
                    "path": f"/accounts/{old_id}",
                    "body": patch,
                    "role": "chow_fallback_reparent",
                }
            )
            i += 2
            continue

        out.append(action)
        out.append(nxt)
        i += 2
    return out


def _active_twin(crm: CRM, body: dict):
    """Avoid a second POST when name+zip+parent already exist as Active."""
    want_name = (body.get("name") or "").strip().lower()
    want_zip = norm_zip(body.get("billing_zip") or "")
    want_parent = body.get("parent_id") or ""
    if not want_name:
        return None
    for a in crm.list_accounts():
        if (a.get("status") or "") != "Active":
            continue
        if want_parent and (a.get("parent_id") or "") != want_parent:
            continue
        if (a.get("name") or "").strip().lower() != want_name:
            continue
        if want_zip and norm_zip(a.get("billing_zip") or "") != want_zip:
            continue
        return a
    return None


def apply_actions(crm: CRM, actions: list[dict], *, dry_run: bool = False) -> list[dict]:
    """Execute approved actions. CHOW is re-checked against the live account.

    dry_run=True still reads the live row for the CHOW lock, but does not POST/PATCH.
    """
    results = []
    successor_id = None
    planned = resolve_chow_actions(crm, actions)
    for action in planned:
        method = action["method"].upper()
        if method == "SKIP":
            results.append({"action": action, "response": {"skipped": True, "reason": action.get("reason")}, "dry_run": dry_run})
            continue
        body = dict(action.get("body") or {})
        for k, v in list(body.items()):
            if v == "$successor_id":
                if dry_run:
                    body[k] = "$successor_id"
                elif not successor_id:
                    raise RuntimeError("CHOW successor id missing; create must run first")
                else:
                    body[k] = successor_id
        if dry_run:
            results.append({"action": action, "planned_body": body, "dry_run": True})
            continue
        if method == "POST" and action["path"] == "/accounts":
            twin = _active_twin(crm, body)
            if twin:
                successor_id = twin["account_id"]
                results.append(
                    {
                        "action": action,
                        "response": {
                            "skipped": True,
                            "reason": f"Active twin already exists {twin['account_id']}",
                            "account_id": twin["account_id"],
                        },
                    }
                )
                continue
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bellhaven website → CRM pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scrape", help="Pull every community page")
    sub.add_parser("snapshot", help="Download all CRM accounts")
    sub.add_parser("match", help="Build review proposals from local snapshots")
    p_run = sub.add_parser("run", help="Scrape + snapshot + match (no writes)")
    p_run.add_argument("--skip-scrape", action="store_true")
    p_apply = sub.add_parser("apply", help="Apply pending proposals (default: dry-run)")
    p_apply.add_argument("--write", action="store_true", help="Actually POST/PATCH. Off = dry-run.")
    p_apply.add_argument("--key", help="Only this proposal key")
    p_apply.add_argument("--all", action="store_true", help="Required with --write to apply every pending item")
    args = parser.parse_args(argv)

    if args.cmd == "scrape":
        rows = scrape()
        print(f"scraped {len(rows)} locations → {config.WEBSITE_PATH}")
        return
    if args.cmd == "snapshot":
        crm = CRM()
        rows = snapshot_accounts(crm)
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
            rows = scrape()
            print(f"scraped {len(rows)} locations")
        crm = CRM()
        print("token:", crm.me())
        accs = snapshot_accounts(crm)
        print(f"snapshot {len(accs)} accounts")
        pending = run_match()
        print(f"{len(pending)} proposals need review")
        for p in pending:
            print(f"  [{p['kind']:16}] {p['confidence']:6} {p['title']}")
        return
    if args.cmd == "apply":
        doc = load_json(config.PROPOSALS_PATH, {})
        pending = list(doc.get("pending") or [])
        if args.key:
            pending = [p for p in pending if p.get("key") == args.key]
            if not pending:
                raise SystemExit(f"No pending proposal with key={args.key}")
        if args.write and not args.key and not args.all:
            raise SystemExit("Refusing --write on the whole queue. Pass --key KEY or --all.")
        dry = not args.write
        crm = CRM()
        print(f"{'DRY RUN' if dry else 'WRITE'} {len(pending)} proposal(s)")
        for p in pending:
            print(f"  [{p['kind']}] {p['title']}")
            for row in apply_actions(crm, p.get("actions") or [], dry_run=dry):
                print("   ", row.get("dry_run") and "planned" or "wrote", row.get("planned_body") or row.get("response"))


if __name__ == "__main__":
    main()
