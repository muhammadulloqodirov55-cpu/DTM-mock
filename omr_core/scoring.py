"""DTM scoring with exact decimal arithmetic.

Official 2026/2027 DTM weights (Vazirlar Mahkamasi / DTM):
    1-30   majburiy fanlar  (1-10 Ona tili, 11-20 Matematika, 21-30 Tarix)  1.1 ball
    31-60  1-fan (asosiy)                                                   3.1 ball
    61-90  2-fan                                                            2.1 ball
    Maksimal: 30*1.1 + 30*3.1 + 30*2.1 = 33 + 93 + 63 = 189.0
The blocks/weights are defined once in layout.SUBJECT_BLOCKS.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Optional

from . import layout as L
from .results import ScanResult, QuestionResult


@dataclass
class QScore:
    q: int
    subject: str
    given: Optional[str]
    correct: str            # key char
    status: str             # scanner status (ok/blank/multiple/review) or 'override'
    is_correct: bool
    points: str             # Decimal as string, e.g. "3.1"
    note: str = ""


@dataclass
class SubjectScore:
    name: str
    q_from: int
    q_to: int
    per_question: str
    total_questions: int
    correct: int
    wrong: int
    blank: int
    invalid: int            # multiple / review-without-answer
    points: str
    max_points: str


@dataclass
class Report:
    student_id: Optional[str]
    variant: Optional[str]
    key_found: bool
    questions: list[QScore]
    subjects: list[SubjectScore]
    total_points: str
    max_points: str
    percent: str
    correct: int
    wrong: int
    blank: int
    invalid: int
    flagged: list[int]
    warnings: list[str] = field(default_factory=list)
    student_name: str = ""
    image_name: str = ""
    scanned_at: str = ""
    annotated_image_path: Optional[str] = None
    answers_string: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Report":
        d = dict(d)
        d["questions"] = [QScore(**q) for q in d["questions"]]
        d["subjects"] = [SubjectScore(**s) for s in d["subjects"]]
        return cls(**d)


def max_total() -> Decimal:
    return sum((Decimal(p) * (b - a + 1) for a, b, _, p in L.SUBJECT_BLOCKS), Decimal(0))


def _subject_of(q: int, blocks) -> tuple[int, str, Decimal]:
    """Block index, subject name and points for a question. Index keys the per-block
    tallies so two blocks may share one subject name without colliding."""
    for i, (a, b, name, p) in enumerate(blocks):
        if a <= q <= b:
            return i, name, Decimal(p)
    raise ValueError(q)


def score(scan: ScanResult, key: Optional[str], overrides: dict[int, Optional[str]] | None = None,
          subjects: dict[str, str] | None = None) -> Report:
    """key: 90-char string (see keys.py) or None. overrides: {q: 'A'|None} from human review.
    subjects: configured names for the 5 blocks ({m1,m2,m3,fan1,fan2}, see omr_core.settings)."""
    overrides = overrides or {}
    blocks = L.subject_blocks(subjects)
    qscores: list[QScore] = []
    per_subject: dict[int, dict] = {}
    for i, (a, b, name, p) in enumerate(blocks):
        per_subject[i] = dict(name=name, q_from=a, q_to=b, per_question=p, total_questions=b - a + 1,
                              correct=0, wrong=0, blank=0, invalid=0, points=Decimal(0),
                              max_points=Decimal(p) * (b - a + 1))

    for qr in scan.questions:
        q = qr.q
        bi, subj, pts = _subject_of(q, blocks)
        kchar = key[q - 1] if key else "-"
        given, status, note = qr.answer, qr.status, qr.note
        if q in overrides:
            given, status, note = overrides[q], "override", "Qo'lda tuzatildi"
        S = per_subject[bi]
        is_correct = False
        earned = Decimal(0)
        if kchar == "-":
            note = (note + "; " if note else "") + "Kalitda yo'q (o'tkazib yuborildi)"
            S["blank"] += 1
        elif kchar == "*":
            is_correct, earned = True, pts
            S["correct"] += 1
            note = (note + "; " if note else "") + "Savol bekor qilingan — hammaga ball"
        elif status == "multiple":
            S["invalid"] += 1
        elif given is None:
            if status == "review":
                S["invalid"] += 1
            else:
                S["blank"] += 1
        elif given == kchar:
            is_correct, earned = True, pts
            S["correct"] += 1
        else:
            S["wrong"] += 1
        S["points"] += earned
        qscores.append(QScore(q=q, subject=subj, given=given, correct=kchar, status=status,
                              is_correct=is_correct, points=str(earned), note=note))

    subject_scores = []
    tot = Decimal(0)
    for S in per_subject.values():
        tot += S["points"]
        subject_scores.append(SubjectScore(**{**S, "points": str(S["points"]), "max_points": str(S["max_points"])}))
    mx = max_total()
    pct = (tot / mx * 100).quantize(Decimal("0.1")) if mx else Decimal(0)
    return Report(
        student_id=scan.student_id, variant=scan.variant, key_found=key is not None,
        questions=qscores, subjects=subject_scores,
        total_points=str(tot), max_points=str(mx), percent=str(pct),
        correct=sum(s.correct for s in subject_scores), wrong=sum(s.wrong for s in subject_scores),
        blank=sum(s.blank for s in subject_scores), invalid=sum(s.invalid for s in subject_scores),
        flagged=[q.q for q in scan.flagged() if q.q not in overrides],
        warnings=list(scan.warnings), annotated_image_path=scan.annotated_image_path,
        answers_string="".join((qs.given or "-") for qs in qscores),
    )
