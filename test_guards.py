"""
test_guards.py — end-to-end test for integration guards A1–A10.

Run AFTER starting the server:
    uvicorn backend.main:app --reload

Then in a second terminal:
    python test_guards.py

Each guard is tested separately. You'll see PASS or FAIL with the HTTP
status/body so you can tell exactly what went wrong.

Uses only the Python standard library (urllib) — no extra packages needed.
"""

import json
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
DB_PATH = "spo2.db"

passed = 0
failed = 0

# Test session IDs — kept high to avoid colliding with real data.
# All cleaned up at the start so the test is re-runnable without restarting
# the server or deleting the DB.
_TEST_SESSION_IDS = (900, 901, 902, 903, 904, 905, 906, 907, 908, 909, 910)


def _cleanup_test_sessions() -> None:
    """Delete any rows from previous runs so the test is fully repeatable."""
    conn = sqlite3.connect(DB_PATH)
    for sid in _TEST_SESSION_IDS:
        conn.execute("DELETE FROM samples WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM nights  WHERE session_id = ?", (sid,))
    conn.commit()
    conn.close()
    print("(previous test sessions cleaned up)\n")


# ---------------------------------------------------------------------------
# Small helpers so the test code stays readable
# ---------------------------------------------------------------------------

def _post_json(path: str, body: dict) -> tuple[int, dict]:
    """POST JSON body; return (status_code, response_dict)."""
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post_csv(path: str, csv_text: str) -> tuple[int, dict]:
    """POST a raw CSV body; return (status_code, response_dict)."""
    data = csv_text.encode("utf-8")
    req  = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "text/csv"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name: str, passed_bool: bool, got, expected_desc: str) -> None:
    """Print PASS or FAIL for one guard test."""
    global passed, failed
    symbol = "PASS" if passed_bool else "FAIL"
    print(f"  [{symbol}] {name}")
    if not passed_bool:
        print(f"         expected: {expected_desc}")
        print(f"         got:      {got}")
        failed += 1
    else:
        passed += 1


# ---------------------------------------------------------------------------
# Helper: build a valid small CSV (4 rows, t in ms at ~1 Hz)
# ---------------------------------------------------------------------------

def _valid_csv(session_offset_ms: int = 0, rows: int = 4) -> str:
    lines = ["t,spo2,hr,flag"]
    for i in range(rows):
        t = session_offset_ms + i * 1000
        lines.append(f"{t},96,58,ok")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Setup: clean DB state so this script is re-runnable
# ---------------------------------------------------------------------------

_cleanup_test_sessions()

# ---------------------------------------------------------------------------
# Guard A1 — t must be milliseconds at ~1 Hz
# ---------------------------------------------------------------------------

print("\n=== Guard A1 (t units) ===")

# A1a: batch upload with t in SECONDS (t=0,1,2,...) — should be rejected
csv_seconds = "t,spo2,hr,flag\n" + "\n".join(f"{i},96,58,ok" for i in range(20)) + "\n"
status, body = _post_csv("/night?session_id=900", csv_seconds)
check(
    "A1a: /night with t in seconds (gap=1ms) -> 400",
    status == 400 and "t units look wrong" in body.get("detail", ""),
    f"{status} {body}",
    "400 with 't units look wrong'"
)

# A1b: batch upload with t in ms at ~1 Hz — should be ACCEPTED
csv_ms = _valid_csv(0, 20)
status, body = _post_csv("/night?session_id=901", csv_ms)
check(
    "A1b: /night with correct ms gaps -> 200",
    status == 200,
    f"{status} {body}",
    "200 ok"
)

# A1c: live stream — send A1_LIVE_BUFFER_SIZE samples with t in seconds -> 400
# session_id 902; t=0,1,2,... (gap = 1 ms each, way below 500 ms floor)
print("  (sending 20 live samples with t in seconds to trigger A1c...)")
last_status, last_body = 200, {}
for i in range(20):
    last_status, last_body = _post_json("/reading", {
        "session_id": 902, "t": i, "spo2": 95, "hr": 58, "flag": "ok"
    })
    if last_status == 400:
        break
check(
    "A1c: /reading 20× with t in seconds -> 400 by sample 20",
    last_status == 400 and "t units look wrong" in last_body.get("detail", ""),
    f"{last_status} {last_body}",
    "400 with 't units look wrong' on or before 20th sample"
)

# ---------------------------------------------------------------------------
# Guard A2 — flag must be exactly "ok" or "invalid"
# ---------------------------------------------------------------------------

print("\n=== Guard A2 (flag value) ===")

for bad_flag in ("OK", "valid", "1", "true", ""):
    status, body = _post_json("/reading", {
        "session_id": 903, "t": 0, "spo2": 95, "hr": 58, "flag": bad_flag
    })
    check(
        f"A2: flag='{bad_flag}' -> 400",
        status == 400 and "unknown flag value" in body.get("detail", ""),
        f"{status} {body}",
        "400 with 'unknown flag value'"
    )

# A2 on a CSV row
csv_bad_flag = "t,spo2,hr,flag\n0,96,58,OK\n"
status, body = _post_csv("/night?session_id=904", csv_bad_flag)
check(
    "A2: CSV row with flag='OK' -> 400",
    status == 400 and "unknown flag value" in body.get("detail", ""),
    f"{status} {body}",
    "400 with 'unknown flag value'"
)

# ---------------------------------------------------------------------------
# Guard A3 — /night body must be CSV, not JSON
# (If JSON is sent with Content-Type: text/csv the header check will catch it.)
# ---------------------------------------------------------------------------

print("\n=== Guard A3/A4 (CSV body and header) ===")

json_as_csv = '{"session_id":1,"samples":[]}'
status, body = _post_csv("/night?session_id=905", json_as_csv)
check(
    "A3/A4: JSON body sent as text/csv -> 400 (bad header)",
    status == 400 and "bad CSV header" in body.get("detail", ""),
    f"{status} {body}",
    "400 with 'bad CSV header'"
)

# Guard A4: wrong column order
csv_wrong_header = "spo2,hr,t,flag\n96,58,0,ok\n"
status, body = _post_csv("/night?session_id=906", csv_wrong_header)
check(
    "A4: wrong column order -> 400",
    status == 400 and "bad CSV header" in body.get("detail", ""),
    f"{status} {body}",
    "400 with 'bad CSV header'"
)

# Guard A4: correct header -> accepted
status, body = _post_csv("/night?session_id=907", _valid_csv(0, 5))
check(
    "A4: correct header 't,spo2,hr,flag' -> 200",
    status == 200,
    f"{status} {body}",
    "200 ok"
)

# ---------------------------------------------------------------------------
# Guard A5 — first /reading creates a stub nights row
# ---------------------------------------------------------------------------

print("\n=== Guard A5 (stub nights row) ===")

status, body = _post_json("/reading", {
    "session_id": 908, "t": 0, "spo2": 95, "hr": 58, "flag": "ok"
})
if status == 200:
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT band FROM nights WHERE session_id = 908"
    ).fetchone()
    conn.close()
    check(
        "A5: /reading creates nights stub with band='pending'",
        row is not None and row[0] == "pending",
        f"DB row: {row}",
        "nights row with band='pending'"
    )
else:
    check("A5: /reading returned 200", False, f"{status} {body}", "200")

# ---------------------------------------------------------------------------
# Guard A6 — re-uploading a finalized session -> 409
# ---------------------------------------------------------------------------

print("\n=== Guard A6 (no overwrite of finalized session) ===")

small_csv = _valid_csv(0, 5)
status, _ = _post_csv("/night?session_id=909", small_csv)
if status == 200:
    status2, body2 = _post_csv("/night?session_id=909", small_csv)
    check(
        "A6: second /night for same session -> 409",
        status2 == 409 and "already finalized" in body2.get("detail", ""),
        f"{status2} {body2}",
        "409 with 'already finalized'"
    )
else:
    check("A6: first /night returned 200", False, f"{status}", "200")

# ---------------------------------------------------------------------------
# Guard A9 — out-of-range spo2/hr re-flagged "invalid", not rejected
# ---------------------------------------------------------------------------

print("\n=== Guard A9 (out-of-range re-flagged invalid) ===")

# spo2=200 is out of range — should be accepted (200 OK) but stored as invalid
status, body = _post_json("/reading", {
    "session_id": 910, "t": 0, "spo2": 200, "hr": 58, "flag": "ok"
})
if status == 200:
    conn  = sqlite3.connect(DB_PATH)
    row   = conn.execute(
        "SELECT flag FROM samples WHERE session_id = 910 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    check(
        "A9: spo2=200 accepted but stored with flag='invalid'",
        row is not None and row[0] == "invalid",
        f"DB flag: {row[0] if row else 'no row'}",
        "flag='invalid' in DB"
    )
else:
    check("A9: /reading with spo2=200 returned 200", False, f"{status} {body}", "200")

# hr=5 (below 30) — same expectation
status, body = _post_json("/reading", {
    "session_id": 910, "t": 1000, "spo2": 95, "hr": 5, "flag": "ok"
})
if status == 200:
    conn  = sqlite3.connect(DB_PATH)
    row   = conn.execute(
        "SELECT flag FROM samples WHERE session_id = 910 AND t = 1000"
    ).fetchone()
    conn.close()
    check(
        "A9: hr=5 accepted but stored with flag='invalid'",
        row is not None and row[0] == "invalid",
        f"DB flag: {row[0] if row else 'no row'}",
        "flag='invalid' in DB"
    )
else:
    check("A9: /reading with hr=5 returned 200", False, f"{status} {body}", "200")

# ---------------------------------------------------------------------------
# Guard A10 — MIN_DURATION_S printed at startup (manual check)
# ---------------------------------------------------------------------------

print("\n=== Guard A10 (startup gate print) ===")
print("  [INFO] A10 is verified by eyeballing the server log at startup.")
print("         Look for: GATE: MIN_DURATION_S=240 (DEV ...)")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

total = passed + failed
print(f"\n{'='*50}")
print(f"Results: {passed}/{total} passed", end="")
if failed:
    print(f"  ({failed} FAILED)")
    sys.exit(1)
else:
    print("  — all guards enforced correctly.")

