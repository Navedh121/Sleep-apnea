#!/usr/bin/env python3
"""
mock_night.py — synthetic overnight SpO2 generator for the SpO2 sleep-apnea
screening monitor.

It produces CSV files that are BYTE-IDENTICAL in format to what the ESP32 writes
to the SD card, so the laptop app + ML pipeline can be built and tested with zero
hardware. The backend cannot tell a mock file from a real one.

Data contract (see SpO2-Data-Contract-Spec.md):
  columns (fixed order): t,spo2,hr,flag
    t    = integer MILLISECONDS since session start (monotonic)
    spo2 = integer 0..100  (%)
    hr   = integer 30..180 (bpm)
    flag = "ok" | "invalid"
  sampling = 1 Hz

Usage:
  python mock_night.py apnea  --hours 8   --out night_apnea.csv
  python mock_night.py normal --hours 8   --out night_normal.csv
  python mock_night.py short  --minutes 4 --out night_short.csv   # trips the gate
"""

import argparse
import csv
import random

# --- contract constants (must match spec §11) ---------------------------------
SAMPLE_RATE_HZ = 1
BASELINE_SPO2 = 96      # healthy resting baseline
BASELINE_HR = 58        # resting sleep heart rate


def gen_samples(duration_s, profile, seed=0):
    """Build a list of (t_ms, spo2, hr, flag) rows for one synthetic night.

    profile:
      "apnea"  -> frequent deep desaturation dips  -> moderate/severe band
      "normal" -> rare, shallow dips               -> normal band
    """
    rnd = random.Random(seed)
    n = int(duration_s * SAMPLE_RATE_HZ)   # one sample per second

    # Start everyone at baseline with gentle per-sample noise. SpO2 is a slow,
    # quiet signal at rest; HR wanders a little. (DSP analogy: baseline + low
    # amplitude noise, before we carve in the "events".)
    spo2 = [BASELINE_SPO2 + rnd.gauss(0, 0.4) for _ in range(n)]
    hr = [BASELINE_HR + rnd.gauss(0, 2.0) for _ in range(n)]
    flag = ["ok"] * n

    # Event cadence + depth depend on the profile.
    if profile == "apnea":
        gap_min, gap_max = 35, 75      # seconds between events -> high ODI
        depth_min, depth_max = 5, 10   # % desaturation below baseline
    else:  # "normal"
        gap_min, gap_max = 600, 1200   # rare
        depth_min, depth_max = 1, 3    # shallow -> stays "normal"

    # Carve each desaturation as fall -> hold -> recover. This is the classic
    # apnea dip-and-recover shape the firmware threshold + the RF both key on.
    t = rnd.randint(gap_min, gap_max)
    while t < duration_s - 60:
        depth = rnd.uniform(depth_min, depth_max)
        fall = rnd.randint(8, 15)      # seconds to fall to nadir
        hold = rnd.randint(3, 10)      # seconds held low
        rise = rnd.randint(10, 18)     # seconds to recover

        for k in range(fall):                       # ramp down
            idx = t + k
            if idx < n:
                spo2[idx] -= depth * ((k + 1) / fall)
        for k in range(hold):                       # held at nadir
            idx = t + fall + k
            if idx < n:
                spo2[idx] -= depth
        for k in range(rise):                       # ramp back up
            idx = t + fall + hold + k
            if idx < n:
                frac = (k + 1) / rise
                spo2[idx] -= depth * (1 - frac)
                hr[idx] += depth * 1.5 * frac        # arousal tachycardia on recovery

        t += fall + hold + rise + rnd.randint(gap_min, gap_max)

    # Sprinkle a few short "finger slipped" dropouts (~1 per hour) so the app's
    # invalid handling + the gate get exercised.
    for _ in range(max(0, int(duration_s / 3600))):
        start = rnd.randint(0, max(0, n - 30))
        length = rnd.randint(5, 25)
        for idx in range(start, min(n, start + length)):
            flag[idx] = "invalid"

    rows = []
    for i in range(n):
        t_ms = int(i * 1000 / SAMPLE_RATE_HZ)
        if flag[i] == "invalid":
            # Guard A9: sentinel 0/0 for invalid rows. If any downstream code
            # forgets to filter flag=="ok" before computing stats, the averages
            # collapse toward 0 and the bug screams instead of quietly skewing
            # a verdict. Real sensor "invalid" readings are junk too.
            rows.append((t_ms, 0, 0, "invalid"))
        else:
            s = int(round(max(70, min(100, spo2[i]))))   # clamp to sane range
            h = int(round(max(30, min(180, hr[i]))))
            rows.append((t_ms, s, h, "ok"))
    return rows


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "spo2", "hr", "flag"])   # header == contract column order
        w.writerows(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate a synthetic SpO2 night CSV.")
    ap.add_argument("profile", choices=["normal", "apnea", "short"])
    ap.add_argument("--hours", type=float, default=8)
    ap.add_argument("--minutes", type=float, default=None,
                    help="overrides --hours when set (used for the short file)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    duration_s = int(a.minutes * 60) if a.minutes is not None else int(a.hours * 3600)
    # "short" is just a brief normal recording; its job is to trip the duration gate.
    profile = "normal" if a.profile == "short" else a.profile

    rows = gen_samples(duration_s, profile, seed=a.seed)
    write_csv(rows, a.out)
    print(f"wrote {len(rows)} rows to {a.out}  (duration={duration_s}s, profile={a.profile})")
