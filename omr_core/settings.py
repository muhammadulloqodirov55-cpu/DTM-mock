"""Shared persistent settings for ALL interfaces (web, bot, CLI).

Stored in data/settings.json:
    {
      "template": "dtm_official",          # which sheet type is being scanned
      "subjects": {                        # names for the 5 scoring blocks
        "m1": "Ona tili",                  # questions 1-10,  1.1 points
        "m2": "Matematika",                # questions 11-20, 1.1 points
        "m3": "O'zbekiston tarixi",        # questions 21-30, 1.1 points
        "fan1": "Fizika",                  # questions 31-60, 3.1 points
        "fan2": "Ingliz tili"              # questions 61-90, 2.1 points
      }
    }

Set once, then every scan (web upload, bot photo, CLI run) is scored with these
names until they are changed.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import layout as L

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "data" / "settings.json"

# DTM test sinovida uchraydigan fanlar (UZBMB "Fanlar majmuasi" 2025-2027 bo'yicha).
# Majburiy blok (1-30) rasman doim: Ona tili, Matematika, O'zbekiston tarixi.
SUBJECT_CHOICES = [
    "Matematika", "Fizika", "Kimyo", "Biologiya", "Tarix", "Geografiya",
    "Ona tili", "O'zbekiston tarixi",
    "Ona tili va adabiyoti", "O'zbek tili va adabiyoti",
    "Ingliz tili", "Nemis tili", "Fransuz tili", "Rus tili",
    "Chet tili", "Huquqshunoslik",
]

# Rasmiy yo'nalish -> fan juftligi qonuniyatlari (fan1 = 3.1 ball, fan2 = 2.1 ball).
# Manba: UZBMB fanlar majmuasi (2025/2026, 2026/2027). Har element: (fan1, fan2, izoh).
PAIR_PRESETS = [
    ("Matematika", "Fizika", "muhandislik, energetika, transport, arxitektura"),
    ("Fizika", "Matematika", "fizika, astronomiya"),
    ("Matematika", "Chet tili", "iqtisodiyot, moliya, bank ishi, menejment"),
    ("Matematika", "Ona tili va adabiyoti", "amaliy matematika, statistika"),
    ("Ona tili va adabiyoti", "Matematika", "boshlang'ich ta'lim"),
    ("Kimyo", "Biologiya", "tibbiyot: davolash, stomatologiya, farmatsiya"),
    ("Biologiya", "Kimyo", "biologiya, agronomiya, veterinariya"),
    ("Kimyo", "Matematika", "kimyo muhandisligi, materialshunoslik"),
    ("Biologiya", "Ona tili va adabiyoti", "psixologiya, maktabgacha ta'lim"),
    ("Tarix", "Ona tili va adabiyoti", "tarix, pedagogika"),
    ("Ona tili va adabiyoti", "Tarix", "o'zbek filologiyasi"),
    ("Tarix", "Geografiya", "geografiya, turizm"),
    ("Tarix", "Chet tili", "xalqaro munosabatlar, siyosatshunoslik"),
    ("Huquqshunoslik", "Chet tili", "yuridik yo'nalishlar"),
    ("Ingliz tili", "Ona tili va adabiyoti", "chet tili filologiyasi"),
    ("O'zbek tili va adabiyoti", "Chet tili", "o'zbek tili yo'nalishlari"),
]


def load() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        return {}


def save(d: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1))


# ---- admin boshqaruv sozlamalari (data/settings.json ichida "admin" kaliti) ----
ADMIN_DEFAULTS = {
    "archive_paused": False,     # True: yopilgan imtihonlar arxivga YOZILMAYDI
    "default_duration": 180,     # yangi imtihon uchun standart davomiylik (daqiqa)
    "max_resumes": 2,            # firibgarlikdan keyin "yana imkon berish" cheki (o'quvchi boshiga)
}


def _clean_admin(d: dict) -> dict:
    out = dict(ADMIN_DEFAULTS)
    out["archive_paused"] = bool(d.get("archive_paused", out["archive_paused"]))
    try:
        out["default_duration"] = max(5, min(600, int(d.get("default_duration", out["default_duration"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["max_resumes"] = max(0, min(10, int(d.get("max_resumes", out["max_resumes"]))))
    except (TypeError, ValueError):
        pass
    return out


def get_admin() -> dict:
    return _clean_admin(load().get("admin", {}))


def set_admin(vals: dict) -> dict:
    d = load()
    cur = load().get("admin", {})
    cur.update(vals)
    d["admin"] = _clean_admin(cur)
    save(d)
    return d["admin"]


def get_subjects() -> dict[str, str]:
    """Always returns all 5 slots (missing ones fall back to defaults)."""
    stored = load().get("subjects", {})
    return {slot: (stored.get(slot) or "").strip() or L.DEFAULT_SUBJECTS[slot]
            for slot, _, _, _ in L.SUBJECT_SLOTS}


def set_subjects(subjects: dict[str, str]) -> None:
    d = load()
    d["subjects"] = {slot: (subjects.get(slot) or "").strip() or L.DEFAULT_SUBJECTS[slot]
                     for slot, _, _, _ in L.SUBJECT_SLOTS}
    save(d)
