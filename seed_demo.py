"""
seed_demo.py — Generate mock CSVs and seed them into the running backend.

Called automatically by run.bat when the database has no nights yet.
Safe to run manually too — it skips seeding if nights already exist.

Sessions seeded:
    1  night_apnea.csv   (8 h, apnea pattern  -> severe)
    2  night_normal.csv  (8 h, normal pattern  -> normal)
    3  night_short.csv   (4 min               -> insufficient)
"""

import os
import sqlite3
import subprocess
import sys

import requests

HOST = "http://localhost:8000"


def nights_in_db() -> int:
    """Return the number of finalized nights already in the database."""
    db_path = "spo2.db"
    if not os.path.exists(db_path):
        return 0
    try:
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM nights WHERE band != 'pending'"
        ).fetchone()[0]
        conn.close()
        return count
    except sqlite3.OperationalError:
        # Table doesn't exist yet — DB was just created but is empty
        return 0


def generate_csv(mode: str, out: str, **kwargs) -> None:
    """Run mock_night.py to create one CSV file if it doesn't already exist."""
    if os.path.exists(out):
        print(f"  {out} already exists — skipping generation.")
        return

    args = ["python", "mock_night.py", mode, "--out", out, "--seed", "42"]
    if "hours" in kwargs:
        args += ["--hours", str(kwargs["hours"])]
    if "minutes" in kwargs:
        args += ["--minutes", str(kwargs["minutes"])]

    print(f"  Generating {out} ...")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR generating {out}: {result.stderr}")
        sys.exit(1)


def send_csv(csv_path: str, session_id: int) -> None:
    """POST one CSV to the running backend (same as the device send button)."""
    url = f"{HOST}/night?session_id={session_id}"
    with open(csv_path, "rb") as f:
        data = f.read()

    print(f"  Uploading {csv_path} as session {session_id} ...")
    try:
        resp = requests.post(url, data=data, headers={"Content-Type": "text/csv"}, timeout=60)
    except requests.ConnectionError:
        print(f"  ERROR: cannot reach {HOST}. Is the server running?")
        sys.exit(1)

    if resp.ok:
        rows = resp.json().get("rows_inserted", "?")
        print(f"  OK ({rows} rows)")
    else:
        print(f"  ERROR {resp.status_code}: {resp.text}")
        sys.exit(1)


def main():
    # Self-check: skip if nights already exist so re-running run.bat is safe
    n = nights_in_db()
    if n > 0:
        print(f"Database already has {n} night(s). Skipping seed.")
        return

    print("Seeding demo data into the database...")

    # Generate the three mock CSV files
    generate_csv("apnea",  "night_apnea.csv",  hours=8)
    generate_csv("normal", "night_normal.csv", hours=8)
    generate_csv("short",  "night_short.csv",  minutes=4)

    # Upload each to the backend
    send_csv("night_apnea.csv",  session_id=1)
    send_csv("night_normal.csv", session_id=2)
    send_csv("night_short.csv",  session_id=3)

    print("\nDemo data seeded successfully.")
    print("  Session 1: apnea night  -> should show SEVERE")
    print("  Session 2: normal night -> should show NORMAL")
    print("  Session 3: short night  -> should show INSUFFICIENT")


if __name__ == "__main__":
    main()
