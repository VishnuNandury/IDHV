"""Deterministic test doubles shared by all backend test modules (Tasks 1-3).

Import as ``from tests.conftest import FakeProvider`` or ``from tests.fakes import FakeProvider``.
"""
from __future__ import annotations

from app.schemas import (
    AskResponse,
    ExtractedContent,
    ExtractedEncounter,
    ExtractedLab,
    ExtractedMedication,
    ExtractedVital,
    ExtractionResult,
)


def default_extraction() -> ExtractionResult:
    return ExtractionResult(
        doc_type="lab_report",
        document_date="2025-03-12",
        facility="Fake Diagnostics",
        doctor_name="Dr. Fake",
        specialization="Pathology",
        summary="Synthetic lab report produced by FakeProvider.",
        lab_results=[
            ExtractedLab(test_name="HbA1c", value="7.8", unit="%", ref_range="4.0-5.6", flag="high",
                         date="2025-03-12", page=1),
        ],
        vitals=[ExtractedVital(kind="bp", value="140/90", unit="mmHg", date="2025-03-12", page=1)],
        medications=[ExtractedMedication(drug_name="Metformin", dosage="500 mg", frequency="BD",
                                         route="oral", start_date="2025-03-12", page=1)],
        encounter=ExtractedEncounter(type="diagnostic", date="2025-03-12", reason="Routine labs", fee=600.0),
        confidence=0.9,
        warnings=[],
    )


class FakeProvider:
    """AIProvider double. Configure ``result`` (returned by digitise), ``raise_exc`` (raised by
    digitise and answer when set) and ``answer_response``. Every call is recorded in ``calls``."""

    def __init__(self, name: str = "fake", result: ExtractionResult | None = None,
                 raise_exc: BaseException | None = None, answer_response: AskResponse | None = None):
        self.name = name
        self.result = result if result is not None else default_extraction()
        self.raise_exc = raise_exc
        self.answer_response = answer_response
        self.calls: list[tuple] = []

    def digitise(self, content: ExtractedContent, filename: str) -> ExtractionResult:
        self.calls.append(("digitise", filename, content))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result.model_copy(deep=True)

    def answer(self, db, patient_id: int, question: str, history: list[dict]) -> AskResponse:
        self.calls.append(("answer", patient_id, question, history))
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.answer_response is not None:
            return self.answer_response.model_copy(deep=True)
        return AskResponse(answer=f"Fake answer to: {question}", citations=[], tool_calls=[], mode=self.name)
