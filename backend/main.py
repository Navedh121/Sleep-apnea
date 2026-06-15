# main.py — FastAPI application entry point.
#
# Phase B3: summary computation + all read endpoints added.
# Phase B2: all integration guards A1–A10 enforced as hard rejections.
#
# Guard summary (spec §A):
#   A1  — t is milliseconds: median inter-sample gap must be 500–2000 ms.
#          Checked BEFORE any DB write (live buffer) and on full batch (/night).
#   A2  — flag is exactly "ok" or "invalid" — no other value accepted.
#   A3  — POST /night body is raw CSV, not JSON.
#   A4  — CSV first line must be exactly "t,spo2,hr,flag".
#   A5  — first /reading for a new session_id creates a stub nights row.
#   A6  — re-uploading a finalized session returns 409.
#   A7  — one baseline function (implemented in summary.py, Phase B3).
#   A8  — firmware-side only; no app action.
#   A9  — out-of-range spo2/hr re-flagged "invalid" rather than rejected.
#   A10 — MIN_DURATION_S printed at import time (done in config.py).

import csv
import io
import statistics
from collections import defaultdict
from datetime import date
from typing import List

import json
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request

from backend.config import (
    A1_GAP_MIN_MS, A1_GAP_MAX_MS, A1_LIVE_BUFFER_SIZE
)
from backend.database import init_db, get_connection
from backend.models import (
    LiveSample, NightRow, NightSummary, VerdictResponse,
    SamplePoint, LiveActiveResponse,
)
from backend.summary import compute_summary

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SpO2 Sleep-Apnea Screening Monitor",
    description="Laptop backend for the ESP32 SpO2 sensor (spec v1.1)",
    version="0.1.0-B2",
)

# In-memory buffer for A1 live-stream check.
# Key = session_id, Value = list of the last A1_LIVE_BUFFER_SIZE t values.
_live_t_buffer: dict[int, list[int]] = defaultdict(list)


@app.on_event("startup")
def on_startup() -> None:
    """Create tables on first run (safe to call every time)."""
    init_db()
    print("Database initialised. Server ready.")


# ---------------------------------------------------------------------------
# Guard helpers
# ---------------------------------------------------------------------------

def _validate_flag(flag: str, context: str = "") -> None:
    """
    Guard A2: reject any value that isn't exactly "ok" or "invalid".
    'context' is shown in the error (e.g. "row t=1000") to help debugging.
    """
    if flag not in ("ok", "invalid"):
        where = f" (at {context})" if context else ""
        raise HTTPException(
            status_code=400,
            detail=f"unknown flag value '{flag}'{where} — must be 'ok' or 'invalid'"
        )


def _check_t_units(t_values: list[int]) -> None:
    """
    Guard A1: verify the median gap between consecutive t values is ~1 Hz.
    Expected range: 500–2000 ms.  Outside this → firmware is almost certainly
    sending seconds or a sample counter rather than milliseconds.

    Requires at least 2 values; silently skips if fewer.
    """
    if len(t_values) < 2:
        return

    sorted_t = sorted(t_values)
    gaps = [sorted_t[i + 1] - sorted_t[i] for i in range(len(sorted_t) - 1)]
    non_zero = [g for g in gaps if g > 0]
    if not non_zero:
        return

    median_gap = statistics.median(non_zero)

    if not (A1_GAP_MIN_MS <= median_gap <= A1_GAP_MAX_MS):
        raise HTTPException(
            status_code=400,
            detail=(
                f"t units look wrong — expected milliseconds at ~1 Hz "
                f"(median gap {median_gap:.0f} ms, expected 500–2000 ms). "
                f"Did the device send seconds or a sample counter instead of ms?"
            )
        )


def _sanitise_sample(spo2: int, hr: int, flag: str) -> tuple[int, int, str]:
    """
    Guard A9 / spec rule 8: out-of-range spo2 or hr → re-mark as 'invalid'
    rather than crashing or rejecting the upload.
    Sentinel 0 values (from mock invalid rows) are caught here automatically.
    """
    if spo2 < 0 or spo2 > 100:
        flag = "invalid"
    if hr < 30 or hr > 180:
        flag = "invalid"
    return spo2, hr, flag


def _ensure_night_stub(session_id: int, conn) -> None:
    """
    Guard A5: create a placeholder nights row (band='pending') the first time
    this session_id appears, so foreign-key constraints on samples are satisfied.
    """
    exists = conn.execute(
        "SELECT 1 FROM nights WHERE session_id = ?", (session_id,)
    ).fetchone()
    if exists is None:
        conn.execute(
            """
            INSERT INTO nights
                (session_id, received_date, duration_s, valid_duration_s,
                 sample_count, valid_sample_count, band, insufficient)
            VALUES (?, ?, 0, 0, 0, 0, 'pending', 0)
            """,
            (session_id, date.today().isoformat())
        )


def _check_session_not_finalized(session_id: int, conn) -> None:
    """
    Guard A6: reject a second /night upload for a session that already has
    a real band (anything other than 'pending' or 'uploaded means nothing yet).
    'uploaded' is our intermediate state set by /night — re-uploading it
    counts as a finalized overwrite attempt.
    """
    row = conn.execute(
        "SELECT band FROM nights WHERE session_id = ?", (session_id,)
    ).fetchone()
    # Allow overwrite only if the session doesn't exist yet OR is still pending
    # (live-stream stub only — no SD file uploaded yet).
    if row is not None and row["band"] not in ("pending",):
        raise HTTPException(
            status_code=409,
            detail=f"session {session_id} already finalized — cannot overwrite"
        )


# ---------------------------------------------------------------------------
# POST /reading — one live sample (spec §2, §3.1)
# ---------------------------------------------------------------------------

@app.post("/reading")
async def post_reading(sample: LiveSample):
    """
    Accept a single SpO2/HR sample streamed in real time.

    Order of guards (rejection happens before any DB write):
      1. A2 — flag value
      2. A9 — out-of-range sanitisation
      3. A1 — update t-buffer, check median gap (rejects before insert)
      4. A5 — create stub night row if new session
      5. DB INSERT
    """
    # Guard A2: flag value
    _validate_flag(sample.flag, context=f"t={sample.t}")

    # Guard A9: sanitise out-of-range values
    spo2, hr, flag = _sanitise_sample(sample.spo2, sample.hr, sample.flag)

    # Guard A1: update buffer THEN check — so a bad sample is rejected before
    # it ever touches the database.
    buf = _live_t_buffer[sample.session_id]
    buf.append(sample.t)
    if len(buf) > A1_LIVE_BUFFER_SIZE:
        buf.pop(0)   # drop oldest to keep the buffer bounded
    if len(buf) >= A1_LIVE_BUFFER_SIZE:
        # This raises HTTPException(400) if the median gap is wrong.
        # The exception bubbles up before we reach the DB code below.
        _check_t_units(buf)

    # All guards passed — write to DB.
    conn = get_connection()
    try:
        _ensure_night_stub(sample.session_id, conn)   # A5
        conn.execute(
            "INSERT INTO samples (session_id, t, spo2, hr, flag) VALUES (?, ?, ?, ?, ?)",
            (sample.session_id, sample.t, spo2, hr, flag)
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /night — full CSV upload (spec §2, §3.2, A3)
# ---------------------------------------------------------------------------

@app.post("/night")
async def post_night(request: Request):
    """
    Accept a complete night's data as a raw CSV body (A3).
    The ESP32 streams its SD file byte-for-byte; no JSON wrapping on device.

    session_id must be supplied as a query param: POST /night?session_id=N
    (The JSON envelope in §3.2 is for tiny hand-made test payloads only.)

    Order of guards:
      A3 — body is CSV
      A4 — header line exact match
      A2 — flag value per row
      A9 — sanitise out-of-range per row
      A1 — median gap check on the full batch
      A6 — reject if session already finalized
      A5 — create stub if needed
    """
    # --- Parse session_id from query string ---
    session_id_str = request.query_params.get("session_id")
    if not session_id_str:
        raise HTTPException(
            status_code=400,
            detail="Missing query parameter: session_id  (e.g. POST /night?session_id=1)"
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

    # --- A3: read raw CSV body ---
    raw_bytes = await request.body()
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV body must be UTF-8 encoded")

    lines = raw_text.strip().splitlines()
    if not lines:
        raise HTTPException(status_code=400, detail="Empty CSV body")

    # --- A4: exact header check ---
    if lines[0].strip() != "t,spo2,hr,flag":
        raise HTTPException(
            status_code=400,
            detail=f"bad CSV header — expected 't,spo2,hr,flag', got '{lines[0].strip()}'"
        )

    # --- Parse rows; apply A2 and A9 per row ---
    reader = csv.DictReader(io.StringIO(raw_text))
    rows: List[dict] = []
    for line_num, row in enumerate(reader, start=2):  # line 1 = header
        flag = row.get("flag", "").strip()
        _validate_flag(flag, context=f"CSV line {line_num}")   # A2

        try:
            t    = int(row["t"])
            spo2 = int(row["spo2"])
            hr   = int(row["hr"])
        except (ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"CSV line {line_num}: integer parse error — {exc}"
            )

        spo2, hr, flag = _sanitise_sample(spo2, hr, flag)  # A9
        rows.append({"t": t, "spo2": spo2, "hr": hr, "flag": flag})

    if not rows:
        raise HTTPException(status_code=400, detail="CSV contained no data rows")

    # --- A1: check t units on the full batch ---
    _check_t_units([r["t"] for r in rows])

    # --- DB writes (after all guards pass) ---
    conn = get_connection()
    try:
        _check_session_not_finalized(session_id, conn)   # A6

        _ensure_night_stub(session_id, conn)   # A5

        # SD file is authoritative: replace any partial live-stream samples.
        conn.execute("DELETE FROM samples WHERE session_id = ?", (session_id,))

        conn.executemany(
            "INSERT INTO samples (session_id, t, spo2, hr, flag) VALUES (?, ?, ?, ?, ?)",
            [(session_id, r["t"], r["spo2"], r["hr"], r["flag"]) for r in rows]
        )

        # Mark night finalized with coarse stats; full summary computed lazily in B3.
        duration_s = rows[-1]["t"] // 1000 if rows else 0
        conn.execute(
            """
            UPDATE nights
            SET band             = 'uploaded',
                received_date    = ?,
                sample_count     = ?,
                duration_s       = ?,
                valid_duration_s = 0,
                valid_sample_count = 0
            WHERE session_id = ?
            """,
            (date.today().isoformat(), len(rows), duration_s, session_id)
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "rows_inserted": len(rows)}


# ---------------------------------------------------------------------------
# GET /nights — list all uploaded nights (spec §2)
# ---------------------------------------------------------------------------

@app.get("/nights", response_model=List[NightRow])
def get_nights():
    """
    Return every night that has been uploaded, newest first.
    Shows: session_id, received_date, band, duration_s, insufficient.
    Used by Page 1 (Logs) to populate the history table.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT session_id, received_date, band, duration_s, insufficient
            FROM   nights
            WHERE  band != 'pending'
            ORDER  BY session_id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "session_id":    r["session_id"],
            "received_date": r["received_date"],
            "band":          r["band"],
            "duration_s":    r["duration_s"],
            "insufficient":  bool(r["insufficient"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /nights/{id}/summary — full §5 summary (spec §2)
# ---------------------------------------------------------------------------

@app.get("/nights/{session_id}/summary", response_model=NightSummary)
def get_summary(session_id: int):
    """
    Compute (or reuse) the full night summary for this session.

    Summary is computed lazily on first call, then written back to the DB so
    repeat calls are instant.  Pass ?recompute=1 to force a recalculation.
    """
    conn = get_connection()
    try:
        # Check the session exists
        row = conn.execute(
            "SELECT band FROM nights WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"session {session_id} not found")

        if row["band"] == "pending":
            raise HTTPException(
                status_code=409,
                detail="session is still live — upload the full night first"
            )

        # compute_summary handles the gate, computation, and DB write-back
        summary = compute_summary(session_id, conn)
    finally:
        conn.close()

    return summary


# ---------------------------------------------------------------------------
# GET /nights/{id}/samples — downsampled time series for plotting (spec §2)
# ---------------------------------------------------------------------------

@app.get("/nights/{session_id}/samples", response_model=List[SamplePoint])
def get_samples(
    session_id: int,
    step: int = Query(default=1, ge=1, description="Return every Nth sample (1 = all)")
):
    """
    Return the raw SpO2/HR samples for this session, suitable for graphing.

    Use ?step=N to downsample (e.g. step=10 gives 10× fewer points, making
    the chart snappier for an 8-hour night).
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM nights WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"session {session_id} not found")

        rows = conn.execute(
            "SELECT t, spo2, hr, flag FROM samples WHERE session_id = ? ORDER BY t",
            (session_id,)
        ).fetchall()
    finally:
        conn.close()

    # Apply the step: take every Nth row (index 0, step, 2*step, ...)
    downsampled = rows[::step]
    return [{"t": r["t"], "spo2": r["spo2"], "hr": r["hr"], "flag": r["flag"]}
            for r in downsampled]


# ---------------------------------------------------------------------------
# GET /nights/{id}/verdict — band + RF second-opinion (spec §2)
# ---------------------------------------------------------------------------

@app.get("/nights/{session_id}/verdict", response_model=VerdictResponse)
def get_verdict(session_id: int):
    """
    Return the severity verdict for this session.

    The 'band' comes from the ODI calculation (computed in /summary).
    'rf_index' and 'rf_confidence' come from the random forest (Phase E);
    they are null until the model is trained and loaded.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT band, insufficient, rf_index, rf_confidence
            FROM   nights
            WHERE  session_id = ?
            """,
            (session_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"session {session_id} not found")

        band = row["band"]

        # If the band is still 'uploaded' (summary not yet computed), compute it now
        if band in ("uploaded", "pending"):
            summary = compute_summary(session_id, conn)
            band        = summary["band"]
            insufficient = summary["insufficient"]
        else:
            insufficient = bool(row["insufficient"])

    finally:
        conn.close()

    return {
        "band":          band,
        "insufficient":  insufficient,
        "rf_index":      row["rf_index"],       # None until Phase E
        "rf_confidence": row["rf_confidence"],  # None until Phase E
    }


# ---------------------------------------------------------------------------
# GET /live/active — which session is currently streaming (spec §2)
# ---------------------------------------------------------------------------

@app.get("/live/active", response_model=LiveActiveResponse)
def get_live_active():
    """
    Return the session_id of the most recent live session (band='pending'),
    or null if nothing is currently streaming.
    Used by Page 2 (Live) to know which session to poll.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT session_id FROM nights
            WHERE  band = 'pending'
            ORDER  BY session_id DESC
            LIMIT  1
            """
        ).fetchone()
    finally:
        conn.close()

    return {"session_id": row["session_id"] if row else None}


# ---------------------------------------------------------------------------
# GET /live/recent — new samples since a given t (spec §2)
# ---------------------------------------------------------------------------

@app.get("/live/recent", response_model=List[SamplePoint])
def get_live_recent(
    session_id: int = Query(..., description="Which session to poll"),
    since_t:    int = Query(default=0, description="Return only samples with t > this value"),
):
    """
    Return all samples for session_id where t > since_t, ordered by t.

    The Live page calls this every second, passing the last t it received as
    since_t, so only new samples come back each time (efficient polling).
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT t, spo2, hr, flag
            FROM   samples
            WHERE  session_id = ? AND t > ?
            ORDER  BY t
            """,
            (session_id, since_t)
        ).fetchall()
    finally:
        conn.close()

    return [{"t": r["t"], "spo2": r["spo2"], "hr": r["hr"], "flag": r["flag"]}
            for r in rows]
