from __future__ import annotations

import re
from difflib import SequenceMatcher

STREET_ABBR = {
    "street": "st",
    "avenue": "ave",
    "road": "rd",
    "drive": "dr",
    "lane": "ln",
    "boulevard": "blvd",
    "court": "ct",
    "place": "pl",
    "parkway": "pkwy",
    "terrace": "ter",
    "circle": "cir",
    "highway": "hwy",
    "pike": "pike",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}

NOISE_NAME = {
    "the", "of", "at", "and", "&", "bellhaven", "senior", "living", "care",
    "center", "centre", "healthcare", "health", "nursing", "rehab",
    "rehabilitation", "community", "communities", "manor", "gardens",
    "court", "woods", "shores", "crossings", "terrace", "estates",
}


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def norm_zip(z: str) -> str:
    return re.sub(r"\D", "", z or "")[:5]


def norm_state(s: str) -> str:
    return collapse(s or "").upper()


def norm_city(s: str) -> str:
    return collapse(s or "").lower().replace(".", "")


def norm_street(s: str) -> str:
    s = collapse(s or "").lower().replace(".", "")
    s = re.sub(r"[#,]", " ", s)
    s = re.sub(r"\b(po|p o)\s*box\b", "pobox", s)
    return " ".join(STREET_ABBR.get(tok, tok) for tok in s.split())


def house_number(street: str) -> str:
    m = re.match(r"(\d+[a-z]?)", norm_street(street))
    return m.group(1) if m else ""


def ratio(a: str, b: str) -> float:
    a, b = collapse(a).lower(), collapse(b).lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def name_core(name: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", (name or "").lower())
    keep = [t for t in tokens if t not in NOISE_NAME]
    return " ".join(keep) if keep else " ".join(tokens)


def phones_digits(p: str) -> str:
    return re.sub(r"\D", "", p or "")[-10:]
