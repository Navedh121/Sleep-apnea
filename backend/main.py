# main.py — FastAPI application entry point.
#
# Phase B1: two ingestion endpoints only.
#   POST /reading  — one live sample
#   POST /night    — full CSV upload (the SD-card file)
#
# Guards enforced here in B1:
#   A2  — flag must be "ok" or "invalid"
#   A3  — /night body is CSV, not JSON
#   A4  — CSV must start with header "t,spo2,hr,flag"
#   A5  — first /reading for a new session creates a stub row in nights
#   A6  — re-uploading a finalized night returns 409
#
# Guards A1 (t-unit check) is also done in B1 for /night uploads.
# For /reading it needs a buffer; that buffer is set up here too.

import csv
import io
import statistics
from collections import defaultdict
from datetime import date
from typing import List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.config import (
    A1_GAP_MIN_MS, A1_GAP_MAX_MS, A1_LIVE_BUFFER_SIZE
)
from backend.database import init_db, get_connection
from backend.models import LiveSample

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SpO2 Sleep-Apnea Screening Monitor",
    description="Laptop backend for the ESP32 SpO2 sensor (spec v1.1)",
    version="0.1.0-B1",
)

# In-memory buffer: stores the last N t-values per session for A1 live check.
# Key = session_id (int), Value = list of recent t values.
_live_t_buffer: dict[int, list[int]] = defaultdict(list)


@app.on_event("startup")
def on_startup() -> None:
    """Create tables on first run (safe to call every time)."""
    init_db()
    print("Database initialised. Server ready.")


# ---------------------------------------------------------------------------
# Helper: A2 flag validation
# ---------------------------------------------------------------------------

def _validate_flag(flag: str, context: str = "") -> None:
    """
    Guard A2: reject any flag value that isn't exactly "ok" or "invalid".
    context is a human-readable hint (e.g. "row t=1000") for error messages.
    """
    if flag not in ("ok", "invalid"):
        where = f" (at {context})" if context else ""
        raise HTTPException(
            status_code=400,
            detail=f"unknown flag value '{flag}'{where} — must be 'ok' or 'invalid'"
        )


# ---------------------------------------------------------------------------
# Helper: A1 gap check on a list of t values
# ---------------------------------------------------------------------------

def _check_t_units(t_values: list[int]) -> None:
    """
    Guard A1: compute the median gap between consecutive t values.
    If it falls outside 500–2000 ms the firmware is probably sending seconds
    or a sample counter instead of milliseconds.
    Needs at least 2 values to compute a gap.
    """
    if len(t_values) < 2:
        return   # not enough data yet

    # Sort first — just in case samples arrived slightly out of order
    sorted_t = sorted(t_values)
    gaps = [sorted_t[i + 1] - sorted_t[i] for i in range(len(sorted_t) - 1)]

    # Filter out zero gaps (duplicate t values) before computing median
    non_zero_gaps = [g for g in gaps if g > 0]
    if not non_zero_gaps:
        return

    median_gap = statistics.median(non_zero_gaps)

    if not (A1_GAP_MIN_MS <= median_gap <= A1_GAP_MAX_MS):
        raise HTTPException(
            status_code=400,
            detail=(
                f"t units look wrong — expected milliseconds at ~1 Hz "
                f"(median gap {median_gap:.0f} ms, expected 500–2000 ms). "
                f"Did the device send seconds or a sample counter instead of ms?"
            )
        )


# ---------------------------------------------------------------------------
# Helper: out-of-range clamping (spec §3.4 rule 8)
# ---------------------------------------------------------------------------

def _sanitise_sample(spo2: int, hr: int, flag: str) -> tuple[int, int, str]:
    """
    Guard A9 / spec rule 8: if spo2 or hr are outside valid range, re-mark
    the sample as invalid rather than crashing. The spec says: "the backend
    re-marks that sample flag:'invalid' rather than rejecting the whole upload."
    """
    if spo2 < 0 or spo2 > 100:
        flag = "invalid"
    if hr < 30 or hr > 180:
        flag = "invalid"
    return spo2, hr, flag


# ---------------------------------------------------------------------------
# Helper: upsert a stub night row (A5)
# ---------------------------------------------------------------------------

def _ensure_night_stub(session_id: int, conn) -> None:
    """
    Guard A5: make sure a row exists in 'nights' for this session_id so that
    foreign-key constraints on 'samples' are satisfied.
    If the row doesn't exist yet, insert a placeholder with band='pending'.
    """
    row = conn.execute(
        "SELECT session_id FROM nights WHERE session_id = ?", (session_id,)
    ).fetchone()

    if row is None:
        today = date.today().isoformat()   # backend stamps the date (spec §5)
        conn.execute(
            """
            INSERT INTO nights
                (session_id, received_date, duration_s, valid_duration_s,
                 sample_count, valid_sample_count, band, insufficient)
            VALUES (?, ?, 0, 0, 0, 0, 'pending', 0)
            """,
            (session_id, today)
        )


# ---------------------------------------------------------------------------
# POST /reading — one live sample (spec §2, §3.1)
# ---------------------------------------------------------------------------

@app.post("/reading")
async def post_reading(sample: LiveSample):
    """
    Accept a single live SpO2/HR sample from the device (or replay_live.py).

    Guards applied:
      A2  — flag value must be "ok" or "invalid"
      A5  — create a stub night row if this is a new session
      A1  — buffer t values; once we have enough, check median gap
    """
    # A2: validate flag
    _validate_flag(sample.flag, context=f"t={sample.t}")

    # Sanitise out-of-range values (spec rule 8)
    spo2, hr, flag = _sanitise_sample(sample.spo2, sample.hr, sample.flag)

    conn = get_connection()
    try:
        # A5: make sure the nights row exists before inserting a sample
        _ensure_night_stub(sample.session_id, conn)

        # Insert the sample into the database
        conn.execute(
            "INSERT INTO samples (session_id, t, spo2, hr, flag) VALUES (?, ?, ?, ?, ?)",
            (sample.session_id, sample.t, spo2, hr, flag)
        )
        conn.commit()
    finally:
        conn.close()

    # A1: update the in-memory t-buffer and check units
    buf = _live_t_buffer[sample.session_id]
    buf.append(sample.t)
    # Keep only the last N values to avoid unbounded memory growth
    if len(buf) > A1_LIVE_BUFFER_SIZE:
        buf.pop(0)
    # Only check once we have enough samples to be meaningful
    if len(buf) >= A1_LIVE_BUFFER_SIZE:
        _check_t_units(buf)

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /night — full CSV upload (spec §2, §3.2, A3)
# ---------------------------------------------------------------------------

@app.post("/night")
async def post_night(request: Request):
    """
    Accept a complete night CSV uploaded in one batch (e.g. from replay or
    from the ESP32 fast-playback mode).

    The body is raw CSV text (Content-Type: text/csv), NOT a JSON array.
    This matches how the ESP32 streams its SD file: byte-identical to the
    file on disk, no re-encoding needed on the device.

    Guards applied:
      A3  — body is CSV (no JSON parsing attempted)
      A4  — first line must be exactly "t,spo2,hr,flag"
      A6  — reject if this session was already finalized
      A2  — flag values checked on every row
      A1  — median gap check across all rows
    """
    # Read the raw bytes and decode to text
    raw_bytes = await request.body()
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Could not decode CSV body as UTF-8"
        )

    lines = raw_text.strip().splitlines()
    if not lines:
        raise HTTPException(status_code=400, detail="Empty CSV body")

    # A4: check the header line exactly
    if lines[0].strip() != "t,spo2,hr,flag":
        raise HTTPException(
            status_code=400,
            detail=f"bad CSV header — expected 't,spo2,hr,flag', got '{lines[0].strip()}'"
        )

    # Parse all data rows
    reader = csv.DictReader(io.StringIO(raw_text))
    rows: List[dict] = []
    for i, row in enumerate(reader, start=2):   # start=2 because row 1 is the header
        # A2: flag check per row
        flag = row.get("flag", "").strip()
        _validate_flag(flag, context=f"CSV row {i}")

        # Parse integers — raise a clear error if a field isn't a number
        try:
            t    = int(row["t"])
            spo2 = int(row["spo2"])
            hr   = int(row["hr"])
        except (ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"CSV row {i}: could not parse integers — {exc}"
            )

        # Sanitise out-of-range values (spec rule 8)
        spo2, hr, flag = _sanitise_sample(spo2, hr, flag)
        rows.append({"t": t, "spo2": spo2, "hr": hr, "flag": flag})

    if not rows:
        raise HTTPException(status_code=400, detail="CSV contained no data rows")

    # A1: check t units across the whole batch
    t_values = [r["t"] for r in rows]
    _check_t_units(t_values)

    # We need a session_id — read it from the first row's t context.
    # The spec says session_id is in the POST body envelope (§3.2).
    # For a CSV-body upload there is no JSON envelope, so we need to know the
    # session_id. The ESP32 will PUT it in a query param or a custom header.
    # For now, accept it as a query parameter ?session_id=N.
    session_id_str = request.query_params.get("session_id")
    if not session_id_str:
        raise HTTPException(
            status_code=400,
            detail="Missing query parameter: session_id (e.g. POST /night?session_id=1)"
        )
    try:
        session_id = int(session_id_str)
        if session_id < 1:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="session_id must be an integer ≥ 1"
        )

    conn = get_connection()
    try:
        # A6: reject if this session was already finalized (band is not 'pending')
        existing = conn.execute(
            "SELECT band FROM nights WHERE session_id = ?", (session_id,)
        ).fetchone()

        if existing is not None and existing["band"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"session {session_id} already finalized — cannot overwrite"
            )

        # A5: ensure stub exists (handles the case where no live samples were sent)
        _ensure_night_stub(session_id, conn)

        # If there were live samples already, delete them — the SD file is
        # authoritative and replaces the partial live record (A5 / spec §8)
        conn.execute("DELETE FROM samples WHERE session_id = ?", (session_id,))

        # Insert all rows in one transaction for speed
        conn.executemany(
            "INSERT INTO samples (session_id, t, spo2, hr, flag) VALUES (?, ?, ?, ?, ?)",
            [(session_id, r["t"], r["spo2"], r["hr"], r["flag"]) for r in rows]
        )

        # Mark the night as finalized (band changes from 'pending' to 'uploaded').
        # The real summary is computed lazily when GET /nights/{id}/summary is called.
        today = date.today().isoformat()
        conn.execute(
            """
            UPDATE nights
            SET band         = 'uploaded',
                received_date = ?,
                sample_count  = ?,
                duration_s    = ?,
                valid_duration_s = 0,
                valid_sample_count = 0
            WHERE session_id = ?
            """,
            (today, len(rows), rows[-1]["t"] // 1000 if rows else 0, session_id)
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "rows_inserted": len(rows)}
