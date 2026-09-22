"""Online DTM mock exam engine (PIIMA/ChSB style).

MODEL (daftar chizmasi bo'yicha):
    UMUMIY IMTIHON (event: nomi, o'tkazish vaqti, davomiyligi)
      └─ YO'NALISHLAR (har biri chizmadagi bitta qator):
           - asosiy juftlik: fan 1 (3.1 ball) + fan 2 (2.1 ball)
           - majburiy 3 fan (1.1 balldan): Ona tili, Matematika, O'zbekiston tarixi
           - O'Z KIRISH KODI (o'quvchi shu kodni kiritadi)
           - variantlar: PDF kitobcha (tuzilish sahifasida) + javob kalitlari
             (alohida "Javoblar" sahifasida HAR FANGA ALOHIDA kiritiladi)

O'quvchi: yo'nalish kodini + familiya-ism + sinfni kiritadi -> shu yo'nalish savollari
va fanlari bilan imtihon topshiradi -> natijada umumiy ball + har fandan olgan bali.

An exam event lives in data/exams/<event_id>/:
    meta.json           - title, scheduled_at, duration, state, directions
    d<D>_v<NN>.pdf      - booklet per direction+variant
    attempts/<aid>.json - one student attempt

Anti-cheat: leaving the exam environment blocks the attempt and PAUSES its clock;
admin can resume; closing the exam finalizes everything.
"""
from __future__ import annotations

import json
import random
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import layout as L
from . import scoring
from .keys import normalize_variant, KeyError_
from .results import ScanResult, QuestionResult

# imtihonga kirishda tanlanadigan sinflar (Excel/PDF tartiblash shu tartibda)
CLASSES = ["11-A1", "11-A2", "11-A3", "11-A4", "11-T"]

FONT_DIR = Path(__file__).parent / "fonts"
KEY_CHARS = set("ABCD*-")
CODE_RE = re.compile(r"[A-Z0-9]{4,10}")
# diskdagi kitobcha nomlari faqat shu shakllarda bo'ladi (eski yassi imtihonda "vNN.pdf")
PDF_NAME_RE = re.compile(r"(?:d\d{1,3}_)?v\d{2}\.pdf")


class ExamError(Exception):
    pass


def _now() -> float:
    return datetime.now().timestamp()


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def block_sizes() -> list[tuple[str, int, int, int]]:
    """(slot, q_from, q_to, size) — 10/10/10/30/30."""
    return [(slot, a, b, b - a + 1) for slot, a, b, _ in L.SUBJECT_SLOTS]


def split_key(key: str) -> dict[str, str]:
    """90 talik kalitni fan bloklariga bo'lib beradi (Javoblar sahifasi formalari uchun)."""
    out = {}
    for slot, a, b, _ in L.SUBJECT_SLOTS:
        out[slot] = key[a - 1:b] if key and len(key) >= b else ""
    return out


class ExamStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- events (umumiy imtihonlar) ----------
    def _dir(self, eid: str) -> Path:
        return self.root / eid

    def _meta_path(self, eid: str) -> Path:
        return self._dir(eid) / "meta.json"

    @staticmethod
    def _clean_subjects(subjects: dict | None) -> dict:
        out = {}
        for slot, _, _, _ in L.SUBJECT_SLOTS:
            v = " ".join(str((subjects or {}).get(slot, "")).split())[:60]
            out[slot] = v or L.DEFAULT_SUBJECTS[slot]
        return out

    def _gen_code(self) -> str:
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        for _ in range(100):
            c = "".join(secrets.choice(alphabet) for _ in range(6))
            if not self._code_taken(c):
                return c
        raise ExamError("Kod yaratib bo'lmadi.")

    def _code_taken(self, code: str) -> bool:
        if self._meta_path(code).exists():
            return True
        for m in self.list():
            for d in m.get("directions", {}).values():
                if d.get("code") == code:
                    return True
        return False

    def create(self, title: str, duration_min: int, scheduled_at: str = "") -> dict:
        title = " ".join((title or "").split())
        if not title:
            raise ExamError("Imtihon nomini kiriting.")
        duration_min = max(5, min(600, int(duration_min)))
        eid = self._gen_code()
        meta = {"id": eid, "title": title, "duration_min": duration_min,
                "scheduled_at": " ".join((scheduled_at or "").split())[:60],
                "state": "draft", "created_at": _stamp(), "directions": {}}
        (self._dir(eid) / "attempts").mkdir(parents=True, exist_ok=True)
        self._save_meta(meta)
        return meta

    def update(self, eid: str, title: str, duration_min, scheduled_at: str = "") -> dict:
        meta = self._load_or_raise(eid)
        title = " ".join((title or "").split())
        if not title:
            raise ExamError("Imtihon nomini kiriting.")
        meta["title"] = title
        meta["duration_min"] = max(5, min(600, int(duration_min)))
        meta["scheduled_at"] = " ".join((scheduled_at or "").split())[:60]
        self._save_meta(meta)
        return meta

    def _save_meta(self, meta: dict):
        self._meta_path(meta["id"]).write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    @staticmethod
    def _migrate(meta: dict) -> dict:
        """Eski (yassi, bitta-yo'nalishli) imtihonni yangi tuzilishga ko'taradi."""
        if "directions" not in meta:
            subj = meta.pop("subjects", None) or {s: L.DEFAULT_SUBJECTS[s] for s, _, _, _ in L.SUBJECT_SLOTS}
            variants = meta.pop("variants", {}) or {}
            meta["directions"] = {"1": {
                "name": f"{subj['fan1']} – {subj['fan2']}",
                "subjects": subj, "code": meta["id"], "variants": variants,
            }} if variants or subj else {}
            meta.setdefault("scheduled_at", "")
        return meta

    def load(self, eid: str) -> Optional[dict]:
        eid = (eid or "").strip().upper()
        if not CODE_RE.fullmatch(eid or ""):
            return None
        p = self._meta_path(eid)
        if not p.exists():
            return None
        meta = self._migrate(json.loads(p.read_text()))
        return meta

    def _load_or_raise(self, eid: str) -> dict:
        meta = self.load(eid)
        if not meta:
            raise ExamError("Imtihon topilmadi.")
        return meta

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.root.glob("*/meta.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                m = self._migrate(json.loads(p.read_text()))
                m["n_attempts"] = len(list((p.parent / "attempts").glob("*.json")))
                out.append(m)
            except Exception:
                continue
        return out

    def delete(self, eid: str):
        """Imtihonni ro'yxatdan olib tashlaydi, LEKIN yo'q qilmaydi: butun katalog
        data/trash/ ga ko'chiriladi — adashib bosilganda natijalar qutqarib qolinadi."""
        import shutil
        eid = (eid or "").strip().upper()
        # CODE_RE ".." kabi qiymatlarni ham rad etadi — data/exams tashqarisiga chiqilmaydi
        if not CODE_RE.fullmatch(eid):
            return
        d = self._dir(eid)
        if d.exists() and d.parent == self.root:
            trash = self.root.parent / "trash"
            trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(trash / f"{eid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"))

    # ---------- yo'nalishlar ----------
    def add_direction(self, eid: str, subjects: dict | None, custom_code: str = "") -> dict:
        meta = self._load_or_raise(eid)
        subj = self._clean_subjects(subjects)
        code = (custom_code or "").strip().upper()
        if code:
            if not CODE_RE.fullmatch(code):
                raise ExamError("Kod 4–10 ta lotin harfi/raqamdan iborat bo'lsin (masalan, AB101010).")
            if self._code_taken(code):
                raise ExamError(f"'{code}' kodi band — boshqa kod tanlang.")
        else:
            code = self._gen_code()
        did = str(1 + max([int(k) for k in meta["directions"]] or [0]))
        meta["directions"][did] = {"name": f"{subj['fan1']} – {subj['fan2']}",
                                   "subjects": subj, "code": code, "variants": {}}
        self._save_meta(meta)
        return meta

    def update_direction(self, eid: str, did: str, subjects: dict | None):
        meta = self._load_or_raise(eid)
        d = meta["directions"].get(str(did))
        if not d:
            raise ExamError("Yo'nalish topilmadi.")
        d["subjects"] = self._clean_subjects(subjects)
        d["name"] = f"{d['subjects']['fan1']} – {d['subjects']['fan2']}"
        self._save_meta(meta)

    def delete_direction(self, eid: str, did: str):
        meta = self._load_or_raise(eid)
        d = meta["directions"].pop(str(did), None)
        if d:
            for v, info in d.get("variants", {}).items():
                self._unlink_pdf(eid, did, v, info)
        self._save_meta(meta)

    def _unlink_pdf(self, eid: str, did: str, v: str, info: dict | None):
        """Variant kitobchasini o'chiradi — yangi ham, eski (yassi) nomdagi faylni ham."""
        names = {f"d{did}_v{v}.pdf"}
        stored = (info or {}).get("pdf", "")
        if stored and PDF_NAME_RE.fullmatch(stored):
            names.add(stored)
        for n in names:
            p = self._dir(eid) / n
            if p.exists():
                p.unlink()

    def find_by_code(self, code: str) -> tuple[Optional[dict], Optional[str]]:
        """Yo'nalish kirish kodi bo'yicha (event, direction_id) topadi."""
        code = (code or "").strip().upper()
        if not CODE_RE.fullmatch(code or ""):
            return None, None
        for m in self.list():
            for did, d in m.get("directions", {}).items():
                if d.get("code") == code:
                    return m, did
        return None, None

    # ---------- variantlar: PDF (tuzilish sahifasi) + kalitlar (Javoblar sahifasi) ----------
    def upload_pdf(self, eid: str, did: str, variant, pdf_bytes: bytes):
        meta = self._load_or_raise(eid)
        d = meta["directions"].get(str(did))
        if not d:
            raise ExamError("Yo'nalish topilmadi.")
        v = normalize_variant(variant)
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
            raise ExamError("Yuklangan fayl PDF emas.")
        pdf_name = f"d{did}_v{v}.pdf"
        (self._dir(eid) / pdf_name).write_bytes(pdf_bytes)
        old = d["variants"].get(v, {})
        d["variants"][v] = {"pdf": pdf_name, "key": old.get("key", "")}
        self._save_meta(meta)

    def set_keys(self, eid: str, did: str, variant, parts: dict) -> None:
        """Javoblar sahifasi: HAR FAN BLOKI ALOHIDA kiritiladi (10/10/10/30/30)."""
        meta = self._load_or_raise(eid)
        d = meta["directions"].get(str(did))
        if not d:
            raise ExamError("Yo'nalish topilmadi.")
        v = normalize_variant(variant)
        if v not in d["variants"]:
            raise ExamError("Avval bu variant uchun PDF yuklang (Tuzilish sahifasida).")
        key = ""
        for slot, a, b, size in block_sizes():
            raw = str(parts.get(slot, "")).upper()
            chars = [c for c in raw if c in KEY_CHARS]
            if len(chars) != size:
                raise ExamError(f"{d['subjects'][slot]} ({a}–{b}): {size} ta javob kerak, "
                                f"{len(chars)} ta kiritildi.")
            key += "".join(chars)
        d["variants"][v]["key"] = key
        self._save_meta(meta)

    def delete_variant(self, eid: str, did: str, variant: str):
        meta = self._load_or_raise(eid)
        d = meta["directions"].get(str(did))
        if not d:
            return
        v = normalize_variant(variant)
        info = d["variants"].pop(v, None)
        self._unlink_pdf(eid, str(did), v, info)
        self._save_meta(meta)

    def pdf_path(self, eid: str, did: str, variant: str) -> Path:
        v = normalize_variant(variant)
        base = self._dir(eid)
        p = base / f"d{did}_v{v}.pdf"
        if p.exists():
            return p
        # eski (yassi) imtihondan meros qolgan fayl ("vNN.pdf"): meta'dagi nomni
        # qat'iy shablon bilan tekshirib ishlatamiz — yo'l foydalanuvchidan kelmaydi
        meta = self.load(eid)
        if meta:
            name = (meta.get("directions", {}).get(str(did), {})
                    .get("variants", {}).get(v, {}).get("pdf", ""))
            if name and PDF_NAME_RE.fullmatch(name):
                return base / name
        return p

    @staticmethod
    def _ready_variants(d: dict) -> list[str]:
        return [v for v, info in d.get("variants", {}).items()
                if info.get("pdf") and len(info.get("key", "")) == L.N_QUESTIONS]

    def ready_directions(self, meta: dict) -> list[str]:
        return [did for did, d in meta.get("directions", {}).items() if self._ready_variants(d)]

    def set_state(self, eid: str, state: str, archived: bool = True):
        meta = self._load_or_raise(eid)
        if state not in ("draft", "open", "closed"):
            raise ExamError(state)
        if state == "open" and not self.ready_directions(meta):
            raise ExamError("Ochish uchun kamida bitta yo'nalishda PDF ham, javoblar ham "
                            "to'liq kiritilgan variant bo'lishi kerak.")
        meta["state"] = state
        if state == "closed":
            # arxiv pauzada bo'lsa bu imtihon Arxiv sahifasida ko'rinmaydi
            meta["archived"] = bool(archived)
        self._save_meta(meta)
        if state == "closed":
            # yakunlash: tugallanmagan (shu jumladan bloklangan) urinishlar avtomatik
            # topshiriladi — "yana imkon berish" ro'yxati bo'shab, ortiqcha holat qolmaydi
            for att in self.attempts(eid):
                if not att.get("submitted"):
                    self.submit(att, auto=True)

    # ---------- attempts ----------
    def _attempt_path(self, eid: str, aid: str) -> Path:
        return self._dir(eid) / "attempts" / f"{aid}.json"

    def start_attempt(self, code: str, student_name: str, klass: str) -> dict:
        meta, did = self.find_by_code(code)
        if not meta:
            raise ExamError("Bunday kodli imtihon topilmadi. Kodni tekshiring.")
        if meta["state"] != "open":
            raise ExamError("Bu imtihon hozir ochiq emas.")
        d = meta["directions"][did]
        ready = self._ready_variants(d)
        if not ready:
            raise ExamError("Bu yo'nalish hali tayyor emas.")
        student_name = " ".join(student_name.split())[:80]
        if len(student_name) < 3:
            raise ExamError("Familiya va ismni to'liq kiriting.")
        if klass not in CLASSES:
            raise ExamError("Sinfni tanlang.")
        # qayta ulanish: shu ism+sinf bilan tugallanmagan urinish bo'lsa — YANGI ochilmaydi,
        # o'sha urinishga qaytariladi (yangi token: bir vaqtda faqat bitta qurilma ishlaydi)
        for old in self.attempts(meta["id"]):
            if (not old.get("submitted") and old.get("student_name", "").lower() == student_name.lower()
                    and old.get("klass") == klass):
                old["token"] = secrets.token_hex(16)
                self._save_attempt(old)
                return old
        n_att = len(list((self._dir(meta["id"]) / "attempts").glob("*.json")))
        if n_att >= 3000:
            raise ExamError("Bu imtihonda urinishlar soni chegaraga yetdi.")
        variant = random.choice(sorted(ready))
        aid = datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(3)
        att = {"id": aid, "exam_id": meta["id"], "student_name": student_name, "klass": klass,
               "direction": did, "direction_name": d["name"],
               "variant": variant, "started_ts": _now(), "started_at": _stamp(),
               "duration_min": meta["duration_min"], "answers": {}, "flags": [], "submitted": False,
               "cheat_count": 0, "paused_total_s": 0.0, "token": secrets.token_hex(16)}
        self._attempt_path(meta["id"], aid).write_text(json.dumps(att, ensure_ascii=False))
        return att

    def load_attempt(self, eid: str, aid: str) -> Optional[dict]:
        p = self._attempt_path((eid or "").strip().upper(), aid)
        if not p.exists():
            return None
        return json.loads(p.read_text())

    def _save_attempt(self, att: dict):
        self._attempt_path(att["exam_id"], att["id"]).write_text(json.dumps(att, ensure_ascii=False))

    # --- timing ---
    @staticmethod
    def _deadline(att: dict) -> float:
        return att["started_ts"] + att["duration_min"] * 60 + att.get("paused_total_s", 0.0)

    def remaining_s(self, att: dict) -> int:
        return max(0, int(self._deadline(att) - _now()))

    # --- anti-cheat ---
    def record_violation(self, att: dict, answers=None, flags=None) -> dict:
        """Nazorat buzilishi (fullscreen/tab tark etildi): BLOKLAMAYMIZ — buzilish
        sanaladi, javoblar saqlanadi; o'quvchi ogohlantirishni ko'rib o'z joyidan
        davom etadi. Soni natija izohida va admin jonli sahifasida ko'rinadi."""
        if att["submitted"]:
            return att
        if answers is not None:
            att["answers"] = self._clean_answers(answers)
        if flags is not None:
            att["flags"] = self._clean_flags(flags)
        att["cheat_count"] = att.get("cheat_count", 0) + 1
        self._save_attempt(att)
        return att

    # --- answers ---
    @staticmethod
    def _clean_answers(answers: dict) -> dict[str, str]:
        out = {}
        for k, v in (answers or {}).items():
            try:
                q = int(k)
            except (TypeError, ValueError):
                continue
            if 1 <= q <= L.N_QUESTIONS and isinstance(v, str) and v.upper() in L.OPTION_LABELS:
                out[str(q)] = v.upper()
        return out

    @staticmethod
    def _clean_flags(flags) -> list[int]:
        out = set()
        for v in (flags or [])[:200]:
            try:
                q = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= q <= L.N_QUESTIONS:
                out.add(q)
        return sorted(out)

    def save_answers(self, att: dict, answers: dict, flags=None) -> dict:
        if att["submitted"]:
            raise ExamError("Bu urinish allaqachon yakunlangan.")
        if self.remaining_s(att) <= 0 and _now() > self._deadline(att) + 120:
            raise ExamError("Vaqt tugagan.")
        att["answers"] = self._clean_answers(answers)
        if flags is not None:
            att["flags"] = self._clean_flags(flags)
        self._save_attempt(att)
        return att

    def submit(self, att: dict, answers: dict | None = None, auto: bool = False,
               auto_note: str | None = None) -> dict:
        """Final submission -> DTM score by the attempt's DIRECTION subjects/key.
        auto=True — server o'zi topshiradi (admin yakunlaganda yoki chek tugaganda);
        auto_note — natija izohiga yoziladigan sabab matni."""
        if att["submitted"]:
            return att
        late = (not auto) and _now() > self._deadline(att) + 120
        if answers is not None and not late:
            att["answers"] = self._clean_answers(answers)
        meta = self.load(att["exam_id"]) or {}
        d = meta.get("directions", {}).get(att.get("direction", ""), {})
        key = d.get("variants", {}).get(att["variant"], {}).get("key") or None
        subjects = d.get("subjects")
        qs = []
        for q in range(1, L.N_QUESTIONS + 1):
            a = att["answers"].get(str(q))
            qs.append(QuestionResult(q=q, answer=a, status="ok" if a else "blank"))
        scan = ScanResult(questions=qs, student_id=None, student_id_status="none",
                          variant=att["variant"], variant_status="ok",
                          markers_found=[], warnings=[], blur_score=0.0, template="online")
        report = scoring.score(scan, key, subjects=subjects)
        report.student_name = att["student_name"]
        report.scanned_at = _stamp()
        report.image_name = f"{att.get('direction_name', '')} (variant {att['variant']})"
        if late:
            report.warnings.append("Vaqt tugaganidan keyin topshirildi.")
        if auto:
            report.warnings.append(auto_note or "Imtihon admin tomonidan yakunlandi.")
        if att.get("cheat_count"):
            report.warnings.append(f"Nazorat buzilishi qayd etilgan: {att['cheat_count']} marta.")
        att["submitted"] = True
        att["finished_at"] = _stamp()
        att["elapsed_s"] = max(0, int(min(_now(), self._deadline(att)) - att["started_ts"] - att.get("paused_total_s", 0.0)))
        att["report"] = report.to_dict()
        self._save_attempt(att)
        return att

    def attempts(self, eid: str) -> list[dict]:
        out = []
        d = self._dir((eid or "").strip().upper()) / "attempts"
        if not d.exists():
            return out
        for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                out.append(json.loads(p.read_text()))
            except Exception:
                continue
        return out

    def delete_attempt(self, eid: str, aid: str):
        p = self._attempt_path(eid, aid)
        if p.exists():
            p.unlink()


# ============================================================================
# REPORTS: Excel + PDF (event darajasida, yo'nalishlar kesimida)
# ============================================================================

def _sorted_done(attempts: list[dict]) -> list[dict]:
    done = [a for a in attempts if a.get("submitted") and a.get("report")]
    korder = {k: i for i, k in enumerate(CLASSES)}
    done.sort(key=lambda a: (korder.get(a.get("klass", ""), 99), a.get("student_name", "").lower()))
    return done


def export_exam_excel(meta: dict, attempts: list[dict], path) -> Path:
    """MS Excel: 'Umumiy' (hamma o'quvchi) + har yo'nalish uchun alohida varaq
    (fan ballari ustunlari bilan) + 'Javoblar' (90 ta, yashil/qizil)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    done = _sorted_done(attempts)
    wb = Workbook()
    hfill = PatternFill("solid", fgColor="111111")
    band = PatternFill("solid", fgColor="F2F0EA")

    def style_head(ws, row, ncols):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=row, column=c)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = hfill
            cell.alignment = Alignment(horizontal="center")

    ws = wb.active
    ws.title = "Umumiy"
    ws.append([meta.get("title", ""), "", meta.get("scheduled_at", ""), "",
               f"Yaratilgan: {meta.get('created_at','')}"])
    ws["A1"].font = Font(bold=True, size=13)
    head = ["№", "Sinf", "Familiya Ism", "Yo'nalish", "Variant", "Jami ball", "Maks", "%",
            "To'g'ri", "Noto'g'ri", "Bo'sh", "Vaqt (daq)", "Izoh"]
    ws.append(head)
    style_head(ws, 2, len(head))
    prev_k, ri = None, 3
    for i, a in enumerate(done, 1):
        r = a["report"]
        ws.append([i, a.get("klass", ""), a.get("student_name", ""), a.get("direction_name", ""),
                   int(a.get("variant", "0")), float(r["total_points"]), float(r["max_points"]),
                   float(r["percent"]), r["correct"], r["wrong"], r["blank"],
                   round(a.get("elapsed_s", 0) / 60), "; ".join(r.get("warnings", []))])
        if a.get("klass") != prev_k:
            for c in range(1, len(head) + 1):
                ws.cell(row=ri, column=c).fill = band
            prev_k = a.get("klass")
        ri += 1
    for i, w in enumerate([4, 8, 26, 24, 8, 10, 8, 7, 8, 9, 7, 10, 34], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A3"

    for did, d in sorted(meta.get("directions", {}).items(), key=lambda t: int(t[0])):
        rows = [a for a in done if a.get("direction") == did]
        if not rows:
            continue
        wd = wb.create_sheet(d["name"][:28].replace("/", "-"))
        subj_names = [s["name"] for s in rows[0]["report"]["subjects"]]
        head_d = ["№", "Sinf", "Familiya Ism", "Variant"] + [f"{n} (ball)" for n in subj_names] + \
                 ["Jami", "Maks", "%", "To'g'ri", "Noto'g'ri", "Bo'sh"]
        wd.append([f"{meta.get('title','')} — {d['name']} (kod: {d.get('code','')})"])
        wd["A1"].font = Font(bold=True, size=12)
        wd.append(head_d)
        style_head(wd, 2, len(head_d))
        prev_k, ri = None, 3
        for i, a in enumerate(rows, 1):
            r = a["report"]
            wd.append([i, a.get("klass", ""), a.get("student_name", ""), int(a.get("variant", "0"))]
                      + [float(s["points"]) for s in r["subjects"]]
                      + [float(r["total_points"]), float(r["max_points"]), float(r["percent"]),
                         r["correct"], r["wrong"], r["blank"]])
            if a.get("klass") != prev_k:
                for c in range(1, len(head_d) + 1):
                    wd.cell(row=ri, column=c).fill = band
                prev_k = a.get("klass")
            ri += 1
        for i, w in enumerate([4, 8, 26, 8] + [15] * len(subj_names) + [9, 8, 7, 8, 9, 7], 1):
            wd.column_dimensions[get_column_letter(i)].width = w
        wd.freeze_panes = "A3"

    wa = wb.create_sheet("Javoblar")
    wa.append(["Sinf", "Familiya Ism", "Yo'nalish", "Variant"] + [str(q) for q in range(1, L.N_QUESTIONS + 1)])
    for c in range(1, L.N_QUESTIONS + 5):
        wa.cell(row=1, column=c).font = Font(bold=True)
    green, red = PatternFill("solid", fgColor="C6EFCE"), PatternFill("solid", fgColor="FFC7CE")
    for ri2, a in enumerate(done, 2):
        r = a["report"]
        wa.append([a.get("klass", ""), a.get("student_name", ""), a.get("direction_name", ""),
                   int(a.get("variant", "0"))] + [q["given"] or "" for q in r["questions"]])
        for qi, q in enumerate(r["questions"], 5):
            cell = wa.cell(row=ri2, column=qi)
            cell.alignment = Alignment(horizontal="center")
            if q["correct"] in ("*", "-"):
                continue
            cell.fill = green if q["is_correct"] else red
    for c in range(5, L.N_QUESTIONS + 5):
        wa.column_dimensions[get_column_letter(c)].width = 3.5
    wa.freeze_panes = "E2"

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _pdf_doc(landscape=False):
    from fpdf import FPDF
    pdf = FPDF(orientation="L" if landscape else "P", unit="mm", format="A4")
    pdf.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.set_auto_page_break(auto=True, margin=12)
    return pdf


def export_exam_pdf(meta: dict, attempts: list[dict], path) -> Path:
    """Umumiy imtihon hisobot PDF'i: har yo'nalish bo'yicha sinf->familiya tartibli jadval."""
    done = _sorted_done(attempts)
    pdf = _pdf_doc(landscape=True)
    pdf.add_page()
    pdf.set_font("DejaVu", "B", 15)
    pdf.cell(0, 9, meta.get("title", ""), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(100)
    extra = f"O'tkazish vaqti: {meta['scheduled_at']}   ·   " if meta.get("scheduled_at") else ""
    pdf.cell(0, 6, extra + f"Yakunlaganlar: {len(done)} ta   ·   Baholash: 1.1 / 3.1 / 2.1, maks 189.0",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)

    for did, d in sorted(meta.get("directions", {}).items(), key=lambda t: int(t[0])):
        rows = [a for a in done if a.get("direction") == did]
        if not rows:
            continue
        subj_names = [s["name"] for s in rows[0]["report"]["subjects"]]
        pdf.ln(4)
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(0, 8, f"Yo'nalish: {d['name']}  (kod: {d.get('code','')})", new_x="LMARGIN", new_y="NEXT")
        widths = [8, 15, 50, 12] + [24] * 5 + [16, 11, 22]
        headers = ["№", "Sinf", "Familiya Ism", "Var."] + subj_names + ["Jami", "%", "T / N / B"]
        pdf.set_font("DejaVu", "B", 7.5)
        pdf.set_fill_color(17, 17, 17)
        pdf.set_text_color(255)
        for w, h in zip(widths, headers):
            pdf.cell(w, 7, str(h)[:18], border=1, fill=True, align="C")
        pdf.ln()
        pdf.set_text_color(0)
        pdf.set_font("DejaVu", "", 8)
        for i, a in enumerate(rows, 1):
            r = a["report"]
            vals = [i, a.get("klass", ""), a.get("student_name", ""), int(a.get("variant", "0"))] + \
                   [s["points"] for s in r["subjects"]] + \
                   [r["total_points"], r["percent"], f"{r['correct']} / {r['wrong']} / {r['blank']}"]
            fill = i % 2 == 0
            pdf.set_fill_color(242, 240, 234)
            for w, v in zip(widths, vals):
                pdf.cell(w, 6.4, str(v), border=1, fill=fill,
                         align="L" if isinstance(v, str) and len(str(v)) > 6 else "C")
            pdf.ln()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path


def student_result_pdf(att: dict, meta: dict, path) -> Path:
    """Bitta o'quvchi natijasi PDF (chop etish / yuborish uchun)."""
    r = att["report"]
    pdf = _pdf_doc()
    pdf.add_page()
    pdf.set_fill_color(17, 17, 17)
    pdf.set_text_color(255)
    pdf.rect(0, 0, 210, 40, style="F")
    pdf.set_y(8)
    pdf.set_font("DejaVu", "B", 14)
    pdf.cell(0, 8, meta.get("title", ""), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9)
    pdf.cell(0, 6, f"{att.get('student_name','')} · {att.get('klass','')} · {att.get('direction_name','')} · "
                   f"Variant {int(att.get('variant','0'))} · {att.get('finished_at','')}",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "B", 20)
    pdf.cell(0, 12, f"{r['total_points']} / {r['max_points']}  ({r['percent']}%)", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.set_y(46)

    pdf.set_font("DejaVu", "B", 10)
    pdf.cell(0, 7, "Fanlar bo'yicha", new_x="LMARGIN", new_y="NEXT")
    widths = [62, 24, 18, 22, 16, 28]
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_fill_color(17, 17, 17)
    pdf.set_text_color(255)
    for w, h in zip(widths, ["Fan", "Savollar", "To'g'ri", "Noto'g'ri", "Bo'sh", "Ball"]):
        pdf.cell(w, 6.5, h, border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_text_color(0)
    pdf.set_font("DejaVu", "", 9)
    for s in r["subjects"]:
        for w, v in zip(widths, [s["name"], f"{s['q_from']}–{s['q_to']}", s["correct"], s["wrong"],
                                 s["blank"], f"{s['points']} / {s['max_points']}"]):
            pdf.cell(w, 6.2, str(v), border=1, align="C" if w < 60 else "L")
        pdf.ln()

    for w in r.get("warnings", []):
        pdf.set_text_color(179, 38, 30)
        pdf.set_font("DejaVu", "", 8.5)
        pdf.cell(0, 6, "! " + w, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)

    pdf.ln(2)
    pdf.set_font("DejaVu", "B", 10)
    pdf.cell(0, 7, "Javoblar tahlili (sizniki -> to'g'risi)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 7.5)
    cols, cw, ch = 6, 31, 5.6
    for i, q in enumerate(r["questions"]):
        if q["correct"] in ("*", "-"):
            txt, ok = f"{q['q']}. {q['given'] or '—'}", None
        elif q["is_correct"]:
            txt, ok = f"{q['q']}. {q['given']}", True
        elif q["given"]:
            txt, ok = f"{q['q']}. {q['given']} -> {q['correct']}", False
        else:
            txt, ok = f"{q['q']}. — -> {q['correct']}", None
        if ok is True:
            pdf.set_fill_color(212, 237, 218)
        elif ok is False:
            pdf.set_fill_color(248, 215, 218)
        else:
            pdf.set_fill_color(245, 245, 243)
        pdf.cell(cw, ch, txt, border=1, fill=True)
        if (i + 1) % cols == 0:
            pdf.ln()
    pdf.ln(8)
    pdf.set_font("DejaVu", "", 7.5)
    pdf.set_text_color(120)
    pdf.cell(0, 5, "DTM Mock platformasi · 1–30 -> 1.1 ball · 31–60 -> 3.1 ball · 61–90 -> 2.1 ball · maks 189.0",
             align="C")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path
