# summary.py — night-summary computation for the SpO2 screening backend.
#
# All maths that turns raw samples into the §5 summary lives here.
#
# Guard A7: compute_baseline() is the ONE function used for both the
# ODI calculation (below) and the RF feature extraction (ml.py Phase E).
# Never copy-paste baseline logic into another file — import this instead.

import json
import statistics

import numpy as np

from backend.config import (
    BASELINE_WINDOW_S,
    DESAT_THRESHOLD_PCT,
    TIME_BELOW_90_THRESHOLD,
    TIME_BELOW_88_THRESHOLD,
    MIN_DURATION_S,
)


# ---------------------------------------------------------------------------
# A7 — the ONE baseline function (imported by ml.py too)
# ---------------------------------------------------------------------------

def compute_baseline(valid_spo2: list, valid_t_ms: list) -> list:
    """
    Rolling-median SpO2 baseline (spec §11, guard A7).

    For each valid sample at time t, takes the median of all valid SpO2
    values in the preceding BASELINE_WINDOW_S seconds (inclusive).

    Why median instead of mean?  A few deep dips would drag a mean down,
    making future dips look smaller.  The median is resistant to outliers,
    so it stays close to the patient's true resting SpO2.

    Uses a sliding-window pointer so the loop is O(n) in the number of
    samples, not O(n²).  numpy.median handles the inner sort efficiently.

    Args:
        valid_spo2  — list of SpO2 integers for flag='ok' samples only
        valid_t_ms  — matching list of t values (milliseconds)

    Returns:
        list of float, same length as inputs, one baseline value per sample.
    """
    if not valid_spo2:
        return []

    window_ms = BASELINE_WINDOW_S * 1000   # 180 s → 180 000 ms
    spo2_arr  = np.array(valid_spo2, dtype=float)
    t_arr     = np.array(valid_t_ms,  dtype=np.int64)

    baseline = []
    left = 0   # sliding left edge of the window

    for i in range(len(valid_spo2)):
        # Advance 'left' until the oldest sample in the window is within range
        while t_arr[left] < t_arr[i] - window_ms:
            left += 1
        # Window = spo2_arr[left : i+1]
        baseline.append(float(np.median(spo2_arr[left : i + 1])))

    return baseline


# ---------------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------------

def detect_events(
    valid_spo2: list, valid_t_ms: list, baseline: list
) -> list:
    """
    Find desaturation events in a valid SpO2 series (spec §5 event_list).

    An event opens when spo2 drops >= DESAT_THRESHOLD_PCT below the
    rolling baseline, and closes when it recovers back above that threshold.

    Each event in the returned list is a dict matching the §5 schema:
        start_t     — ms since session start (event open)
        end_t       — ms (event close / recovery)
        nadir_spo2  — lowest SpO2 during the event
        drop        — percentage points below baseline at event open
        duration_s  — length in seconds

    Args:
        valid_spo2  — SpO2 list (flag='ok' samples only)
        valid_t_ms  — matching t values in ms
        baseline    — list from compute_baseline(), same length

    Returns:
        list of event dicts, in chronological order.
    """
    events = []
    in_event = False

    # Track current event state
    event_start_t        = 0
    event_start_baseline = 0.0
    event_nadir_spo2     = 100
    event_end_t          = 0

    for spo2, t, base in zip(valid_spo2, valid_t_ms, baseline):
        drop_pct = base - spo2   # positive = spo2 is below baseline

        if not in_event:
            if drop_pct >= DESAT_THRESHOLD_PCT:
                # Event opens
                in_event             = True
                event_start_t        = t
                event_start_baseline = base
                event_nadir_spo2     = spo2
        else:
            # Track the nadir (lowest SpO2 in this event)
            if spo2 < event_nadir_spo2:
                event_nadir_spo2 = spo2

            if drop_pct < DESAT_THRESHOLD_PCT:
                # Event closes — record it
                in_event = False
                event_end_t = t
                events.append({
                    "start_t":    event_start_t,
                    "end_t":      event_end_t,
                    "nadir_spo2": int(event_nadir_spo2),
                    # drop = how far nadir fell below the baseline at event open
                    "drop":       int(round(event_start_baseline - event_nadir_spo2)),
                    "duration_s": (event_end_t - event_start_t) // 1000,
                })

    # If the recording ends while still in a desaturation, close that event
    if in_event and valid_t_ms:
        last_t = valid_t_ms[-1]
        events.append({
            "start_t":    event_start_t,
            "end_t":      last_t,
            "nadir_spo2": int(event_nadir_spo2),
            "drop":       int(round(event_start_baseline - event_nadir_spo2)),
            "duration_s": (last_t - event_start_t) // 1000,
        })

    return events


# ---------------------------------------------------------------------------
# Band mapping
# ---------------------------------------------------------------------------

def odi_band(odi: float) -> str:
    """
    Map an ODI value (events per hour) to a severity band (spec §5).
    Cutoffs are frozen — do not change without a contract bump to v2.
    """
    if odi < 5:
        return "normal"
    elif odi < 15:
        return "mild"
    elif odi < 30:
        return "moderate"
    else:
        return "severe"


# ---------------------------------------------------------------------------
# Hourly breakdown
# ---------------------------------------------------------------------------

def _compute_hourly(
    valid_spo2: list, valid_t_ms: list, event_list: list
) -> list:
    """
    Build the hourly[] array for the §5 summary.
    Each entry covers one hour of recording:
        { hour: N, events: count_in_that_hour, spo2_min: lowest_value }
    Only hours that have at least one valid sample are included.
    """
    if not valid_t_ms:
        return []

    max_t_ms  = valid_t_ms[-1]
    max_hour  = max_t_ms // 3_600_000   # integer hours

    hourly = []
    for h in range(int(max_hour) + 1):
        start_ms = h * 3_600_000
        end_ms   = (h + 1) * 3_600_000

        hour_spo2 = [
            s for s, t in zip(valid_spo2, valid_t_ms)
            if start_ms <= t < end_ms
        ]
        if not hour_spo2:
            continue

        hour_events = sum(
            1 for e in event_list
            if start_ms <= e["start_t"] < end_ms
        )

        hourly.append({
            "hour":     h,
            "events":   hour_events,
            "spo2_min": int(min(hour_spo2)),
        })

    return hourly


# ---------------------------------------------------------------------------
# DB write-back
# ---------------------------------------------------------------------------

def _update_nights_row(session_id: int, summary: dict, conn) -> None:
    """Persist the computed summary scalars into the nights table."""
    conn.execute(
        """
        UPDATE nights SET
            duration_s         = ?,
            valid_duration_s   = ?,
            sample_count       = ?,
            valid_sample_count = ?,
            spo2_baseline      = ?,
            spo2_min           = ?,
            spo2_mean          = ?,
            time_below_90_s    = ?,
            time_below_88_s    = ?,
            events             = ?,
            odi                = ?,
            band               = ?,
            insufficient       = ?,
            hourly_json        = ?,
            event_list_json    = ?
        WHERE session_id = ?
        """,
        (
            summary["duration_s"],
            summary["valid_duration_s"],
            summary["sample_count"],
            summary["valid_sample_count"],
            summary["spo2_baseline"],
            summary["spo2_min"],
            summary["spo2_mean"],
            summary["time_below_90_s"],
            summary["time_below_88_s"],
            summary["events"],
            summary["odi"],
            summary["band"],
            1 if summary["insufficient"] else 0,
            json.dumps(summary["hourly"]),
            json.dumps(summary["event_list"]),
            session_id,
        )
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_summary(session_id: int, conn) -> dict:
    """
    Compute the full §5 night summary for session_id from raw samples in the DB.

    Steps:
      1. Load all samples, split into valid / all.
      2. Compute valid_duration_s from timestamps (spec: use t, not sample count).
      3. Duration gate (§7): if too short → insufficient=True, return early.
      4. compute_baseline() (A7) → detect_events() → ODI → band.
      5. Write results back to the nights table.
      6. Return the summary dict matching §5 exactly.

    This is called lazily by GET /nights/{id}/summary — not at upload time —
    so the POST /night endpoint stays fast even for 28 800-row files.
    """
    # --- Load samples ---
    rows = conn.execute(
        "SELECT t, spo2, hr, flag FROM samples WHERE session_id = ? ORDER BY t",
        (session_id,)
    ).fetchall()

    if not rows:
        raise ValueError(f"No samples found for session {session_id}")

    night_row = conn.execute(
        "SELECT received_date FROM nights WHERE session_id = ?",
        (session_id,)
    ).fetchone()
    received_date = night_row["received_date"] if night_row else "unknown"

    # --- Separate valid and all samples ---
    all_t    = [r["t"]    for r in rows]
    valid_t  = [r["t"]    for r in rows if r["flag"] == "ok"]
    valid_spo2 = [r["spo2"] for r in rows if r["flag"] == "ok"]
    valid_hr   = [r["hr"]   for r in rows if r["flag"] == "ok"]

    sample_count       = len(rows)
    valid_sample_count = len(valid_spo2)

    # Total recording length (first to last sample)
    duration_s = (all_t[-1] - all_t[0]) // 1000 if len(all_t) >= 2 else 0

    # valid_duration_s: sum of inter-sample gaps between consecutive valid samples
    # where the gap is <= 2000 ms (= two expected 1-Hz intervals).
    # Larger gaps mean the sensor was invalid for more than one cycle —
    # those stretches are excluded, which is the honest result (spec §7).
    if len(valid_t) < 2:
        valid_duration_s = 0
    else:
        valid_gaps = [
            valid_t[i + 1] - valid_t[i]
            for i in range(len(valid_t) - 1)
            if valid_t[i + 1] - valid_t[i] <= 2000
        ]
        valid_duration_s = sum(valid_gaps) // 1000

    # --- Duration gate (spec §7) ---
    if valid_duration_s < MIN_DURATION_S:
        summary = {
            "session_id":         session_id,
            "received_date":      received_date,
            "duration_s":         duration_s,
            "valid_duration_s":   valid_duration_s,
            "sample_count":       sample_count,
            "valid_sample_count": valid_sample_count,
            "spo2_baseline":      None,
            "spo2_min":           None,
            "spo2_mean":          None,
            "time_below_90_s":    None,
            "time_below_88_s":    None,
            "events":             None,
            "odi":                None,
            "band":               "insufficient",
            "insufficient":       True,
            "hourly":             [],
            "event_list":         [],
        }
        _update_nights_row(session_id, summary, conn)
        return summary

    # --- Full analysis ---

    # A7: rolling-median baseline (imported by ml.py — never recomputed elsewhere)
    baseline_series = compute_baseline(valid_spo2, valid_t)

    # Overall baseline for the summary: median of the per-sample baselines
    # (stable over the whole night; personalised to this patient)
    spo2_baseline = int(round(float(np.median(baseline_series))))

    # SpO2 statistics
    spo2_min  = int(min(valid_spo2))
    spo2_mean = round(sum(valid_spo2) / len(valid_spo2), 1)

    # Time below clinical thresholds (1 valid sample ≈ 1 second at 1 Hz)
    time_below_90_s = sum(1 for s in valid_spo2 if s < TIME_BELOW_90_THRESHOLD)
    time_below_88_s = sum(1 for s in valid_spo2 if s < TIME_BELOW_88_THRESHOLD)

    # Desaturation events → ODI
    event_list  = detect_events(valid_spo2, valid_t, baseline_series)
    events      = len(event_list)
    valid_hours = valid_duration_s / 3600.0
    odi         = round(events / valid_hours, 1) if valid_hours > 0 else 0.0
    band        = odi_band(odi)

    # Hourly breakdown
    hourly = _compute_hourly(valid_spo2, valid_t, event_list)

    summary = {
        "session_id":         session_id,
        "received_date":      received_date,
        "duration_s":         duration_s,
        "valid_duration_s":   valid_duration_s,
        "sample_count":       sample_count,
        "valid_sample_count": valid_sample_count,
        "spo2_baseline":      spo2_baseline,
        "spo2_min":           spo2_min,
        "spo2_mean":          spo2_mean,
        "time_below_90_s":    time_below_90_s,
        "time_below_88_s":    time_below_88_s,
        "events":             events,
        "odi":                odi,
        "band":               band,
        "insufficient":       False,
        "hourly":             hourly,
        "event_list":         event_list,
    }

    _update_nights_row(session_id, summary, conn)
    return summary
