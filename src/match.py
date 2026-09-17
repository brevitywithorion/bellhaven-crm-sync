from __future__ import annotations

from . import config
from .normalize import (
    house_number, name_core, norm_city, norm_state, norm_street, norm_zip, phones_digits, ratio,
)
from .store import fingerprint

BELLHAVEN_PARENT_ID = "0015QAPLGS3FVYEEEM"
PARENT_ACCOUNT_IDS = {
    "0015QAPLGS3FVYEEEM", "001FJZYHR7MLFMNPLL", "001FWSQ30SFW6S7604",
    "001DAAUWV2J3SHQJ34", "00139TNDS8HNLUZ5A6", "001YRHHXQ5HJ0TCL2U",
}
CONFIDENT = 72.0
POSSIBLE = 48.0


def map_care(offerings: str) -> str:
    key = (offerings or "").strip().lower()
    if key in config.CARE_MAP:
        return config.CARE_MAP[key]
    if "memory" in key and "assisted" not in key:
        return "Memory Care"
    if "nursing" in key or "rehab" in key:
        return "Skilled Nursing"
    if "assisted" in key:
        return "Assisted Living"
    if "independent" in key:
        return "Independent Living"
    return ""


def score_pair(loc: dict, acct: dict) -> dict:
    reasons = []
    score = 0.0
    z_l, z_a = norm_zip(loc.get("zip", "")), norm_zip(acct.get("billing_zip", ""))
    st_l, st_a = norm_street(loc.get("street", "")), norm_street(acct.get("billing_street", ""))
    c_l, c_a = norm_city(loc.get("city", "")), norm_city(acct.get("billing_city", ""))
    s_l, s_a = norm_state(loc.get("state", "")), norm_state(acct.get("billing_state", ""))
    hn_l, hn_a = house_number(loc.get("street", "")), house_number(acct.get("billing_street", ""))
    street_r = ratio(st_l, st_a)
    name_r = ratio((loc.get("name") or "").lower(), (acct.get("name") or "").lower())
    core_r = ratio(name_core(loc.get("name")), name_core(acct.get("name")))
    if z_l and z_l == z_a:
        score += 34; reasons.append(f"zip {z_l}")
    if s_l and s_l == s_a:
        score += 4; reasons.append(f"state {s_l}")
    if c_l and c_l == c_a:
        score += 8; reasons.append(f"city {loc.get('city')}")
    if st_l and st_a:
        score += 40 * street_r
        if street_r >= 0.75:
            reasons.append(f"street similar ({street_r:.2f})")
        if hn_l and hn_l == hn_a:
            score += 6; reasons.append(f"house# {hn_l}")
    score += 14 * max(name_r, core_r)
    if max(name_r, core_r) >= 0.7:
        reasons.append(f"name similar ({max(name_r, core_r):.2f})")
    if name_r >= 0.88 and c_l == c_a and s_l == s_a and z_l == z_a:
        score = max(score, 80); reasons.append("same name + city/state/zip")
    pobox = "pobox" in st_a or "pobox" in st_l
    if pobox and name_r >= 0.8 and c_l == c_a:
        score = max(score, 78); reasons.append("PO Box vs street; name+city agree")
    if hn_l and hn_a and hn_l != hn_a and street_r < 0.78 and not pobox:
        score = min(score, 40); reasons.append(f"blocked: house# {hn_l} vs {hn_a}")
    return {"score": round(score, 2), "street_ratio": round(street_r, 3), "name_ratio": round(name_r, 3), "reasons": reasons}


def needs_chow(acct: dict) -> bool:
    try:
        return float(acct.get("lifetime_revenue") or 0) > 0 and float(acct.get("outstanding_ar") or 0) > 0
    except (TypeError, ValueError):
        return False


def field_updates(loc: dict, acct: dict) -> dict:
    desired = {"name": loc["name"], "billing_street": loc["street"], "billing_city": loc["city"], "billing_state": loc["state"], "billing_zip": loc["zip"]}
    care = map_care(loc.get("care_offerings", ""))
    if care:
        desired["care_type"] = care
    if loc.get("phone"):
        desired["phone"] = loc["phone"]
    patch = {}
    for k, v in desired.items():
        cur, new = (acct.get(k) or "").strip(), (v or "").strip()
        if not new:
            continue
        if k == "phone" and phones_digits(cur) != phones_digits(new):
            patch[k] = new
        elif k == "billing_street" and norm_street(cur) != norm_street(new):
            patch[k] = new
        elif k == "billing_city" and norm_city(cur) != norm_city(new):
            patch[k] = new
        elif k == "billing_state" and norm_state(cur) != norm_state(new):
            patch[k] = new
        elif k == "billing_zip" and norm_zip(cur) != norm_zip(new):
            patch[k] = new
        elif k not in {"phone", "billing_street", "billing_city", "billing_state", "billing_zip"} and cur != new:
            patch[k] = new
    return patch


def pick_survivor(cands: list[dict], parent_id: str) -> dict:
    def key(a: dict):
        return (
            1 if (a.get("status") or "") == "Active" else 0,
            1 if a.get("parent_id") == parent_id else 0,
            1 if "bellhaven" in (a.get("name") or "").lower() else 0,
            float(a.get("lifetime_revenue") or 0),
        )
    return sorted(cands, key=key, reverse=True)[0]


def find_parent(accounts: list[dict]) -> dict:
    for a in accounts:
        if a.get("account_id") == BELLHAVEN_PARENT_ID or (a.get("name") or "") == config.BELLHAVEN_PARENT_NAME_HINT:
            return a
    raise RuntimeError("Bellhaven parent account not found")


def build_proposals(locations: list[dict], accounts: list[dict]) -> list[dict]:
    parent = find_parent(accounts)
    parent_id = parent["account_id"]
    proposals = []
    facility_accts = [
        a for a in accounts
        if a["account_id"] not in PARENT_ACCOUNT_IDS
        and "(Parent Account)" not in (a.get("name") or "")
        and not (a.get("name") or "").startswith("__PROBE")
    ]
    claimed = set()
    for loc in locations:
        ranked = []
        for acct in facility_accts:
            ev = score_pair(loc, acct)
            ranked.append((ev["score"], ev, acct))
        ranked.sort(key=lambda x: x[0], reverse=True)
        confident = [row for row in ranked if row[0] >= CONFIDENT]
        matches = []
        if confident:
            top = confident[0][2]
            top_zip = norm_zip(top.get("billing_zip", "")) or norm_zip(loc.get("zip", ""))
            top_hn = house_number(top.get("billing_street", "")) or house_number(loc.get("street", ""))
            for s, ev, a in confident:
                same = norm_zip(a.get("billing_zip", "")) == top_zip and (
                    not top_hn or house_number(a.get("billing_street", "")) == top_hn or ev["street_ratio"] >= 0.8 or ev["name_ratio"] >= 0.85
                )
                if same:
                    matches.append((s, ev, a))
            if not matches:
                matches = [confident[0]]
        elif ranked and ranked[0][0] >= POSSIBLE:
            matches = [ranked[0]]
        if not matches:
            body = {
                "name": loc["name"], "parent_id": parent_id, "status": "Active",
                "billing_street": loc["street"], "billing_city": loc["city"],
                "billing_state": loc["state"], "billing_zip": loc["zip"],
                "phone": loc.get("phone") or "",
                "note": f"Created from website listing {loc['url']} ({loc.get('care_offerings')}).",
            }
            care = map_care(loc.get("care_offerings", ""))
            if care:
                body["care_type"] = care
            proposals.append(_proposal(f"site:{loc['slug']}", "create", f"Create account for {loc['name']}", "high", loc, [], ["No CRM account scored above the match threshold."], [{"method": "POST", "path": "/accounts", "body": body}], True))
            continue
        live = [(s, ev, a) for s, ev, a in matches if not a.get("duplicate_of_account") and not a.get("chow_current_account")] or matches
        survivor = pick_survivor([a for _, _, a in live], parent_id)
        losers = [a for _, _, a in live if a["account_id"] != survivor["account_id"] and not a.get("duplicate_of_account")]
        top_ev = next(ev for _, ev, a in matches if a["account_id"] == survivor["account_id"])
        for _, _, a in matches:
            claimed.add(a["account_id"])
        actions, evidence = [], [f"Matched {survivor['name']} ({survivor['account_id']}) score={top_ev['score']}: " + "; ".join(top_ev["reasons"])]
        kind, recommended = "match_update", top_ev["score"] >= CONFIDENT
        for dup in losers:
            claimed.add(dup["account_id"])
            evidence.append(f"Duplicate {dup['name']} ({dup['account_id']}) under {dup.get('parent_name') or 'no parent'}")
            actions.append({"method": "PATCH", "path": f"/accounts/{dup['account_id']}", "body": {"status": "Inactive", "duplicate_of_account": survivor["account_id"], "note": f"Duplicate of {survivor['name']} ({survivor['account_id']}) for {loc['name']}."}})
        wrong_parent = (survivor.get("parent_id") or "") != parent_id
        if wrong_parent and needs_chow(survivor):
            kind = "chow"
            new_body = {
                "name": loc["name"], "parent_id": parent_id, "status": "Active",
                "billing_street": loc["street"], "billing_city": loc["city"],
                "billing_state": loc["state"], "billing_zip": loc["zip"],
                "phone": loc.get("phone") or survivor.get("phone") or "",
                "note": f"CHOW successor for {survivor['account_id']}. Old parent kept because rev={survivor.get('lifetime_revenue')} AR={survivor.get('outstanding_ar')}.",
            }
            care = map_care(loc.get("care_offerings", ""))
            if care:
                new_body["care_type"] = care
            actions.append({"method": "POST", "path": "/accounts", "body": new_body, "role": "chow_successor"})
            actions.append({"method": "PATCH", "path": f"/accounts/{survivor['account_id']}", "body": {"chow_current_account": "$successor_id", "note": f"CHOW: successor under Bellhaven for {loc['name']}. Parent unchanged for billing."}, "role": "chow_old"})
            evidence.append("CHOW path: revenue history AND outstanding AR > 0.")
        else:
            patch = field_updates(loc, survivor)
            if wrong_parent:
                patch["parent_id"] = parent_id
                kind = "reparent"
                evidence.append(f"Reparent {survivor.get('parent_name') or '(none)'} → Bellhaven. CHOW not required.")
            if (survivor.get("status") or "") != "Active":
                patch["status"] = "Active"
            if patch:
                if any(k in patch for k in ("name", "parent_id", "billing_street", "billing_zip")):
                    patch["note"] = f"Synced from website {loc['url']}. Updated: {', '.join(sorted(patch))}."
                actions.append({"method": "PATCH", "path": f"/accounts/{survivor['account_id']}", "body": patch})
            elif not losers:
                kind = "already_clean"
                evidence.append("Already aligned with the website; no field changes.")
        if kind == "already_clean" and not losers:
            proposals.append(_proposal(f"site:{loc['slug']}", "already_clean", f"No change: {loc['name']}", "high", loc, [survivor], evidence, [], False, True))
            continue
        title_map = {"create": f"Create {loc['name']}", "chow": f"CHOW {loc['name']}", "reparent": f"Reparent / fix {loc['name']}", "match_update": f"Update {loc['name']}"}
        proposals.append(_proposal(f"site:{loc['slug']}", kind, title_map.get(kind, f"Review {loc['name']}"), "high" if recommended else "medium", loc, [survivor] + losers, evidence, actions, recommended))
    for acct in facility_accts:
        if acct["account_id"] in claimed or acct.get("parent_id") != parent_id or (acct.get("status") or "") != "Active":
            continue
        proposals.append(_proposal(
            f"orphan:{acct['account_id']}", "orphan_inactive", f"Inactivate leftover {acct['name']}", "high", None, [acct],
            [f"{acct['name']} is Active under Bellhaven but missing from the website."],
            [{"method": "PATCH", "path": f"/accounts/{acct['account_id']}", "body": {"status": "Inactive", "note": "No longer listed on the Bellhaven public website."}}],
            True,
        ))
    return proposals


def _proposal(key, kind, title, confidence, loc, accounts, evidence, actions, recommended, skip_review=False):
    return {
        "id": key, "key": key, "kind": kind, "title": title, "confidence": confidence,
        "recommended": recommended, "skip_review": skip_review,
        "fingerprint": fingerprint(actions, extra={"key": key, "kind": kind, "slug": (loc or {}).get("slug")}),
        "website": loc,
        "accounts": [{k: a.get(k) for k in ("account_id", "name", "parent_id", "parent_name", "billing_street", "billing_city", "billing_state", "billing_zip", "care_type", "status", "phone", "lifetime_revenue", "outstanding_ar", "note", "duplicate_of_account", "chow_current_account")} for a in accounts],
        "evidence": evidence,
        "actions": actions,
    }
