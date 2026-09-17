"""Matcher invariants — the cases that would silently break billing or the sales picture."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.match import CONFIDENT, build_proposals, map_care, needs_chow, score_pair
from src.pipeline import apply_actions, resolve_chow_actions


PARENT = {
    "account_id": "0015QAPLGS3FVYEEEM",
    "name": "Bellhaven Senior Living (Parent Account)",
    "parent_id": "",
    "status": "Active",
}


def loc(**kw):
    base = {
        "name": "Bellhaven of Marietta",
        "street": "400 Matthew Terrace",
        "city": "Marietta",
        "state": "OH",
        "zip": "45750",
        "care_offerings": "Short-Term Rehabilitation & Nursing",
        "phone": "(740) 555-0100",
        "slug": "bellhaven-of-marietta",
        "url": "https://example.test/communities/bellhaven-of-marietta",
    }
    base.update(kw)
    return base


def acct(**kw):
    base = {
        "account_id": "OLD1",
        "name": "Bellhaven of Marietta",
        "parent_id": "001FWSQ30SFW6S7604",
        "parent_name": "Cedar Trail Communities (Parent Account)",
        "billing_street": "400 Matthew Terrace",
        "billing_city": "Marietta",
        "billing_state": "OH",
        "billing_zip": "45750",
        "care_type": "Assisted Living",
        "status": "Active",
        "phone": "",
        "lifetime_revenue": 0,
        "outstanding_ar": 0,
        "chow_current_account": "",
        "duplicate_of_account": "",
        "note": "",
    }
    base.update(kw)
    return base


class CareAndChow(unittest.TestCase):
    def test_rehab_maps_to_skilled_nursing(self):
        self.assertEqual(map_care("Short-Term Rehabilitation & Nursing"), "Skilled Nursing")

    def test_memory_support_maps(self):
        self.assertEqual(map_care("Memory Support"), "Memory Care")

    def test_chow_only_when_revenue_and_ar(self):
        self.assertTrue(needs_chow(acct(lifetime_revenue=51250, outstanding_ar=3800)))
        self.assertFalse(needs_chow(acct(lifetime_revenue=22000, outstanding_ar=0)))
        self.assertFalse(needs_chow(acct(lifetime_revenue=0, outstanding_ar=400)))


class ScoringGuards(unittest.TestCase):
    def test_same_building_is_confident(self):
        ev = score_pair(loc(), acct())
        self.assertGreaterEqual(ev["score"], CONFIDENT)

    def test_same_name_different_city_is_not_a_match(self):
        ev = score_pair(
            loc(name="Amberly Manor", street="5774 Darrow Rd", city="Hudson", state="OH", zip="44236", slug="amberly"),
            acct(
                name="Amberly Manor",
                billing_street="2120 N Circle Dr",
                billing_city="Colorado Springs",
                billing_state="CO",
                billing_zip="80909",
            ),
        )
        self.assertLess(ev["score"], CONFIDENT)

    def test_union_square_house_number_blocks_wrong_street(self):
        ev = score_pair(
            loc(
                name="Bellhaven at Union Square",
                street="118 Union Square Dr",
                city="New Albany",
                state="OH",
                zip="43054",
                slug="union-square",
            ),
            acct(
                name="Union Square Senior Living",
                billing_street="240 Market St",
                billing_city="New Albany",
                billing_state="OH",
                billing_zip="43054",
            ),
        )
        self.assertLess(ev["score"], CONFIDENT)


class ProposalShapes(unittest.TestCase):
    def test_chow_creates_successor_and_does_not_move_parent(self):
        old = acct(lifetime_revenue=51250, outstanding_ar=3800)
        props = build_proposals([loc()], [PARENT, old])
        chow = next(p for p in props if p["kind"] == "chow")
        methods = [(a["method"], a["path"], a["body"]) for a in chow["actions"]]
        self.assertTrue(any(m == "POST" and p == "/accounts" for m, p, _ in methods))
        patch = next(b for m, p, b in methods if m == "PATCH")
        self.assertEqual(patch.get("chow_current_account"), "$successor_id")
        self.assertNotIn("parent_id", patch)

    def test_zero_ar_reparents_in_place(self):
        old = acct(lifetime_revenue=18400, outstanding_ar=0, parent_id="001FJZYHR7MLFMNPLL")
        props = build_proposals([loc()], [PARENT, old])
        reparent = next(p for p in props if p["kind"] == "reparent")
        body = reparent["actions"][0]["body"]
        self.assertEqual(body["parent_id"], PARENT["account_id"])
        self.assertTrue(all(a["method"] != "POST" for a in reparent["actions"]))

    def test_missing_site_creates_account(self):
        props = build_proposals([loc()], [PARENT])
        create = next(p for p in props if p["kind"] == "create")
        self.assertEqual(create["actions"][0]["method"], "POST")
        self.assertEqual(create["actions"][0]["body"]["parent_id"], PARENT["account_id"])


def _chow_pair(old_id="OLD1"):
    return [
        {
            "method": "POST",
            "path": "/accounts",
            "role": "chow_successor",
            "body": {"name": "Bellhaven of Marietta", "parent_id": PARENT["account_id"], "status": "Active"},
        },
        {
            "method": "PATCH",
            "path": f"/accounts/{old_id}",
            "role": "chow_old",
            "body": {"chow_current_account": "$successor_id"},
        },
    ]


class WriteTimeChow(unittest.TestCase):
    def test_refuses_second_chow_on_live_row(self):
        crm = MagicMock()
        crm.get_account.return_value = {
            "account_id": "OLD1",
            "lifetime_revenue": 51250,
            "outstanding_ar": 3800,
            "chow_current_account": "ALREADY",
        }
        planned = resolve_chow_actions(crm, _chow_pair())
        self.assertEqual(planned[0]["role"], "chow_refused_second")
        apply_actions(crm, _chow_pair())
        crm.create_account.assert_not_called()
        crm.patch_account.assert_not_called()

    def test_falls_back_to_reparent_when_ar_cleared(self):
        crm = MagicMock()
        crm.get_account.return_value = {
            "account_id": "OLD1",
            "lifetime_revenue": 51250,
            "outstanding_ar": 0,
            "chow_current_account": "",
        }
        crm.patch_account.return_value = {"message": "updated"}
        results = apply_actions(crm, _chow_pair())
        crm.create_account.assert_not_called()
        crm.patch_account.assert_called_once()
        account_id, body = crm.patch_account.call_args[0]
        self.assertEqual(account_id, "OLD1")
        self.assertEqual(body["parent_id"], PARENT["account_id"])
        self.assertTrue(results[0]["action"]["role"] == "chow_fallback_reparent")


if __name__ == "__main__":
    unittest.main()
