"""Pydantic API schemas + ExtractionResult (also used as the Claude structured-output schema)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


@dataclass
class ExtractedContent:
    """Output of a file extractor: text (with ``[Page n]`` markers for paged docs) and PNG images
    as ``(png_bytes, page_no_or_None)``."""

    text: str = ""
    images: list[tuple[bytes, int | None]] = field(default_factory=list)
    pages: int = 0
    meta: dict = field(default_factory=dict)


class ExtractedCondition(BaseModel):
    name: Optional[str] = None
    diagnosed_date: Optional[str] = None  # YYYY-MM-DD
    severity: Optional[str] = None
    status: Optional[str] = None  # active|resolved
    page: Optional[int] = None


class ExtractedMedication(BaseModel):
    drug_name: Optional[str] = None
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    route: Optional[str] = None
    duration: Optional[str] = None
    instructions: Optional[str] = None
    start_date: Optional[str] = None
    page: Optional[int] = None


class ExtractedLab(BaseModel):
    test_name: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None
    ref_range: Optional[str] = None
    flag: Optional[str] = None  # normal|high|low
    date: Optional[str] = None
    page: Optional[int] = None


class ExtractedVital(BaseModel):
    kind: Optional[str] = None  # bp|weight|glucose|pulse|spo2|temp
    value: Optional[str] = None
    unit: Optional[str] = None
    date: Optional[str] = None
    page: Optional[int] = None


class ExtractedEncounter(BaseModel):
    type: Optional[str] = None  # opd|ipd|diagnostic|pharmacy
    date: Optional[str] = None
    end_date: Optional[str] = None
    reason: Optional[str] = None
    diagnosis: Optional[str] = None
    notes: Optional[str] = None
    advice: Optional[str] = None
    follow_up_date: Optional[str] = None
    fee: Optional[float] = None
    page: Optional[int] = None


class ExtractionResult(BaseModel):
    doc_type: str = "other"
    document_date: Optional[str] = None
    facility: Optional[str] = None
    doctor_name: Optional[str] = None
    specialization: Optional[str] = None
    summary: Optional[str] = None
    conditions: list[ExtractedCondition] = Field(default_factory=list)
    medications: list[ExtractedMedication] = Field(default_factory=list)
    lab_results: list[ExtractedLab] = Field(default_factory=list)
    vitals: list[ExtractedVital] = Field(default_factory=list)
    encounter: Optional[ExtractedEncounter] = None
    confidence: float = 0.5
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Doctor Q&A
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    document_id: int
    filename: str
    page: Optional[int] = None
    snippet: Optional[str] = None


class ToolCallInfo(BaseModel):
    name: str
    args: dict = Field(default_factory=dict)
    row_count: int = 0


MAX_HISTORY_ITEMS = 200  # accepted at the schema level; trimmed to MAX_HISTORY_TURNS before use
MAX_HISTORY_TURNS = 20
MAX_HISTORY_CHARS = 8000


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str  # any length; truncated to MAX_HISTORY_CHARS before use


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=MAX_HISTORY_ITEMS)

    def trimmed_history(self) -> list[dict]:
        """The last ``MAX_HISTORY_TURNS`` turns, each truncated to ``MAX_HISTORY_CHARS`` characters."""
        return [{"role": h.role, "content": h.content[:MAX_HISTORY_CHARS]}
                for h in self.history[-MAX_HISTORY_TURNS:]]


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    tool_calls: list[ToolCallInfo] = Field(default_factory=list)
    mode: str
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Auth / profile / consent request bodies
# ---------------------------------------------------------------------------


# Max lengths mirror the String(n) columns in app.models (Text columns are unbounded).
class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=6, max_length=72)
    full_name: str = Field(min_length=1, max_length=255)
    role: Literal["patient", "doctor"] = "patient"
    # patient-only fields
    dob: Optional[date] = None
    sex: Optional[str] = Field(default=None, max_length=16)
    phone: Optional[str] = Field(default=None, max_length=32)
    # doctor-only fields
    license_no: Optional[str] = Field(default=None, max_length=64)
    specialization: Optional[str] = Field(default=None, max_length=128)
    qualification: Optional[str] = Field(default=None, max_length=128)
    facility: Optional[str] = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: Optional[str] = Field(default=None, max_length=255)
    dob: Optional[date] = None
    sex: Optional[str] = Field(default=None, max_length=16)
    phone: Optional[str] = Field(default=None, max_length=32)
    address: Optional[str] = None
    occupation: Optional[str] = Field(default=None, max_length=128)
    blood_group: Optional[str] = Field(default=None, max_length=8)
    height_cm: Optional[float] = Field(default=None, gt=0, lt=300)
    weight_kg: Optional[float] = Field(default=None, gt=0, lt=500)
    allergies: Optional[list[str]] = None
    hereditary_history: Optional[list[dict]] = None
    insurance: Optional[dict] = None
    family_members: Optional[list[dict]] = None
    emergency_contact: Optional[str] = Field(default=None, max_length=255)


class GrantCreate(BaseModel):
    access_minutes: Literal[60, 1440, 10080] = 60
    scope: Literal["read", "read_write"] = "read"


class RedeemRequest(BaseModel):
    dhid: str
    otp: str
