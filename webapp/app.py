"""DTM Mock — onlayn imtihon platformasi (PIIMA/ChSB uslubida).

Run:   python -m webapp.app          (from the project root)
Open:  http://127.0.0.1:5000
    /           - o'quvchi: kod + F.I.O. + sinf + yo'nalish -> onlayn imtihon
    /exams      - admin: imtihonlar, yo'nalishlar (PDF + kalitlar), natijalar (Excel/PDF)
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets as pysecrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))

from omr_core import layout as L                       # noqa: E402
from omr_core import settings as S                      # noqa: E402
from omr_core.exam import (ExamStore, ExamError, CLASSES, split_key, block_sizes,   # noqa: E402
                           export_exam_excel, export_exam_pdf, student_result_pdf)
from omr_core.keys import KeyError_                     # noqa: E402

DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
log = logging.getLogger("dtm_mock")
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)

# security audit log — fail2ban shu faylni o'qiy oladi (deploy/fail2ban/ ga qarang)
seclog = logging.getLogger("dtm_sec")
_sh = logging.FileHandler(DATA / "security.log")
_sh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
seclog.addHandler(_sh)
seclog.setLevel(logging.INFO)


def _persistent_secret() -> bytes:
    """Stable across restarts: otherwise every restart kills all sessions and students
    mid-exam get 403 on save/submit. Env overrides; else a key file with 0600 perms."""
    v = os.environ.get("DTM_OMR_SECRET")
    if v:
        return v.encode()
    f = DATA / ".secret_key"
    if not f.exists():
        f.write_bytes(pysecrets.token_bytes(32))
        f.chmod(0o600)
    return f.read_bytes()


# Railway/PaaS avto-aniqlash: u yerda ilova doim HTTPS beruvchi proxy ortida turadi,
# shuning uchun Secure cookie / HSTS / X-Forwarded-For ishonchi o'z-o'zidan yoqiladi
ON_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
HTTPS_ON = bool(os.environ.get("DTM_HTTPS")) or ON_RAILWAY

app.secret_key = _persistent_secret()
app.config.update(
    MAX_CONTENT_LENGTH=64 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=HTTPS_ON,
    PERMANENT_SESSION_LIFETIME=12 * 3600,
)

exams = ExamStore(DATA / "exams")


# ============================================================================
# SECURITY LAYER: rate limiting, bans, admin auth, headers
# ============================================================================

class RateLimiter:
    """In-process sliding-window limiter (thread-safe, memory-bounded). For serious
    volumetric DDoS put nginx/Cloudflare in front — this stops abuse the app can see."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()
        self._next_gc = 0.0

    def allow(self, key: str, limit: int, per_s: float) -> bool:
        now = time.monotonic()
        with self._lock:
            # xotira to'lib ketsa — HAMMANI bo'shatmaymiz (aks holda hujumchi 50k kalit
            # yaratib boshqalarning hisoblagichini nolga tushirishi mumkin edi): faqat
            # eskirgan kalitlar tozalanadi, skan esa 5 soniyada ko'pi bilan 1 marta
            # (aks holda to'lgan lug'at har so'rovda O(n) aylanib CPU'ni yeb qo'yadi).
            if len(self._hits) > 50000 and now >= self._next_gc:
                self._next_gc = now + 5.0
                for k in [k for k, d in self._hits.items() if not d or d[-1] < now - 3600]:
                    del self._hits[k]
            dq = self._hits[key]
            while dq and dq[0] < now - per_s:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            return True


class BanList:
    """Qoidabuzarlikni takrorlagan IP'lar vaqtincha to'liq bloklanadi."""

    def __init__(self):
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def banned(self, ip: str) -> bool:
        with self._lock:
            t = self._until.get(ip)
            if t is None:
                return False
            if t < time.monotonic():
                del self._until[ip]
                return False
            return True

    def ban(self, ip: str, seconds: float):
        now = time.monotonic()
        with self._lock:
            if len(self._until) > 20000:
                # clear() MUMKIN EMAS: hujumchi lug'atni to'ldirib hammaning (o'zining ham)
                # banini bekor qilar edi. Avval muddati o'tganlar, yetmasa eng erta
                # tugaydiganlar chiqariladi — uzoq banlar joyida qoladi.
                for k in [k for k, t in self._until.items() if t < now]:
                    del self._until[k]
                if len(self._until) > 20000:
                    for k, _ in sorted(self._until.items(), key=lambda kv: kv[1])[:5000]:
                        del self._until[k]
            self._until[ip] = now + seconds


RL = RateLimiter()
BANS = BanList()

# Maktab NAT'i: yuzlab o'quvchi bitta umumiy IP ortidan kirsa, per-IP chegaralar
# butun sinfni bloklab qo'ymasligi uchun DTM_IP_MULT bilan kengaytiriladi
# (masalan, 500 o'quvchili imtihonda DTM_IP_MULT=20). Global (barcha IP birgalikda)
# chegaralarga ta'sir qilmaydi.
IP_MULT = max(1, min(100, int(os.environ.get("DTM_IP_MULT", "1") or 1)))


def _ip() -> str:
    if os.environ.get("DTM_BEHIND_PROXY") or ON_RAILWAY:
        fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if fwd:
            return fwd
    return request.remote_addr or "?"


def _violation(ip: str, what: str):
    """25 ta rad etilgan so'rov / 5 daqiqa -> 15 daqiqa avto-ban."""
    seclog.info("VIOLATION %s ip=%s", what, ip)
    if not RL.allow(f"viol:{ip}", 25, 300):
        BANS.ban(ip, 900)
        seclog.info("BAN ip=%s 900s (%s)", ip, what)
        log.warning("IP banned 15min: %s (%s)", ip, what)


def _limited(bucket: str, limit: int, per_s: float):
    ip = _ip()
    if not RL.allow(f"{bucket}:{ip}", limit * IP_MULT, per_s):
        _violation(ip, bucket)
        abort(429)


def _admin_password() -> str:
    pw = os.environ.get("DTM_ADMIN_PASSWORD")
    if pw:
        return pw
    f = DATA / "admin_parol.txt"
    if not f.exists():
        f.write_text(pysecrets.token_urlsafe(9))
        f.chmod(0o600)
    return f.read_text().strip()


def _admin_code() -> str:
    """Yashirin admin imtihon-kodi: o'quvchi sahifasida shu kod + 'Muhammadullo' ismi
    kiritilsa admin panel ochiladi. 8 belgi (31^8 ~ 852 mlrd kombinatsiya) + start
    endpointining o'z chegarasi bilan terib topish amalda imkonsiz."""
    c = os.environ.get("DTM_ADMIN_CODE")
    if c:
        return c.strip().upper()
    f = DATA / "admin_kirish_kodi.txt"
    if not f.exists():
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        f.write_text("".join(pysecrets.choice(alphabet) for _ in range(8)))
        f.chmod(0o600)
    return f.read_text().strip().upper()


# faqat shu endpointlar parolsiz ochiq (o'quvchi oqimi + kirish)
PUBLIC_ENDPOINTS = {
    "home", "exam_info", "exam_start", "exam_take", "exam_pdf", "exam_save", "exam_submit",
    "exam_result", "exam_result_pdf", "exam_cheat", "exam_status", "admin_login", "static",
}


@app.before_request
def _security_gate():
    ip = _ip()
    if BANS.banned(ip):
        return "Bloklangan. Keyinroq urining.", 403
    if not RL.allow(f"g:{ip}", 400 * IP_MULT, 60):
        _violation(ip, "global")
        abort(429)
    # global yuk tashlash: floodda imtihon topshirayotganlar va admin ishlashda davom etadi
    if not RL.allow("__all__", 2400, 20):
        has_session = session.get("admin") or any(k.startswith("att_") for k in session)
        if not has_session:
            return "Server band. Birozdan so'ng qayta urining.", 503
    # CSRF: begona sahifadan POST -> rad. Brauzerlar cross-site POST'da Origin yuboradi;
    # Origin bo'lmasa (eski brauzer/qirqilgan) Referer zaxira tekshiruv bo'lib xizmat qiladi.
    if request.method == "POST":
        from urllib.parse import urlsplit
        origin = request.headers.get("Origin")
        src = origin or request.headers.get("Referer")
        # "null" — sandbox iframe / data: sahifadan kelgan begona POST (urlsplit netloc'i
        # bo'sh chiqib tekshiruvdan sirg'alib o'tmasin), aniq rad etiladi
        if src and (src.strip().lower() == "null"
                    or urlsplit(src).netloc not in ("", request.host)):
            seclog.info("CSRF-BLOCK ip=%s src=%s path=%s", ip, src, request.path)
            abort(403)
    if request.endpoint and request.endpoint not in PUBLIC_ENDPOINTS:
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
    return None


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                            "script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
                            "frame-ancestors 'self'; form-action 'self'")
    if HTTPS_ON:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.endpoint == "static":
        resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
    elif request.endpoint in ("exam_pdf", "exam_take", "exam_result"):
        resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.errorhandler(429)
def _too_many(_e):
    if request.is_json or request.path.endswith(("/save", "/submit")):
        return jsonify({"ok": False, "error": "Juda ko'p so'rov — biroz kuting."}), 429
    return "<h3 style='font-family:sans-serif'>429 — Juda ko'p so'rov. Biroz kutib qayta urining.</h3>", 429


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        _limited("login", 5, 60)
        # taqsimlangan (ko'p IP'dan) parol terishga qarshi GLOBAL chegara:
        # botnet ham daqiqasiga 30 urinishdan oshira olmaydi
        if not RL.allow("login:__all__", 30, 60):
            seclog.info("VIOLATION login-global ip=%s", _ip())
            abort(429)
        pw = request.form.get("password", "")
        if hmac.compare_digest(pw.encode(), _admin_password().encode()):
            session.clear()
            session["admin"] = True
            session.permanent = True
            seclog.info("LOGIN-OK ip=%s", _ip())
            nxt = request.form.get("next") or url_for("exams_page")
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("exams_page")
            return redirect(nxt)
        seclog.info("LOGIN-FAIL ip=%s", _ip())
        _violation(_ip(), "login-fail")
        flash("Parol noto'g'ri.", "err")
    return render_template("admin_login.html", next=request.args.get("next", ""))


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("home"))


@app.context_processor
def _ctx():
    return {"subject_slots": L.SUBJECT_SLOTS, "subject_choices": S.SUBJECT_CHOICES,
            "pair_presets": S.PAIR_PRESETS, "default_subjects": L.DEFAULT_SUBJECTS,
            "classes": CLASSES}


# ============================================================================
# O'QUVCHI TOMONI
# ============================================================================

def _attempt_or_403(eid, aid):
    att = exams.load_attempt(eid, aid)
    if not att:
        abort(404)
    tok = session.get(f"att_{aid}") or ""
    if not hmac.compare_digest(str(tok), str(att["token"])):
        abort(403)
    return att


def _json_body_cap():
    if request.content_length and request.content_length > 64 * 1024:
        abort(413)


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/exam/info")
def exam_info():
    """Kod kiritilganda imtihon nomi + yo'nalishini ko'rsatadi (tasdiq uchun)."""
    _limited("info", 30, 60)
    meta, did = exams.find_by_code(request.args.get("code", ""))
    if not meta or meta["state"] != "open" or did not in exams.ready_directions(meta):
        return jsonify({"ok": False})
    return jsonify({"ok": True, "title": meta["title"], "duration": meta["duration_min"],
                    "direction": meta["directions"][did]["name"],
                    "scheduled": meta.get("scheduled_at", "")})


@app.route("/exam/start", methods=["POST"])
def exam_start():
    _limited("start", 10, 60)
    code = (request.form.get("code") or "").strip().upper()
    name = request.form.get("student_name", "").strip()
    klass = (request.form.get("klass") or "").strip()
    # yashirin admin yo'li: maxsus kod + aynan "Muhammadullo" ismi
    if hmac.compare_digest(code, _admin_code()) and name == "Muhammadullo":
        session.clear()
        session["admin"] = True
        session.permanent = True
        seclog.info("LOGIN-OK ip=%s (yashirin kod)", _ip())
        return redirect(url_for("exams_page"))
    try:
        att = exams.start_attempt(code, name, klass)
    except ExamError as e:
        flash(str(e), "err")
        return redirect(url_for("home"))
    session[f"att_{att['id']}"] = att["token"]
    session.permanent = True
    return redirect(url_for("exam_take", eid=att["exam_id"], aid=att["id"]))


@app.route("/exam/<eid>/<aid>")
def exam_take(eid, aid):
    att = _attempt_or_403(eid, aid)
    if att["submitted"]:
        return redirect(url_for("exam_result", eid=eid, aid=aid))
    meta = exams.load(eid)
    if att.get("blocked"):
        return render_template("exam_blocked.html", att=att, meta=meta)
    if exams.remaining_s(att) <= 0:
        exams.submit(att)
        return redirect(url_for("exam_result", eid=eid, aid=aid))
    subjects = meta["directions"].get(att.get("direction", ""), {}).get("subjects")
    return render_template("exam_take.html", att=att, meta=meta,
                           remaining_s=exams.remaining_s(att),
                           flags=att.get("flags", []),
                           blocks=L.subject_blocks(subjects))


@app.route("/exam/<eid>/<aid>/pdf")
def exam_pdf(eid, aid):
    att = _attempt_or_403(eid, aid)
    p = exams.pdf_path(eid, att.get("direction", "1"), att["variant"])
    if not p.exists():
        abort(404)
    resp = send_file(p, mimetype="application/pdf", as_attachment=False,
                     download_name="savollar.pdf", conditional=True)
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/exam/<eid>/<aid>/save", methods=["POST"])
def exam_save(eid, aid):
    _limited("save", 30, 60)
    _json_body_cap()
    att = _attempt_or_403(eid, aid)
    data = request.get_json(silent=True) or {}
    try:
        att = exams.save_answers(att, data.get("answers") or {}, data.get("flags"))
    except ExamError as e:
        return jsonify({"ok": False, "error": str(e), "remaining_s": exams.remaining_s(att)}), 409
    return jsonify({"ok": True, "remaining_s": exams.remaining_s(att), "n": len(att["answers"])})


@app.route("/exam/<eid>/<aid>/submit", methods=["POST"])
def exam_submit(eid, aid):
    _limited("submit", 8, 60)
    _json_body_cap()
    att = _attempt_or_403(eid, aid)
    data = request.get_json(silent=True) if request.is_json else None
    try:
        exams.submit(att, (data or {}).get("answers"))
    except ExamError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    if request.is_json:
        return jsonify({"ok": True, "url": url_for("exam_result", eid=eid, aid=aid)})
    return redirect(url_for("exam_result", eid=eid, aid=aid))


@app.route("/exam/<eid>/<aid>/cheat", methods=["POST"])
def exam_cheat(eid, aid):
    """O'quvchi imtihon muhitidan chiqdi — javob saqlanadi, vaqt pauzaga olinadi."""
    _limited("cheat", 20, 60)
    _json_body_cap()
    att = _attempt_or_403(eid, aid)
    data = request.get_json(silent=True) or {}
    exams.block_attempt(att, data.get("answers"), data.get("flags"))
    seclog.info("CHEAT ip=%s exam=%s student=%s marta=%s", _ip(), eid,
                att.get("student_name", "?"), att.get("cheat_count"))
    return jsonify({"ok": True})


@app.route("/exam/<eid>/<aid>/status")
def exam_status(eid, aid):
    _limited("status", 40, 60)          # kutish sahifasi 4 soniyada 1 so'raydi (15/daq)
    att = _attempt_or_403(eid, aid)
    return jsonify({"blocked": bool(att.get("blocked")), "submitted": bool(att.get("submitted")),
                    "remaining_s": exams.remaining_s(att)})


@app.route("/exam/<eid>/<aid>/natija")
def exam_result(eid, aid):
    att = _attempt_or_403(eid, aid)
    if not att["submitted"]:
        return redirect(url_for("exam_take", eid=eid, aid=aid))
    meta = exams.load(eid)
    return render_template("exam_result.html", att=att, rep=att["report"], meta=meta)


@app.route("/exam/<eid>/<aid>/natija.pdf")
def exam_result_pdf(eid, aid):
    att = _attempt_or_403(eid, aid)
    if not att["submitted"]:
        abort(404)
    meta = exams.load(eid)
    p = student_result_pdf(att, meta, DATA / "exams" / eid / "attempts" / f"{aid}.pdf")
    return send_file(p, as_attachment=True,
                     download_name=f"natija_{att.get('student_name','').replace(' ', '_')}.pdf")


# ============================================================================
# ADMIN
# ============================================================================

def _form_subjects() -> dict:
    return {slot: request.form.get(f"subject_{slot}", "") for slot, _, _, _ in L.SUBJECT_SLOTS}


@app.route("/exams", methods=["GET", "POST"])
def exams_page():
    if request.method == "POST":
        try:
            meta = exams.create(request.form.get("title", ""), request.form.get("duration", 180),
                                scheduled_at=request.form.get("scheduled_at", ""))
            flash(f"Umumiy imtihon yaratildi. Endi yo'nalishlarni qo'shing.", "ok")
            return redirect(url_for("exam_manage", eid=meta["id"]))
        except (ExamError, ValueError) as e:
            flash(str(e), "err")
        return redirect(url_for("exams_page"))
    return render_template("exams.html", items=exams.list(),
                           default_duration=S.get_admin()["default_duration"])


@app.route("/exams/<eid>", methods=["GET", "POST"])
def exam_manage(eid):
    meta = exams.load(eid)
    if not meta:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "settings":
                exams.update(eid, request.form.get("title", ""), request.form.get("duration", 180),
                             scheduled_at=request.form.get("scheduled_at", ""))
                flash("Imtihon sozlamalari saqlandi.", "ok")
            elif action == "add_direction":
                exams.add_direction(eid, _form_subjects(), custom_code=request.form.get("code", ""))
                flash("Yo'nalish qo'shildi — endi PDF yuklang va Javoblar sahifasida kalitlarni kiriting.", "ok")
            elif action == "update_direction":
                exams.update_direction(eid, request.form["did"], _form_subjects())
                flash("Yo'nalish fanlari yangilandi.", "ok")
            elif action == "delete_direction":
                exams.delete_direction(eid, request.form["did"])
                flash("Yo'nalish o'chirildi.", "ok")
            elif action == "pdf":
                f = request.files.get("pdf")
                if not f or not f.filename:
                    raise ExamError("PDF faylni tanlang.")
                exams.upload_pdf(eid, request.form["did"], request.form.get("variant", "1"), f.read())
                flash("PDF yuklandi. Endi Javoblar sahifasida shu variant kalitlarini kiriting.", "ok")
            elif action == "delete_variant":
                exams.delete_variant(eid, request.form["did"], request.form["variant"])
                flash("Variant o'chirildi.", "ok")
            elif action == "state":
                exams.set_state(eid, request.form["state"],
                                archived=not S.get_admin()["archive_paused"])
                flash({"open": "Imtihon ochildi — kodni o'quvchilarga bering.",
                       "closed": "Imtihon yakunlandi: tugallanmagan urinishlar avtomatik topshirildi.",
                       "draft": "Qoralamaga qaytarildi."}[request.form["state"]], "ok")
            elif action == "resume":
                att = exams.resume_attempt(eid, request.form["aid"])
                flash(f"{att.get('student_name','')} davom ettirishi mumkin — vaqti pauzadan chiqarildi.", "ok")
            elif action == "delete_attempt":
                exams.delete_attempt(eid, request.form["aid"])
                flash("Urinish o'chirildi.", "ok")
            elif action == "delete_exam":
                exams.delete(eid)
                flash("Imtihon o'chirildi.", "ok")
                return redirect(url_for("exams_page"))
        except (ExamError, KeyError_) as e:
            flash(str(e), "err")
        except KeyError:
            flash("Noto'g'ri so'rov.", "err")
        return redirect(url_for("exam_manage", eid=eid))
    return render_template("exam_manage.html", meta=meta, attempts=exams.attempts(eid),
                           max_resumes=S.get_admin()["max_resumes"])


@app.route("/exams/<eid>/javoblar", methods=["GET", "POST"])
def exam_answers(eid):
    """2-sahifa (daftar chizmasi): javob kalitlari — HAR FANGA ALOHIDA kiritiladi."""
    meta = exams.load(eid)
    if not meta:
        abort(404)
    if request.method == "POST":
        try:
            parts = {slot: request.form.get(f"key_{slot}", "") for slot, _, _, _ in L.SUBJECT_SLOTS}
            exams.set_keys(eid, request.form["did"], request.form.get("variant", "1"), parts)
            flash("Javoblar saqlandi.", "ok")
        except (ExamError, KeyError_) as e:
            flash(str(e), "err")
        except KeyError:
            flash("Noto'g'ri so'rov.", "err")
        return redirect(url_for("exam_answers", eid=eid))
    # har yo'nalish varianti uchun mavjud kalitlarni bloklarga bo'lib beramiz
    key_parts = {}
    for did, d in meta["directions"].items():
        for v, info in d.get("variants", {}).items():
            key_parts[f"{did}:{v}"] = split_key(info.get("key", ""))
    return render_template("exam_answers.html", meta=meta, key_parts=key_parts,
                           sizes=block_sizes())


# ---------- IMTIHON JARAYONI (jonli kuzatuv, faqat ochiq imtihonlar) ----------

def _attempt_row(att: dict) -> dict:
    """Jonli jadval uchun bitta o'quvchining xavfsiz (kalitsiz) holati."""
    return {
        "aid": att["id"], "name": att.get("student_name", ""), "klass": att.get("klass", ""),
        "direction": att.get("direction_name", ""), "variant": int(att.get("variant", "0") or 0),
        "n": len(att.get("answers", {})), "flags": len(att.get("flags", [])),
        "blocked": bool(att.get("blocked")), "submitted": bool(att.get("submitted")),
        "cheat_count": att.get("cheat_count", 0), "resumes_used": att.get("resumes_used", 0),
        "remaining_s": exams.remaining_s(att), "started_at": att.get("started_at", ""),
        "total": (att.get("report") or {}).get("total_points") if att.get("submitted") else None,
        "percent": (att.get("report") or {}).get("percent") if att.get("submitted") else None,
    }


@app.route("/jarayon")
def exam_live():
    items = [m for m in exams.list() if m["state"] == "open"]
    return render_template("exam_live.html", items=items,
                           max_resumes=S.get_admin()["max_resumes"])


@app.route("/jarayon/<eid>/data")
def exam_live_data(eid):
    meta = exams.load(eid)
    if not meta or meta["state"] != "open":
        return jsonify({"ok": False, "closed": True})
    rows = [_attempt_row(a) for a in exams.attempts(eid)]
    # tartib: to'xtatilganlar tepada, keyin ishlayotganlar, oxirida yakunlaganlar
    rows.sort(key=lambda r: (0 if r["blocked"] else (1 if not r["submitted"] else 2), r["name"].lower()))
    return jsonify({"ok": True, "rows": rows})


@app.route("/jarayon/<eid>/<aid>/resume", methods=["POST"])
def exam_live_resume(eid, aid):
    try:
        att = exams.resume_attempt(eid, aid)
    except ExamError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "resumes_used": att.get("resumes_used", 0)})


@app.route("/jarayon/<eid>/<aid>")
def exam_spectate(eid, aid):
    att = exams.load_attempt(eid, aid)
    if not att:
        abort(404)
    meta = exams.load(eid)
    subjects = meta["directions"].get(att.get("direction", ""), {}).get("subjects")
    return render_template("exam_spectate.html", att=att, meta=meta,
                           blocks=L.subject_blocks(subjects),
                           max_resumes=S.get_admin()["max_resumes"])


@app.route("/jarayon/<eid>/<aid>/data")
def exam_spectate_data(eid, aid):
    att = exams.load_attempt(eid, aid)
    if not att:
        abort(404)
    return jsonify({"answers": att.get("answers", {}), "flags": att.get("flags", []),
                    "blocked": bool(att.get("blocked")), "submitted": bool(att.get("submitted")),
                    "remaining_s": exams.remaining_s(att),
                    "cheat_count": att.get("cheat_count", 0),
                    "resumes_used": att.get("resumes_used", 0),
                    "n": len(att.get("answers", {})),
                    "total": (att.get("report") or {}).get("total_points") if att.get("submitted") else None})


# ---------- ARXIV (yakunlangan imtihonlar) ----------

@app.route("/arxiv")
def archive_page():
    from omr_core.exam import _sorted_done
    items = []
    for m in exams.list():
        if m["state"] == "closed" and m.get("archived", True):
            items.append({"meta": m, "attempts": _sorted_done(exams.attempts(m["id"]))})
    return render_template("archive.html", items=items)


# ---------- SOZLAMALAR ----------

@app.route("/sozlamalar", methods=["GET", "POST"])
def admin_settings():
    if request.method == "POST":
        S.set_admin({"archive_paused": request.form.get("archive_paused") == "1",
                     "default_duration": request.form.get("default_duration", 180),
                     "max_resumes": request.form.get("max_resumes", 2)})
        flash("Sozlamalar saqlandi.", "ok")
        return redirect(url_for("admin_settings"))
    return render_template("settings.html", s=S.get_admin())


@app.route("/exams/<eid>/attempt/<aid>")
def exam_attempt_admin(eid, aid):
    att = exams.load_attempt(eid, aid)
    if not att or not att.get("submitted"):
        abort(404)
    meta = exams.load(eid)
    return render_template("exam_result.html", att=att, rep=att["report"], meta=meta, admin_view=True)


@app.route("/exams/<eid>/attempt/<aid>.pdf")
def exam_attempt_admin_pdf(eid, aid):
    att = exams.load_attempt(eid, aid)
    if not att or not att.get("submitted"):
        abort(404)
    meta = exams.load(eid)
    p = student_result_pdf(att, meta, DATA / "exams" / eid / "attempts" / f"{aid}.pdf")
    return send_file(p, as_attachment=True,
                     download_name=f"natija_{att.get('student_name','').replace(' ', '_')}.pdf")


@app.route("/exams/<eid>/d<did>_v<variant>.pdf")
def exam_admin_pdf(eid, did, variant):
    if not (did.isdigit() and variant.isdigit()):
        abort(404)
    try:
        p = exams.pdf_path(eid, did, variant)
    except KeyError_:
        abort(404)
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="application/pdf")


@app.route("/exams/<eid>/export.xlsx")
def exam_export(eid):
    meta = exams.load(eid)
    if not meta:
        abort(404)
    atts = exams.attempts(eid)
    if not any(a.get("submitted") for a in atts):
        flash("Hali yakunlangan urinish yo'q.", "err")
        return redirect(url_for("exam_manage", eid=eid))
    p = export_exam_excel(meta, atts, DATA / "exams" / eid / "natijalar.xlsx")
    return send_file(p, as_attachment=True, download_name=f"imtihon_{eid}_natijalar.xlsx")


@app.route("/exams/<eid>/hisobot.pdf")
def exam_report_pdf(eid):
    meta = exams.load(eid)
    if not meta:
        abort(404)
    atts = exams.attempts(eid)
    if not any(a.get("submitted") for a in atts):
        flash("Hali yakunlangan urinish yo'q.", "err")
        return redirect(url_for("exam_manage", eid=eid))
    p = export_exam_pdf(meta, atts, DATA / "exams" / eid / "hisobot.pdf")
    return send_file(p, as_attachment=True, download_name=f"imtihon_{eid}_hisobot.pdf")


if __name__ == "__main__":
    _admin_password()
    _admin_code()
    # Railway'da tashqi proxy'ga ochiq turishi kerak; oddiy serverda faqat nginx orqali
    host = os.environ.get("HOST", "0.0.0.0" if ON_RAILWAY else "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    log.info("Admin parol fayli: %s (yoki DTM_ADMIN_PASSWORD env)", DATA / "admin_parol.txt")
    log.info("Yashirin admin-kod fayli: %s (yoki DTM_ADMIN_CODE env)", DATA / "admin_kirish_kodi.txt")
    try:
        os.chmod(DATA, 0o700)
    except OSError:
        pass
    try:
        from waitress import serve
        kwargs = dict(threads=16, connection_limit=300, channel_timeout=30,
                      max_request_header_size=16384,
                      max_request_body_size=70 * 1024 * 1024, ident=None)
        if os.environ.get("DTM_BEHIND_PROXY") or ON_RAILWAY:
            # waitress 2+ X-Forwarded-* ni sukut bo'yicha O'CHIRIB tashlaydi — proxy
            # (nginx/Railway) ortida ularga ishonmasak, hamma o'quvchi bitta IP bo'lib
            # ko'rinadi va per-IP limitlar butun maktabni birga bo'g'adi.
            kwargs.update(trusted_proxy="*", trusted_proxy_count=1,
                          trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto",
                                                 "x-forwarded-host", "x-forwarded-port"})
        log.info("waitress production server: http://%s:%s", host, port)
        serve(app, host=host, port=port, **kwargs)
    except ImportError:
        log.warning("waitress topilmadi — Flask dev server (productionga yaroqsiz)")
        app.run(host=host, port=port, debug=False)
