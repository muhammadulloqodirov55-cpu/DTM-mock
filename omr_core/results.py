"""Scan-natija ma'lumot tuzilmalari (yengil modul).

QuestionResult/ScanResult shu yerda turadi, chunki ularni webapp (onlayn imtihon)
ham ishlatadi — u OpenCV/NumPy'siz ishlashi kerak. scanner.py bularni qayta
eksport qiladi, eski importlar buzilmaydi.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class QuestionResult:
    q: int
    answer: Optional[str]
    status: str                    # ok | blank | multiple | review
    fills: dict = field(default_factory=dict)
    note: str = ""


@dataclass
class ScanResult:
    questions: list[QuestionResult]
    student_id: Optional[str]
    student_id_status: str
    variant: Optional[str]
    variant_status: str
    markers_found: list[int]
    warnings: list[str]
    blur_score: float
    template: str = ""
    fields: dict = field(default_factory=dict)      # raw digit fields: name -> {value, status}
    template_fit: Optional[float] = None            # bubbles-on-printed-circles score (frame templates)
    canon_image_path: Optional[str] = None
    annotated_image_path: Optional[str] = None

    def answers_string(self) -> str:
        return "".join(q.answer or "-" for q in self.questions)

    def flagged(self) -> list[QuestionResult]:
        return [q for q in self.questions if q.status in ("multiple", "review")]

    def to_dict(self) -> dict:
        return asdict(self)
