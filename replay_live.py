#!/usr/bin/env python3
"""
replay_live.py — stream a contract CSV to the backend's /reading endpoint so the
Live page (data-contract spec §9, page 2) can be built and watched with no device.

Usage:
  python replay_live.py night_apnea.csv
  python replay_live.py night_apnea.csv --url http://localhost:8000/reading --speed 60

--speed 1   = real time (1 sample/sec)
--speed 60  = 60x faster (a whole night in minutes) for quick UI testing
"""

import argparse
import csv
import time

import requests   # pip install requests


def replay(path, url, session_id, speed):
    with open(path) as f:
        rows = list(csv.DictReader(f))

    # Use a Session so the TCP connection is reused across all POSTs.
    # Without this, each request opens a new connection (~120 ms overhead on
    # Windows) which makes a 240-sample replay take 30+ seconds instead of ~2.
    session = requests.Session()

    prev_t = 0
    for row in rows:
        t = int(row["t"])                       # ms since session start
        dt = (t - prev_t) / 1000.0 / speed      # real wait, scaled by --speed
        if dt > 0:
            time.sleep(dt)
        prev_t = t

        payload = {
            "session_id": session_id,
            "t": t,
            "spo2": int(row["spo2"]),
            "hr": int(row["hr"]),
            "flag": row["flag"],
        }
        try:
            session.post(url, json=payload, timeout=2)
        except requests.RequestException as e:
            print(f"POST failed at t={t}: {e}")   # keep going; mimic a flaky link
    print(f"done -- streamed {len(rows)} samples as session {session_id}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--url", default="http://localhost:8000/reading")
    ap.add_argument("--session-id", type=int, default=901)   # any unused test id
    ap.add_argument("--speed", type=float, default=60)
    a = ap.parse_args()
    replay(a.csv_path, a.url, a.session_id, a.speed)
