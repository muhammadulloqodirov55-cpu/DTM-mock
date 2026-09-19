"""Sheet geometry (all units in millimetres on an A4 page).

The SAME numbers are used both to draw the printable sheet and to locate
bubbles when scanning, so the printed sheet and the scanner can never drift
apart. Change a number here -> regenerate the sheet PDF -> scanner follows.

Coordinate system: origin = top-left corner of the A4 page, x -> right, y -> down.
"""
from __future__ import annotations

PAGE_W = 210.0
PAGE_H = 297.0

# ---- fiducial markers (ArUco DICT_4X4_50, ids 0..3) ----------------------
MARKER_SIZE = 14.0            # mm, corner markers (black square incl. border)
MID_MARKER_SIZE = 12.0        # mm, the two mid-height side markers
MARKER_MARGIN = 8.0           # mm from page edge to marker edge
# 4 corners + 2 mid-height markers. The mid ones let the scanner correct a
# sheet that is bent/curved (very common with phone photos) by using two
# separate perspective transforms for the top and bottom halves.
MARKER_IDS = {"TL": 0, "TR": 1, "BR": 2, "BL": 3, "ML": 4, "MR": 5}
TOP_HALF_MARKERS = ("TL", "TR", "MR", "ML")
BOTTOM_HALF_MARKERS = ("ML", "MR", "BR", "BL")

# ---- question grid ---------------------------------------------------------
N_QUESTIONS = 90
N_OPTIONS = 4
OPTION_LABELS = ["A", "B", "C", "D"]
QUESTIONS_PER_COLUMN = 30
COLUMN_X0 = [24.0, 86.0, 148.0]        # left x of each of the 3 columns
QUESTION_Y0 = 104.0                    # centre y of the first row
ROW_PITCH = 5.5                        # mm between rows
BUBBLE_R = 2.0                         # bubble radius (mm)
OPTION_DX = 12.0                       # offset from column x0 to option A centre
OPTION_PITCH = 8.0                     # mm between A/B/C/D centres
NUMBER_DX = 0.0                        # where the question number text starts

# ---- student ID / variant digit grids --------------------------------------
ID_DIGITS = 6
VARIANT_DIGITS = 2
DIGIT_ROWS = 10                        # 0..9
DIGIT_ROW_PITCH = 5.0
DIGIT_COL_PITCH = 6.5
ID_X0 = 118.0                          # centre x of first ID column
ID_Y0 = 44.0                           # centre y of digit row "0"
VARIANT_X0 = ID_X0 + ID_DIGITS * DIGIT_COL_PITCH + 10.0
DIGIT_R = 1.9

# ---- subject blocks & DTM scoring --------------------------------------------
# The block STRUCTURE (question ranges + weights) is fixed DTM policy:
#   1-10, 11-20, 21-30  mandatory subjects, 1.1 points each
#   31-60               main subject (fan 1), 3.1 points each
#   61-90               second subject (fan 2), 2.1 points each
# The subject NAMES are configurable (data/settings.json, see omr_core.settings).
# (slot_key, start_q, end_q inclusive, points per correct answer as string)
SUBJECT_SLOTS = [
    ("m1", 1, 10, "1.1"),
    ("m2", 11, 20, "1.1"),
    ("m3", 21, 30, "1.1"),
    ("fan1", 31, 60, "3.1"),
    ("fan2", 61, 90, "2.1"),
]
DEFAULT_SUBJECTS = {
    "m1": "Ona tili",
    "m2": "Matematika",
    "m3": "O'zbekiston tarixi",
    "fan1": "Fan 1 (asosiy)",
    "fan2": "Fan 2 (ikkinchi)",
}


def subject_blocks(names: dict | None = None) -> list[tuple[int, int, str, str]]:
    """(start_q, end_q, subject name, points) for the 5 blocks, with configured names."""
    n = {**DEFAULT_SUBJECTS, **{k: v for k, v in (names or {}).items() if v}}
    return [(a, b, n[slot], pts) for slot, a, b, pts in SUBJECT_SLOTS]


SUBJECT_BLOCKS = subject_blocks()    # default-named blocks (tests, printable sheet text)


def marker_rects() -> dict[str, tuple[float, float, float, float]]:
    """Return {name: (x, y, w, h)} in mm for the 4 corner markers."""
    s, m, ms = MARKER_SIZE, MARKER_MARGIN, MID_MARKER_SIZE
    ym = PAGE_H / 2 - ms / 2
    return {
        "TL": (m, m, s, s),
        "TR": (PAGE_W - m - s, m, s, s),
        "BR": (PAGE_W - m - s, PAGE_H - m - s, s, s),
        "BL": (m, PAGE_H - m - s, s, s),
        "ML": (m, ym, ms, ms),
        "MR": (PAGE_W - m - ms, ym, ms, ms),
    }


def marker_corners_mm() -> dict[int, list[tuple[float, float]]]:
    """ArUco corner order: TL, TR, BR, BL of the marker itself."""
    out = {}
    for name, (x, y, w, h) in marker_rects().items():
        out[MARKER_IDS[name]] = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return out


def question_bubbles() -> list[dict]:
    """List of {q, option, cx, cy, r} for all 90x4 bubbles."""
    out = []
    for q in range(1, N_QUESTIONS + 1):
        col = (q - 1) // QUESTIONS_PER_COLUMN
        row = (q - 1) % QUESTIONS_PER_COLUMN
        cy = QUESTION_Y0 + row * ROW_PITCH
        for i, label in enumerate(OPTION_LABELS):
            cx = COLUMN_X0[col] + OPTION_DX + i * OPTION_PITCH
            out.append({"q": q, "option": label, "cx": cx, "cy": cy, "r": BUBBLE_R})
    return out


def digit_bubbles(kind: str) -> list[dict]:
    """kind = 'id' | 'variant'. Returns {kind, col, digit, cx, cy, r}."""
    if kind == "id":
        n, x0 = ID_DIGITS, ID_X0
    elif kind == "variant":
        n, x0 = VARIANT_DIGITS, VARIANT_X0
    else:
        raise ValueError(kind)
    out = []
    for c in range(n):
        for d in range(DIGIT_ROWS):
            out.append({
                "kind": kind, "col": c, "digit": d,
                "cx": x0 + c * DIGIT_COL_PITCH,
                "cy": ID_Y0 + d * DIGIT_ROW_PITCH,
                "r": DIGIT_R,
            })
    return out


def custom_template():
    """Template object (see template.py) for our own ArUco sheet."""
    from .template import Template, Bubble
    W, H = PAGE_W, PAGE_H
    qs = {f"{b['q']}:{b['option']}": Bubble(b["cx"] / W, b["cy"] / H, b["r"] / W) for b in question_bubbles()}
    fields = {}
    for kind in ("id", "variant"):
        fields[kind] = [{"col": b["col"], "digit": b["digit"], "u": b["cx"] / W, "v": b["cy"] / H, "r": b["r"] / W}
                        for b in digit_bubbles(kind)]
    return Template(
        name="custom_aruco", registration="aruco", aspect=H / W, ink_channel="minRG", refine=False,
        n_questions=N_QUESTIONS, options=list(OPTION_LABELS), questions=qs, digit_fields=fields,
        semantics={"variant": ["variant", 0, VARIANT_DIGITS], "student_id": ["id", 0, ID_DIGITS]},
        marker_corners={str(k): [[x / W, y / H] for x, y in v] for k, v in marker_corners_mm().items()},
        notes="Own printable sheet with 6 ArUco markers (generated by omr_core.sheet).",
    )


def build_template() -> dict:
    """Legacy dict description of the sheet (kept for template.json in data/sheet)."""
    return {
        "version": 1,
        "page_mm": [PAGE_W, PAGE_H],
        "markers": {str(k): v for k, v in marker_corners_mm().items()},
        "questions": question_bubbles(),
        "id_digits": digit_bubbles("id"),
        "variant_digits": digit_bubbles("variant"),
        "n_questions": N_QUESTIONS,
        "options": OPTION_LABELS,
        "subject_blocks": [
            {"from": a, "to": b, "name": n, "points": p} for a, b, n, p in SUBJECT_BLOCKS
        ],
    }
