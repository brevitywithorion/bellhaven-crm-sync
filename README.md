# Bellhaven website → CRM sync

Daily pipeline that scrapes Bellhaven communities, matches them to the CRM sandbox, and queues every write for human review. **Nothing is written to the CRM until a reviewer approves it.**

Public repo for the Clipboard Health analyst exercise.

## Run locally

```bash
cd bellhaven-crm-sync
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export CRM_TOKEN='your-token'
python -m src.pipeline run
python -m src.review_app          # http://127.0.0.1:5055
```

## What the four pieces do

1. **Scraper** (`src/scraper.py`) — every community page plus Findlay from the homepage.
2. **Matcher** (`src/match.py`) — confident match, field/parent fix, create, duplicate, leftover, CHOW.
3. **Review app** (`src/review_app.py`) — evidence + exact API payload. Writes only on approve.
4. **Daily schedule** — `cron/daily.cron` and `.github/workflows/daily-sync.yml`. Fingerprints in `data/decisions.json` make reruns safe.

## Matching rules

Bellhaven parent: `0015QAPLGS3FVYEEEM`.

- Same facility, already under Bellhaven → patch drifted name/address/phone/care_type.
- Wrong/missing parent, and NOT (revenue AND AR>0) → re-parent in place.
- Wrong parent AND lifetime_revenue > 0 AND outstanding_ar > 0 → CHOW: new Bellhaven child, set `chow_current_account` on the old row, do not change old parent.
- No CRM row → create under Bellhaven.
- Several CRM rows for one site → survivor stays Active; losers get `duplicate_of_account` + Inactive.
- Active Bellhaven child missing from the website → Inactive + note. No delete.

Name-only collisions are not matches (Hudson Amberly Manor ≠ Colorado Springs; 118 Union Square Dr ≠ 240 Market St).

## End state of this CRM copy

35 website communities ↔ 35 Active Bellhaven children. Second pipeline run is an empty queue.

CHOW: Marietta `001A34WFSUYHCRBLFT` → `00190EC211DECFC16D`; Tiffin `001U6RW32TY0WSXZZB` → `001C38203B5A6A57E6`.

Leftovers inactivated: Alliance, Coldwater, Sandusky.

## Time and AI use

About 2 hours 15 minutes. An AI coding agent inspected the site/API and drafted the pipeline; every proposal was checked against address, parent, revenue, and AR before write. The daily job itself does not call an LLM.

Next: administrator/contact sync, a nightly diff email, and fixture tests for CHOW/duplicate/collision cases.
