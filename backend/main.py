# main.py — FastAPI application entry point.
#
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

from fastapi import FastAPI, HTTPException, Request

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
