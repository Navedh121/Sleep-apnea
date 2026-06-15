"""
mock_upload.py — Phase C end-to-end integration test.

Proves the full pipeline with NO hardware by:
  1. Uploading the three mock CSV files (apnea / normal / short) to POST /night.
  2. Calling GET /nights/{id}/verdict and asserting the expected outcome.
  3. Streaming the short CSV through POST /reading (via replay_live.py in a
     subprocess) and verifying that /live/active and /live/recent respond.

Run this AFTER starting the server:
    uvicorn backend.main:app --reload

Then:
    python mock_upload.py

All three scenarios must pass for Phase C to be complete.
"""

import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE     = "http://localhost:8000"
DB_PATH  = "spo2.db"

# Session IDs used by this test
APNEA_SESSION  = 1
NORMAL_SESSION = 2
SHORT_SESSION  = 3
LIVE_SESSION   = 4   # used for the live-stream test

passed = 0
failed = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_csv(path_or_url: str, csv_path: str) -> tuple[int, dict]:
    """POST a raw CSV file to the given URL."""
    with open(csv_path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(
        path_or_url,
        data=data,
        headers={"Content-Type": "text/csv"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url: str) -> dict:
    """GET a URL and return parsed JSON."""
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def check(name: str, ok: bool, got=None, expected="") -> None:
    global passed, failed
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}")
    if not ok:
        print(f"         expected: {expected}")
        print(f"         got:      {got}")
        failed += 1
    else:
        passed += 1


def _cleanup_test_sessions() -> None:
    """Wipe the sessions used by this test so it can be re-run cleanly."""
    conn = sqlite3.connect(DB_PATH)
    for sid in (APNEA_SESSION, NORMAL_SESSION, SHORT_SESSION, LIVE_SESSION):
        conn.execute("DELETE FROM samples WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM nights  WHERE session_id = ?", (sid,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Part 1: batch upload of the three mock nights
# ---------------------------------------------------------------------------

print("=" * 56)
print("Phase C — Mock Integration Test")
print("=" * 56)

print("\n[1/2] Batch upload — POST /night for each scenario\n")

_cleanup_test_sessions()
print("  (previous test data cleaned up)\n")

# Upload apnea night
status, body = _post_csv(
    f"{BASE}/night?session_id={APNEA_SESSION}",
    "night_apnea.csv"
)
check(
    f"Upload night_apnea.csv  (session {APNEA_SESSION})",
    status == 200 and body.get("rows_inserted", 0) == 28800,
    f"{status} {body}",
    "200 rows_inserted=28800"
)

# Upload normal night
status, body = _post_csv(
    f"{BASE}/night?session_id={NORMAL_SESSION}",
    "night_normal.csv"
)
check(
    f"Upload night_normal.csv (session {NORMAL_SESSION})",
    status == 200 and body.get("rows_inserted", 0) == 28800,
    f"{status} {body}",
    "200 rows_inserted=28800"
)

# Upload short night
status, body = _post_csv(
    f"{BASE}/night?session_id={SHORT_SESSION}",
    "night_short.csv"
)
check(
    f"Upload night_short.csv  (session {SHORT_SESSION})",
    status == 200 and body.get("rows_inserted", 0) == 240,
    f"{status} {body}",
    "200 rows_inserted=240"
)

# ---------------------------------------------------------------------------
# Part 2: verdict assertions
# ---------------------------------------------------------------------------

print("\n[2a/2b] Verdict checks\n")

verdict_apnea  = _get(f"{BASE}/nights/{APNEA_SESSION}/verdict")
verdict_normal = _get(f"{BASE}/nights/{NORMAL_SESSION}/verdict")
verdict_short  = _get(f"{BASE}/nights/{SHORT_SESSION}/verdict")

check(
    "Session 1 (apnea)  -> band moderate or severe",
    verdict_apnea["band"] in ("moderate", "severe") and not verdict_apnea["insufficient"],
    verdict_apnea,
    "band in (moderate, severe), insufficient=false"
)

check(
    "Session 2 (normal) -> band normal",
    verdict_normal["band"] == "normal" and not verdict_normal["insufficient"],
    verdict_normal,
    "band=normal, insufficient=false"
)

check(
    "Session 3 (short)  -> insufficient=true",
    verdict_short["insufficient"] is True,
    verdict_short,
    "insufficient=true"
)

# Also print the summary stats for the apnea night so you can see the ODI
summary = _get(f"{BASE}/nights/{APNEA_SESSION}/summary")
print(f"\n  Apnea night summary:")
print(f"    ODI:           {summary['odi']} events/hr")
print(f"    Events:        {summary['events']}")
print(f"    SpO2 baseline: {summary['spo2_baseline']} %")
print(f"    SpO2 min:      {summary['spo2_min']} %")
print(f"    Time <90%:     {summary['time_below_90_s']} s")
print(f"    Hours tracked: {summary['valid_duration_s'] / 3600:.1f} h")

# ---------------------------------------------------------------------------
# Part 3: live-stream test using replay_live.py
# ---------------------------------------------------------------------------
# We replay the SHORT CSV (240 samples) at 60x speed → completes in ~4 seconds.
# While it runs we poll /live/active and /live/recent.
# ---------------------------------------------------------------------------

print("\n[2b] Live-stream test — replay_live.py + polling\n")

# Start replay_live.py in the background
print(f"  Starting replay_live.py (night_short.csv, 60x speed, session {LIVE_SESSION})...")
proc = subprocess.Popen(
    [
        sys.executable, "replay_live.py",
        "night_short.csv",
        "--session-id", str(LIVE_SESSION),
        "--speed", "60",
        "--url", f"{BASE}/reading",
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

# Give it a moment to send the first few samples
time.sleep(0.5)

# Check /live/active — should show the live session
try:
    active = _get(f"{BASE}/live/active")
    check(
        f"/live/active shows session {LIVE_SESSION} while streaming",
        active.get("session_id") == LIVE_SESSION,
        active,
        f"session_id={LIVE_SESSION}"
    )
except Exception as e:
    check("/live/active returned a response", False, str(e), "JSON with session_id")

# Check /live/recent — should have at least some samples
time.sleep(0.5)
try:
    recent = _get(f"{BASE}/live/recent?session_id={LIVE_SESSION}&since_t=0")
    check(
        f"/live/recent returns samples for session {LIVE_SESSION}",
        len(recent) > 0,
        f"{len(recent)} samples",
        ">0 samples"
    )
    if recent:
        print(f"  First live sample: t={recent[0]['t']}  spo2={recent[0]['spo2']}  flag={recent[0]['flag']}")
        print(f"  Last  live sample: t={recent[-1]['t']}  spo2={recent[-1]['spo2']}  flag={recent[-1]['flag']}")
except Exception as e:
    check("/live/recent returned a response", False, str(e), "JSON array")

# Wait for replay to finish (it's only 240 samples at 60x; ~4 seconds)
print("  Waiting for replay to finish...")
try:
    stdout, stderr = proc.communicate(timeout=120)
    replay_output = stdout.decode().strip() or stderr.decode().strip()
    check(
        "replay_live.py completed without error",
        proc.returncode == 0,
        f"returncode={proc.returncode}  output={replay_output}",
        "returncode=0"
    )
    if replay_output:
        print(f"  replay output: {replay_output}")
except subprocess.TimeoutExpired:
    proc.kill()
    check("replay_live.py completed within 120 s", False, "timed out", "completed")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

total = passed + failed
print(f"\n{'=' * 56}")
print(f"Results: {passed}/{total} passed", end="")
if failed:
    print(f"  ({failed} FAILED)")
    sys.exit(1)
else:
    print("  -- Phase C COMPLETE")
