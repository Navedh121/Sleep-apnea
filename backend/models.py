# models.py — Pydantic schemas that mirror the wire contract exactly.
#
# Pydantic automatically validates incoming JSON against these models.
# If a field is the wrong type or is missing, FastAPI returns a 422 error
# before our code even runs — free validation.
#
# Field names MUST match spec §3.3 exactly (lowercase snake_case).

from typing import Optional, List
from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Ingestion models (device → backend)
# ---------------------------------------------------------------------------

class LiveSample(BaseModel):
    """
    Body of POST /reading — one sample pushed in real time (spec §3.1).
    All three numeric fields are integers as required by spec §3.4 rule 2.
    """
    session_id: int   # the night's number; must be ≥ 1
    t:          int   # milliseconds since session start
    spo2:       int   # blood-oxygen saturation 0–100
    hr:         int   # heart rate 30–180 (out-of-range → clamped/re-flagged in backend)
    flag:       str   # exactly "ok" or "invalid" (enforced in main.py guard A2)

    @field_validator("session_id")
    @classmethod
    def session_id_positive(cls, v: int) -> int:
        # spec §3.3: session_id is int ≥ 1
        if v < 1:
            raise ValueError("session_id must be ≥ 1")
        return v

    @field_validator("t")
    @classmethod
    def t_non_negative(cls, v: int) -> int:
        # t is elapsed ms — can't be negative
        if v < 0:
            raise ValueError("t must be ≥ 0")
        return v


# ---------------------------------------------------------------------------
# Response models (backend → browser)
# ---------------------------------------------------------------------------

class NightRow(BaseModel):
    """
    One row returned by GET /nights — the summary list on the Logs page.
    """
    session_id:       int
    received_date:    str
    band:             str
    duration_s:       int
    insufficient:     bool


class HourlyBucket(BaseModel):
    """One element of the hourly[] array in the night summary (spec §5)."""
    hour:     int
    events:   int
    spo2_min: int


class EventEntry(BaseModel):
    """One detected desaturation event (spec §5 event_list)."""
    start_t:    int    # ms since session start
    end_t:      int
    nadir_spo2: int    # lowest SpO2 during this event
    drop:       int    # % drop below baseline
    duration_s: int    # length of the event in seconds


class NightSummary(BaseModel):
    """
    Full night summary returned by GET /nights/{id}/summary (spec §5).
    All field names match spec exactly — do not rename.
    """
    session_id:         int
    received_date:      str
    duration_s:         int
    valid_duration_s:   int
    sample_count:       int
    valid_sample_count: int
    spo2_baseline:      Optional[int]
    spo2_min:           Optional[int]
    spo2_mean:          Optional[float]
    time_below_90_s:    Optional[int]
    time_below_88_s:    Optional[int]
    events:             Optional[int]
    odi:                Optional[float]
    band:               str
    insufficient:       bool
    hourly:             List[HourlyBucket]
    event_list:         List[EventEntry]


class VerdictResponse(BaseModel):
    """
    Returned by GET /nights/{id}/verdict (spec §2).
    rf_index and rf_confidence are null until the ML model is trained (Phase E).
    """
    band:          str
    insufficient:  bool
    rf_index:      Optional[float]
    rf_confidence: Optional[float]


class SamplePoint(BaseModel):
    """One data point returned by GET /nights/{id}/samples."""
    t:    int
    spo2: int
    hr:   int
    flag: str


class LiveActiveResponse(BaseModel):
    """Returned by GET /live/active."""
    session_id: Optional[int]   # null if no active session


class ChatRequest(BaseModel):
    """Body of POST /nights/{id}/chat."""
    question: str


class ChatResponse(BaseModel):
    """Response from POST /nights/{id}/chat."""
    answer: str
