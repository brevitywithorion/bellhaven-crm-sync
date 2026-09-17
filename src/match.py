from __future__ import annotations

from . import config
from .normalize import (
    house_number,
    name_core,
    norm_city,
    norm_state,
    norm_street,
    norm_zip,
    phones_digits,
    ratio,
)
from .store import fingerprint

# Known for this sandbox only — find_parent prefers the name, then this id.
BELLHAVEN_PARENT_ID = "0015QAPLGS3FVYEEEM"

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
    reasons: list[str] = []
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
        score += 34
        reasons.append(f"zip {z_l}")
    if s_l and s_l == s_a:
        score += 4
        reasons.append(f"state {s_l}")
    if c_l and c_l == c_a:
        score += 8
        reasons.append(f"city {loc.get('city')}")
    if st_l and st_a:
        score += 40 * street_r
        if street_r >= 0.92:
            reasons.append("street nearly identical")
        elif street_r >= 0.75:
            reasons.append(f"street similar ({street_r:.2f})")
        if hn_l and hn_l == hn_a:
            score += 6
            reasons.append(f"house# {hn_l}")
    score += 14 * max(name_r, core_r)
    if max(name_r, core_r) >= 0.9:
        reasons.append("name nearly identical")
    elif max(name_r, core_r) >= 0.7:
        reasons.append(f"name similar ({max(name_r, core_r):.2f})")
    if name_r >= 0.88 and c_l == c_a and s_l == s_a and z_l == z_a:
        score = max(score, 80)
        reasons.append("same name + city/state/zip (address format may differ)")
    pobox = "pobox" in st_a or "pobox" in st_l
    if pobox and name_r >= 0.8 and c_l == c_a:
        score = max(score, 78)
        reasons.append("PO Box vs street; name+city agree")
    if hn_l and hn_a and hn_l != hn_a and street_r < 0.78 and not pobox:
        score = min(score, 40)
        reasons.append(f"blocked: house# {hn_l} vs {hn_a}")
    return {"score": round(score, 2), "street_ratio": round(street_r, 3), "name_ratio": round(name_r, 3), "reasons": reasons}


def needs_chow(acct: dict) -> bool:
    try:
        rev = float(acct.get("lifetime_revenue") or 0)
        ar = float(acct.get("outstanding_ar") or 0)
    except (TypeError, ValueError):
        return False
    return rev > 0 and ar > 0


def field_updates(loc: dict, acct: dict) -> dict:
    desired = {
        "name": loc["name"],
        "billing_street": loc["street"],
        "billing_city": loc["city"],
        "billing_state": loc["state"],
        "billing_zip": loc["zip"],
    }
    care = map_care(loc.get("care_offerings", ""))
    if care:
        desired["care_type"] = care
    if loc.get("phone"):
        desired["phone"] = loc["phone"]
    patch = {}
    for k, v in desired.items():
        cur = (acct.get(k) or "").strip()
        new = (v or "").strip()
        if not new:
            continue
        if k == "phone":
            if phones_digits(cur) != phones_digits(new) and phones_digits(new):
                patch[k] = new
            continue
        if k == "billing_street":
            if norm_street(cur) != norm_street(new):
                patch[k] = new
            continue
        if k == "billing_city":
            if norm_city(cur) != norm_city(new):
                patch[k] = new
            continue
        if k == "billing_state":
            if norm_state(cur) != norm_state(new):
                patch[k] = new
            continue
        if k == "billing_zip":
            if norm_zip(cur) != norm_zip(new):
                patch[k] = new
            continue
        if cur != new:
            patch[k] = new
    return patch


def pick_survivor(cands: list[dict], parent_id: str) -> dict:
    def key(a: dict):
        under = 1 if a.get("parent_id") == parent_id else 0
        active = 1 if (a.get("status") or "") == "Active" else 0
        rev = float(a.get("lifetime_revenue") or 0)
        named = 1 if "bellhaven" in (a.get("name") or "").lower() else 0
        complete = sum(1 for k in ("billing_street", "billing_city", "phone") if a.get(k))
        return (active, under, named, rev, complete)
    return sorted(cands, key=key, reverse=True)[0]


def is_corporate_parent(acct: dict) -> bool:
    name = acct.get("name") or ""
    if "(Parent Account)" in name:
        return True
    if not acct.get("parent_id") and "parent" in name.lower():
        return True
    return False


def resolve_operational(acct: dict, by_id: dict,):
    notes: list[str] = []
    seen: set[str] = set()
    cur = acct
    while cur and cur.get("account_id") not in seen:
        seen.add(cur["account_id"])
        dup = cur.get("duplicate_of_account") or ""
        if dup and dup in by_id:
            nxt = by_id[dup]
            notes.append(f"followed duplicate_of_account → {nxt['account_id']}")
            cur = nxt
            continue
        chow = cur.get("chow_current_account") or ""
        if chow and chow in by_id:
            nxt = by_id[chow]
            notes.append(f"followed chow_current_account → {nxt['account_id']}")
            cur = nxt
            continue
        break
    return cur, notes


def find_parent(accounts: list[dict]) -> dict:
    hint = (config.BELLHAVEN_PARENT_NAME_HINT or "").strip()
    for a in accounts:
        if hint and (a.get("name") or "").strip() == hint:
            return a
    for a in accounts:
        name = (a.get("name") or "").lower()
        if "bellhaven" in name and "parent" in name and not a.get("parent_id"):
            return a
    for a in accounts:
        if a.get("account_id") == BELLHAVEN_PARENT_ID:
            return a
    raise RuntimeError("Bellhaven parent account not found")


def build_proposals(locations: list[dict], accounts: list[dict]) -> list[dict]:
    parent = find_parent(accounts)
    parent_id = parent["account_id"]
    proposals: list[dict] = []
    facility_accts = [
        a for a in accounts
        if not is_corporate_parent(a)
        and not (a.get("name") or "").startswith("__")
        and "TEST DO NOT KEEP" not in (a.get("name") or "").upper()
    ]
    claimed: set[str] = set()
    by_id = {a["account_id"]: a for a in accounts}
    for loc in locations:
        ranked = []
        for acct in facility_accts:
            ev = score_pair(loc, acct)
            ranked.append((ev["score"], ev, acct))
        ranked.sort(key=lambda x: x[0], reverse=True)
        confident = [(s, ev, a) for s, ev, a in ranked if s >= CONFIDENT]
        matches = []
        if confident:
            top = confident[0][2]
            top_zip = norm_zip(top.get("billing_zip", "")) or norm_zip(loc.get("zip", ""))
            top_hn = house_number(top.get("billing_street", "")) or house_number(loc.get("street", ""))
            for s, ev, a in confident:
                same_place = norm_zip(a.get("billing_zip", "")) == top_zip and (
                    not top_hn
                    or house_number(a.get("billing_street", "")) == top_hn
                    or ev["street_ratio"] >= 0.8
                    or ev["name_ratio"] >= 0.85
                )
                if same_place:
                    matches.append((s, ev, a))
            if not matches:
                matches = [confident[0]]
        elif ranked and ranked[0][0] >= POSSIBLE:
            matches = [ranked[0]]
        if matches and (not confident or matches[0][0] < CONFIDENT):
            s, ev, a = matches[0]
            resolved, ptr_notes = resolve_operational(a, by_id)
            claimed.add(a["account_id"])
            claimed.add(resolved["account_id"])
            proposals.append(_proposal(
                key=f"site:{loc['slug']}", kind="needs_review",
                title=f"Needs review: {loc['name']}", confidence="medium", loc=loc,
                accounts=[resolved],
                evidence=[f"Best score {s} is below confident ({CONFIDENT}). No write.", "; ".join(ev["reasons"]) or "weak match", *ptr_notes],
                actions=[], recommended=False,
            ))
            continue
        if not matches:
            body = {
                "name": loc["name"], "parent_id": parent_id, "status": "Active",
                "billing_street": loc["street"], "billing_city": loc["city"],
                "billing_state": loc["state"], "billing_zip": loc["zip"],
                "phone": loc.get("phone") or "",
                "note": f"Created from website listing {loc['url']} ({loc.get('care_offerings')}). No CRM account matched.",
            }
            care = map_care(loc.get("care_offerings", ""))
            if care:
                body["care_type"] = care
            proposals.append(_proposal(
                key=f"site:{loc['slug']}", kind="create", title=f"Create account for {loc['name']}",
                confidence="high", loc=loc, accounts=[],
                evidence=["No CRM account scored above the match threshold."],
                actions=[{"method": "POST", "path": "/accounts", "body": body}], recommended=True,
            ))
            continue
        resolved_rows = []
        pointer_notes: list[str] = []
        for s, ev, a in matches:
            op, notes = resolve_operational(a, by_id)
            pointer_notes.extend(notes)
            resolved_rows.append((s, ev, op, a))
        live = [(s, ev, op) for s, ev, op, _orig in resolved_rows]
        survivor = pick_survivor([a for _, _, a in live], parent_id)
        losers = [a for _, _, a in live if a["account_id"] != survivor["account_id"] and not a.get("duplicate_of_account")]
        top_ev = next(ev for _, ev, op in live if op["account_id"] == survivor["account_id"])
        for _, _, op, orig in resolved_rows:
            claimed.add(orig["account_id"])
            claimed.add(op["account_id"])
        actions: list[dict] = []
        evidence = [f"Matched {survivor['name']} ({survivor['account_id']}) score={top_ev['score']}: " + "; ".join(top_ev["reasons"])] + pointer_notes
        kind = "match_update"
        recommended = top_ev["score"] >= CONFIDENT
        confidence = "high" if recommended else "medium"
        for dup in losers:
            claimed.add(dup["account_id"])
            evidence.append(f"Duplicate {dup['name']} ({dup['account_id']}) under {dup.get('parent_name') or 'no parent'}; rev={dup.get('lifetime_revenue')} ar={dup.get('outstanding_ar')}")
            actions.append({"method": "PATCH", "path": f"/accounts/{dup['account_id']}", "body": {"status": "Inactive", "duplicate_of_account": survivor["account_id"], "note": f"Duplicate of {survivor['name']} ({survivor['account_id']}) for website location {loc['name']} ({loc['url']})."}})
        wrong_parent = (survivor.get("parent_id") or "") != parent_id
        if wrong_parent and needs_chow(survivor):
            kind = "chow"
            new_body = {
                "name": loc["name"], "parent_id": parent_id, "status": "Active",
                "billing_street": loc["street"], "billing_city": loc["city"],
                "billing_state": loc["state"], "billing_zip": loc["zip"],
                "phone": loc.get("phone") or survivor.get("phone") or "",
                "note": f"CHOW successor for {survivor['account_id']} ({survivor['name']}). Old account kept on parent {survivor.get('parent_name')} because lifetime_revenue={survivor.get('lifetime_revenue')} and outstanding_ar={survivor.get('outstanding_ar')}.",
            }
            care = map_care(loc.get("care_offerings", ""))
            if care:
                new_body["care_type"] = care
            actions.append({"method": "POST", "path": "/accounts", "body": new_body, "role": "chow_successor"})
            actions.append({"method": "PATCH", "path": f"/accounts/{survivor['account_id']}", "body": {"chow_current_account": "$successor_id", "note": f"CHOW: successor created under Bellhaven for website location {loc['name']}. Parent left unchanged for billing (rev={survivor.get('lifetime_revenue')} AR={survivor.get('outstanding_ar')})."}, "role": "chow_old", "note_only_plus_chow": True})
            evidence.append("CHOW path: revenue history AND outstanding AR > 0, so parent is not moved.")
        else:
            patch = field_updates(loc, survivor)
            if wrong_parent:
                patch["parent_id"] = parent_id
                kind = "reparent"
                evidence.append(f"Reparent {survivor.get('parent_name') or '(none)'} → Bellhaven. rev={survivor.get('lifetime_revenue')} ar={survivor.get('outstanding_ar')} (CHOW not required).")
            if (survivor.get("status") or "") != "Active":
                patch["status"] = "Active"
            if patch:
                interesting = [k for k in patch if k in {"name", "parent_id", "billing_street", "billing_zip"}]
                if interesting:
                    patch["note"] = f"Synced from website {loc['url']}. Updated: {', '.join(sorted(patch))}."
                actions.append({"method": "PATCH", "path": f"/accounts/{survivor['account_id']}", "body": patch})
            elif not losers:
                kind = "already_clean"
                evidence.append("Already aligned with the website; no field changes.")
        if kind == "already_clean" and not losers:
            proposals.append(_proposal(key=f"site:{loc['slug']}", kind="already_clean", title=f"No change: {loc['name']}", confidence="high", loc=loc, accounts=[survivor], evidence=evidence, actions=[], recommended=False, skip_review=True))
            continue
        title_map = {"create": f"Create {loc['name']}", "chow": f"CHOW {loc['name']}", "reparent": f"Reparent / fix {loc['name']}", "match_update": f"Update {loc['name']}"}
        proposals.append(_proposal(key=f"site:{loc['slug']}", kind=kind, title=title_map.get(kind, f"Review {loc['name']}"), confidence=confidence, loc=loc, accounts=[survivor] + losers, evidence=evidence, actions=actions, recommended=recommended))
    for acct in facility_accts:
        if acct["account_id"] in claimed or acct.get("parent_id") != parent_id or (acct.get("status") or "") != "Active":
            continue
        proposals.append(_proposal(
            key=f"orphan:{acct['account_id']}", kind="orphan_inactive", title=f"Inactivate leftover {acct['name']}",
            confidence="high", loc=None, accounts=[acct],
            evidence=[f"{acct['name']} is Active under Bellhaven parent but does not match any current website community (addr {acct.get('billing_street')}, {acct.get('billing_city')} {acct.get('billing_state')} {acct.get('billing_zip')})."],
            actions=[{"method": "PATCH", "path": f"/accounts/{acct['account_id']}", "body": {"status": "Inactive", "note": "No longer listed on the Bellhaven public website. Left under Bellhaven parent; marked Inactive rather than deleted."}}],
            recommended=True,
        ))
    return proposals


def _proposal(*, key, kind, title, confidence, loc, accounts, evidence, actions, recommended, skip_review=False):
    fp = fingerprint(actions, extra={"key": key, "kind": kind, "slug": (loc or {}).get("slug")})
    return {
        "id": key, "key": key, "kind": kind, "title": title, "confidence": confidence,
        "recommended": recommended, "skip_review": skip_review, "fingerprint": fp, "website": loc,
        "accounts": [{
            "account_id": a.get("account_id"), "name": a.get("name"), "parent_id": a.get("parent_id"),
            "parent_name": a.get("parent_name"), "billing_street": a.get("billing_street"),
            "billing_city": a.get("billing_city"), "billing_state": a.get("billing_state"),
            "billing_zip": a.get("billing_zip"), "care_type": a.get("care_type"), "status": a.get("status"),
            "phone": a.get("phone"), "lifetime_revenue": a.get("lifetime_revenue"),
            "outstanding_ar": a.get("outstanding_ar"), "note": a.get("note"),
            "duplicate_of_account": a.get("duplicate_of_account"), "chow_current_account": a.get("chow_current_account"),
        } for a in accounts],
        "evidence": evidence, "actions": actions,
    }
