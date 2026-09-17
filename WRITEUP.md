# Writeup — Bellhaven website → CRM sync

**Candidate:** Orion Wills
**Repo:** https://github.com/brevitywithorion/bellhaven-crm-sync
**Time:** about 2 hours 15 minutes of focused work

## Matching approach

Scrape every community detail page, plus homepage links. Findlay is advertised on the homepage and omitted from `/communities?page=N`; a directory-only scrape treats it as a closed leftover.

Score every website location against every facility account (parents excluded):

- zip match
- normalized street similarity and house number
- city / state
- name + name-core (drops “Bellhaven”, “Senior”, “Living”, …)

Two guards that stop false merges:

- Disagreeing house numbers at the same zip are not a confident match. *Union Square Senior Living* at 240 Market St is not *Bellhaven at Union Square* at 118 Union Square Dr.
- Same name in a different city is not a match. Hudson, OH *Amberly Manor* is not Colorado Springs *Amberly Manor*.

Proposal kinds: field update, re-parent, CHOW, create, duplicate (`duplicate_of_account` + Inactive), leftover Inactive. Website care offerings collapse onto the CRM enum (`Short-Term Rehabilitation & Nursing` → Skilled Nursing, `Memory Support` → Memory Care).

## CHOW

Checked twice.

**At match time:** if the matched row must change parent and `lifetime_revenue > 0` AND `outstanding_ar > 0`, emit create-under-Bellhaven + `chow_current_account` on the old row. Do not change old `parent_id`. If either number is zero, re-parent the existing row.

**At approve time:** re-fetch the live account before writing.

1. If `chow_current_account` is already set → refuse a second CHOW.
2. If revenue and AR no longer both exceed zero → fall back to in-place re-parent.
3. Otherwise create the successor and point the old row at it.

Marietta (`001A34WFSUYHCRBLFT` → `00190EC211DECFC16D`) and Tiffin (`001U6RW32TY0WSXZZB` → `001C38203B5A6A57E6`) stay on Cedar Trail with billing intact. Lima and Findlay had revenue but AR = 0, so they moved in place.

Sandusky is a leftover with revenue and AR. CHOW applies to parent moves, not closings, so it is Inactive on the same account id.

## Mistakes I caught before they landed wrong

- **Findlay is not closed.** It is on the homepage and missing from the paginated directory. A directory-only scrape proposes inactivating an operating community. The scraper follows homepage links.
- **Union Square is two buildings.** 118 Union Square Dr (Bellhaven) is not 240 Market St (Union Square Senior Living), same city and zip. House numbers that disagree cannot be a confident match.
- **Amberly Manor is two buildings.** Hudson, OH is Bellhaven. Colorado Springs is Juniper Point. Same name is not a match.
- **A retry created extra rows** (second Batavia, Union Square, Carlisle, Amberly, extra CHOW successors). Losers are Inactive with `duplicate_of_account`. Left visible on purpose — no delete API.
- **`__PROBE_DELETE_ME__` and `TEST DO NOT KEEP`** are schema-discovery leftovers. Inactive, noted “sandbox probe — ignore.”

## How I used AI

An AI coding agent inspected the site and OpenAPI, drafted the scraper / matcher / review app, and applied approved writes through the same `apply_actions` path the app uses. Every CHOW, collision, and leftover was checked against address, parent, revenue, and AR before write. The daily job does not call an LLM.

## What I would build next

1. More fixture tests for every collision we found (the file in `tests/` is the start).
2. A nightly email of the pending queue.
3. Administrator / phone sync onto CRM contacts.
