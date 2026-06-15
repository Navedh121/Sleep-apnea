# ml.py — ML feature extraction and RF inference for the SpO2 screening backend.
#
# This file has two jobs:
#   1.  extract_window_features() — turns one 60-second window of samples into the
#       exact 10-element feature vector defined in spec §6 (fixed index order).
#   2.  predict_night()           — slices a full night into windows, runs the RF,
#       computes rf_index and rf_confidence, and writes them back to the DB.
#
# Guard A7: the rolling-median baseline is NOT recomputed here.
#   compute_baseline() is imported from summary.py and called once on the full night
#   before any windowing.  Both summary and ML use the identical baseline values.

import numpy as np

from backend.config import WINDOW_S, DESAT_THRESHOLD_PCT
from backend.summary import compute_baseline, odi_band


# ---------------------------------------------------------------------------
# Feature extraction — spec §6, indices 0–9 (DO NOT reorder)
# ---------------------------------------------------------------------------

def extract_window_features(
    window_spo2: list,
    window_hr:   list,
    baseline_at_window: list,
) -> list:
    """
    Compute the 10-element feature vector for one 60-second window.

    Arguments:
        window_spo2        — SpO2 values (flag='ok' samples only, within the window)
        window_hr          — HR values, same samples, same order
        baseline_at_window — per-sample rolling baseline from the FULL night's
                             compute_baseline() call (NOT recomputed per window)

    Returns:
        List of 10 floats in the exact §6 index order, or None if window is empty.
    """
    n = len(window_spo2)
    if n == 0:
        return None

    spo2 = np.array(window_spo2, dtype=float)
    hr   = np.array(window_hr,   dtype=float)
    base = np.array(baseline_at_window, dtype=float)

    # ── indices 0–4: basic SpO2 stats ────────────────────────────────────────
    spo2_min   = float(np.min(spo2))
    spo2_mean  = float(np.mean(spo2))
    spo2_max   = float(np.max(spo2))
    spo2_range = spo2_max - spo2_min
    spo2_std   = float(np.std(spo2)) if n > 1 else 0.0

    # ── index 5: desat_depth — how far the worst dip fell below the window's baseline ──
    # "baseline − spo2_min" — use the mean baseline for the window as the reference.
    desat_depth = float(np.mean(base)) - spo2_min

    # Compute per-sample drop below rolling baseline (positive = spo2 is low)
    drops = base - spo2

    # ── index 6: desat_count — number of distinct dips (state-machine, same logic
    #    as detect_events in summary.py so the two are consistent) ────────────────
    in_dip    = False
    dip_count = 0
    for drop in drops:
        if not in_dip and drop >= DESAT_THRESHOLD_PCT:
            in_dip     = True
            dip_count += 1
        elif in_dip and drop < DESAT_THRESHOLD_PCT:
            in_dip = False
    desat_count = dip_count

    # ── index 7: secs_below_baseline_minus_thr ───────────────────────────────
    # Seconds where spo2 stayed >= DESAT_THRESHOLD_PCT below the rolling baseline.
    # At ~1 Hz, sample count ≈ seconds — the spec note "derive t from millis()"
    # means we trust the sample count as a proxy for time inside each window.
    secs_below = int(np.sum(drops >= DESAT_THRESHOLD_PCT))

    # ── indices 8–9: heart-rate stats ────────────────────────────────────────
    hr_mean = float(np.mean(hr))
    hr_std  = float(np.std(hr)) if n > 1 else 0.0

    # Return in the EXACT §6 order — do not reorder; the trained model depends on position
    return [
        spo2_min,    # idx 0
        spo2_mean,   # idx 1
        spo2_max,    # idx 2
        spo2_range,  # idx 3
        spo2_std,    # idx 4
        desat_depth, # idx 5
        desat_count, # idx 6
        secs_below,  # idx 7
        hr_mean,     # idx 8
        hr_std,      # idx 9
    ]


# ---------------------------------------------------------------------------
# Full-night feature extraction (used by both train_rf.py and predict_night)
# ---------------------------------------------------------------------------

def extract_features_for_night(rows: list) -> list:
    """
    Extract all 60-second window feature vectors from a full night's sample rows.

    'rows' is a list of dicts: {'t': int, 'spo2': int, 'hr': int, 'flag': str}
    This is the format produced both by SQLite (fetchall + dict()) and by CSV reader.

    Steps:
      1. Compute rolling-median baseline across the FULL night (A7 — one call, not per window).
      2. Build a t → baseline lookup.
      3. Slice rows into non-overlapping 60-second windows using a single pass.
      4. For each window with at least one valid sample, call extract_window_features().

    Returns:
        List of (window_index, feature_vector) pairs; all-invalid windows are skipped.
    """
    # Filter valid samples for the baseline (A7 guard: same function as summary.py)
    valid_spo2 = [r['spo2'] for r in rows if r['flag'] == 'ok']
    valid_t_ms = [r['t']    for r in rows if r['flag'] == 'ok']

    if not valid_spo2:
        return []

    # One call to compute_baseline (A7) — shared with the ODI computation
    baseline_series = compute_baseline(valid_spo2, valid_t_ms)

    # Map each valid sample's t to its rolling-baseline value
    # (dict supports duplicate t lookups gracefully — last value wins, which is fine)
    baseline_map = dict(zip(valid_t_ms, baseline_series))

    # Slice into non-overlapping WINDOW_S windows using a single left-to-right pass
    if not rows:
        return []

    window_ms = WINDOW_S * 1000   # 60 000 ms
    first_t   = rows[0]['t']

    windows        = []
    t_start        = first_t
    window_bucket  = []   # rows belonging to the current window
    ptr            = 0    # pointer into rows

    # Iterate through all rows in time order; bucket them into windows
    while ptr < len(rows):
        r    = rows[ptr]
        t    = r['t']
        t_end = t_start + window_ms

        if t < t_end:
            # Row belongs to current window
            window_bucket.append(r)
            ptr += 1
        else:
            # Current window is closed — process it
            windows.append((t_start, window_bucket))
            window_bucket = []
            t_start       = t_end   # advance window boundary

    # Don't forget the last bucket
    if window_bucket:
        windows.append((t_start, window_bucket))

    # Extract features for each window
    results   = []
    win_idx   = 0
    for t_start, bucket in windows:
        # Only use valid (flag='ok') samples for features
        valid_in_window = [s for s in bucket if s['flag'] == 'ok']

        if not valid_in_window:
            win_idx += 1
            continue   # spec §6: skip all-invalid windows

        w_spo2     = [s['spo2'] for s in valid_in_window]
        w_hr       = [s['hr']   for s in valid_in_window]
        w_baseline = [baseline_map[s['t']] for s in valid_in_window]

        fv = extract_window_features(w_spo2, w_hr, w_baseline)
        if fv is not None:
            results.append((win_idx, fv))

        win_idx += 1

    return results


# ---------------------------------------------------------------------------
# Night-level RF inference (called by backend/main.py verdict endpoint)
# ---------------------------------------------------------------------------

def predict_night(session_id: int, conn, model) -> dict:
    """
    Run the loaded RandomForest model against every valid 60-second window for
    this session and return rf_index + rf_confidence (spec §6).

    rf_index      = apnea-labelled windows ÷ valid recording hours
    rf_confidence = mean P(apnea) across apnea-predicted windows

    Results are also written back to the nights table so repeat calls hit the DB
    instead of re-running the model.
    """
    rows = conn.execute(
        "SELECT t, spo2, hr, flag FROM samples WHERE session_id = ? ORDER BY t",
        (session_id,)
    ).fetchall()

    # Convert sqlite3.Row to plain dict so extract_features_for_night can accept
    # both SQLite rows (backend) and CSV dicts (train_rf.py)
    rows = [{"t": r["t"], "spo2": r["spo2"], "hr": r["hr"], "flag": r["flag"]}
            for r in rows]

    if not rows:
        return {"rf_index": None, "rf_confidence": None}

    # Extract features — A7 ensures the same baseline as the ODI calculation
    window_features = extract_features_for_night(rows)

    if not window_features:
        return {"rf_index": None, "rf_confidence": None}

    X = np.array([fv for _, fv in window_features])

    # Predict: class 1 = apnea, class 0 = normal
    preds  = model.predict(X)
    probas = model.predict_proba(X)   # shape (n_windows, 2)

    apnea_mask      = preds == 1
    n_apnea_windows = int(np.sum(apnea_mask))

    # Compute valid_duration_s using the same gap method as summary.py
    valid_t = [r['t'] for r in rows if r['flag'] == 'ok']
    if len(valid_t) < 2:
        return {"rf_index": None, "rf_confidence": None}

    valid_gaps       = [valid_t[i + 1] - valid_t[i] for i in range(len(valid_t) - 1)
                        if valid_t[i + 1] - valid_t[i] <= 2000]
    valid_duration_s = sum(valid_gaps) // 1000
    valid_hours      = valid_duration_s / 3600.0

    if valid_hours <= 0:
        return {"rf_index": None, "rf_confidence": None}

    rf_index = round(n_apnea_windows / valid_hours, 1)

    # rf_confidence: mean P(apnea) for windows the model called "apnea"
    if n_apnea_windows > 0:
        rf_confidence = round(float(np.mean(probas[apnea_mask, 1])), 3)
    else:
        rf_confidence = 0.0

    # Cache results in the DB so repeat GET /verdict calls are instant
    conn.execute(
        "UPDATE nights SET rf_index = ?, rf_confidence = ? WHERE session_id = ?",
        (rf_index, rf_confidence, session_id)
    )
    conn.commit()

    return {"rf_index": rf_index, "rf_confidence": rf_confidence}
