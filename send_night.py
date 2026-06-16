"""
send_night.py — POST a single night CSV to the backend.

Simulates the device's "send" button: reads the CSV file and sends it
to POST /night exactly as the ESP32 would, so you can test one night
at a time without running mock_upload.py.

Usage:
    python send_night.py <csv_path> <session_id> [--host HOST]

Examples:
    python send_night.py night_apnea.csv 1
    python send_night.py night_normal.csv 2
    python send_night.py night_apnea.csv 5 --host http://192.168.1.42:8000
"""

import argparse
import sys

import requests


def main():
    parser = argparse.ArgumentParser(
        description="Send a night CSV to the SpO2 backend (simulates the device send button)."
    )
    parser.add_argument("csv_path",   help="Path to the CSV file, e.g. night_apnea.csv")
    parser.add_argument("session_id", type=int, help="Session ID (integer >= 1)")
    parser.add_argument(
        "--host",
        default="http://localhost:8000",
        help="Backend base URL (default: http://localhost:8000)",
    )
    args = parser.parse_args()

    if args.session_id < 1:
        print("ERROR: session_id must be >= 1")
        sys.exit(1)

    # Read the CSV bytes exactly as the ESP32 would stream the SD file
    try:
        with open(args.csv_path, "rb") as f:
            csv_data = f.read()
    except FileNotFoundError:
        print(f"ERROR: file not found: {args.csv_path}")
        sys.exit(1)

    url = f"{args.host}/night?session_id={args.session_id}"
    print(f"Sending {args.csv_path} ({len(csv_data):,} bytes) -> {url}")

    try:
        resp = requests.post(
            url,
            data=csv_data,
            headers={"Content-Type": "text/csv"},
            timeout=60,
        )
    except requests.ConnectionError:
        print(f"ERROR: cannot connect to {args.host} — is the server running?")
        sys.exit(1)

    if resp.ok:
        result = resp.json()
        print(f"OK  rows_inserted={result.get('rows_inserted', '?')}")
    else:
        print(f"ERROR {resp.status_code}: {resp.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
