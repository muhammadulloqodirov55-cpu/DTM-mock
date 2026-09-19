"""Answer-key storage.

Keys live in data/keys/keys.json  ->  {"01": "ABCD...", "02": "..."}  (90 chars each)
Allowed characters per question:
    A B C D  - correct option
    *        - question annulled: counted as correct for everybody
    -        - no key given: question is skipped (0 points for everybody)
"""
from __future__ import annotations

import json
from pathlib import Path

from . import layout as L

VALID = set(L.OPTION_LABELS) | {"*", "-"}


class KeyError_(Exception):
    pass


def normalize_key(raw: str) -> str:
    """Accepts 'ABCD...', 'A B C D', '1.A 2.B', 'A,B,C' ... returns 90-char string."""
    s = raw.upper()
    # keep only meaningful symbols; strip digits/dots/spaces/commas
    chars = [ch for ch in s if ch in VALID]
    key = "".join(chars)
    if len(key) != L.N_QUESTIONS:
        raise KeyError_(f"Kalit {L.N_QUESTIONS} ta belgidan iborat bo'lishi kerak, topildi: {len(key)}")
    return key


def normalize_variant(v: str | int) -> str:
    v = str(v).strip()
    if not v.isdigit() or not (0 <= int(v) <= 99):
        raise KeyError_(f"Variant 00-99 oralig'ida raqam bo'lishi kerak: {v!r}")
    return f"{int(v):02d}"


class KeyStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, str] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text())

    def all(self) -> dict[str, str]:
        return dict(sorted(self._data.items()))

    def get(self, variant: str | int) -> str | None:
        return self._data.get(normalize_variant(variant))

    def set(self, variant: str | int, raw_key: str) -> str:
        v = normalize_variant(variant)
        self._data[v] = normalize_key(raw_key)
        self._save()
        return v

    def delete(self, variant: str | int) -> bool:
        v = normalize_variant(variant)
        ok = self._data.pop(v, None) is not None
        self._save()
        return ok

    def _save(self):
        self.path.write_text(json.dumps(self._data, indent=1))
