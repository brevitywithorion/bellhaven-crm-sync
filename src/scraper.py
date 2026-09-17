from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import config


def _soup(url: str) -> BeautifulSoup:
    r = requests.get(url, timeout=30, headers={"User-Agent": "BellhavenSync/1.0"})
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def collect_slugs() -> list[str]:
    slugs: list[str] = []
    seen: set[str] = set()

    def add(href: str) -> None:
        if not href:
            return
        path = href.split("?")[0]
        m = re.search(r"/communities/([a-z0-9-]+)/?$", path)
        if not m:
            return
        slug = m.group(1)
        if slug not in seen:
            seen.add(slug)
            slugs.append(slug)

    home = _soup(f"{config.SITE_BASE}/")
    for a in home.find_all("a", href=True):
        add(a["href"])

    page = 1
    seen_page_hashes: set[str] = set()
    while True:
        url = f"{config.SITE_BASE}/communities" + (f"?page={page}" if page > 1 else "")
        r = requests.get(url, timeout=30, headers={"User-Agent": "BellhavenSync/1.0"})
        r.raise_for_status()
        page_hash = hashlib.sha256(r.text.encode()).hexdigest()
        if page_hash in seen_page_hashes:
            break
        seen_page_hashes.add(page_hash)
        soup = BeautifulSoup(r.text, "html.parser")
        found_this_page = 0
        for a in soup.find_all("a", href=True):
            before = len(slugs)
            add(a["href"])
            if len(slugs) > before:
                found_this_page += 1
        nxt = soup.find("a", string=re.compile(r"Next", re.I))
        if not nxt or found_this_page == 0:
            if not nxt:
                break
            href = nxt.get("href") or ""
            m = re.search(r"page=(\d+)", href)
            if not m:
                break
            nxt_page = int(m.group(1))
            if nxt_page <= page:
                break
            page = nxt_page
            continue
        page += 1
        if page > 10:
            break
    return slugs


def parse_community(slug: str) -> dict:
    url = f"{config.SITE_BASE}/communities/{slug}"
    soup = _soup(url)
    wrap = soup.select_one(".wrap") or soup
    h1 = wrap.find("h1")
    name = h1.get_text(" ", strip=True) if h1 else slug.replace("-", " ").title()

    fields: dict[str, str] = {}
    for dt in wrap.find_all("dt"):
        key = dt.get_text(" ", strip=True)
        dd = dt.find_next("dd")
        if not dd:
            continue
        if key.lower() == "address":
            raw = dd.decode_contents()
            raw = raw.replace("<br/>", "\n").replace("<br>", "\n").replace("<br />", "\n")
            lines = [BeautifulSoup(x, "html.parser").get_text(" ", strip=True) for x in raw.split("\n")]
            lines = [ln for ln in lines if ln]
            street = lines[0] if lines else ""
            city = state = zipc = ""
            if len(lines) >= 2:
                m = re.match(r"(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", lines[1])
                if m:
                    city, state, zipc = m.group(1), m.group(2), m.group(3)
            fields["street"] = street
            fields["city"] = city
            fields["state"] = state
            fields["zip"] = zipc
            fields["raw_address"] = " | ".join(lines)
        elif key.lower() == "care offerings":
            badges = [s.get_text(" ", strip=True) for s in dd.find_all("span")]
            fields["care_offerings"] = ", ".join(badges) if badges else dd.get_text(" ", strip=True)
        else:
            fields[key.lower()] = dd.get_text(" ", strip=True)

    return {
        "name": name,
        "street": fields.get("street", ""),
        "city": fields.get("city", ""),
        "state": fields.get("state", ""),
        "zip": fields.get("zip", ""),
        "care_offerings": fields.get("care_offerings", ""),
        "administrator": fields.get("administrator", ""),
        "phone": fields.get("phone", ""),
        "slug": slug,
        "url": url,
        "raw_address": fields.get("raw_address", ""),
    }


def scrape() -> list[dict]:
    slugs = collect_slugs()
    locs = [parse_community(s) for s in slugs]
    config.WEBSITE_PATH.write_text(json.dumps(locs, indent=2))
    return locs


if __name__ == "__main__":
    rows = scrape()
    print(f"scraped {len(rows)} communities")
    for r in rows:
        print(f"{r['name'][:40]:40} {r['street']}, {r['city']} {r['state']} {r['zip']} | {r['care_offerings']}")
