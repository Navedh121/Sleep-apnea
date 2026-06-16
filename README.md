# SpO₂ Sleep-Apnea Screening Monitor — Laptop Software

A low-cost overnight blood-oxygen screening tool.
**This is a screening tool, not a medical device.**
All results are estimated risk bands. Always consult a sleep specialist.

---

## Quick start (Windows)

**Double-click `run.bat`.**

That's it. On first run it creates a virtual environment, installs dependencies,
starts the server, seeds three demo nights into the database, and opens
`http://localhost:8000` in your browser. On subsequent runs it skips setup and
seeding and just starts the server.

To stop the server, close the **SpO2 Server** terminal window that opens.

---

## What this repo is

The laptop software half of the project:

- A **FastAPI backend** receives SpO₂/HR data from an ESP32 sensor over WiFi.
- A **SQLite database** stores raw samples and computed summaries.
- A **4-page web app** shows live graphs, history, ML verdict, and an LLM chatbot.
- A **scikit-learn random forest** gives a second-opinion severity band.

The ESP32 firmware is built separately. This software is fully testable without
any hardware using the included `mock_night.py` and `replay_live.py` scripts.

---

## Prerequisites

- Python 3.10 or newer (`python --version` to check)
- `pip` (comes with Python)

---

## Manual setup (if you prefer not to use run.bat)

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your .env file for the Groq API key (enables LLM chat)
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux
# Open .env and add your Groq API key.

# 4. Start the server
uvicorn backend.main:app --reload
```

Open `http://localhost:8000` in a browser.
Open `http://localhost:8000/docs` for the interactive API reference.

---

## Sending a night to the backend

### Option A — send_night.py (recommended, simulates the device send button)

```bash
python send_night.py night_apnea.csv 1
python send_night.py night_normal.csv 2
python send_night.py night_short.csv 3
```

`send_night.py <csv_path> <session_id> [--host URL]`

### Option B — curl

```bash
curl -X POST "http://localhost:8000/night?session_id=1" \
     -H "Content-Type: text/csv" \
     --data-binary @night_apnea.csv
```

---

## Generating mock data (no hardware needed)

```bash
# 8-hour apnea night -> scores moderate/severe
python mock_night.py apnea  --hours 8   --out night_apnea.csv  --seed 42

# 8-hour normal night -> scores normal
python mock_night.py normal --hours 8   --out night_normal.csv --seed 42

# 4-minute recording -> fails the duration gate (insufficient)
python mock_night.py short  --minutes 4 --out night_short.csv  --seed 42
```

---

## Streaming a mock night to the live graph

```bash
# Terminal 1: server running
uvicorn backend.main:app --reload

# Terminal 2: stream at 60x speed
python replay_live.py night_apnea.csv --speed 60 --session-id 4
# Then open http://localhost:8000/app/live.html
```

---

## Training the ML model

The model is trained on mock data and saved to `model/rf_model.pkl`.
Run this once (or re-run whenever you have new training data):

```bash
python train_rf.py
```

Expected output includes **SENSITIVITY** and **SPECIFICITY** on the held-out test
split. The server loads the model automatically on startup.

---

## Deleting a night

Use the **Delete** button on the History page (with a confirm dialog), or via the API:

```bash
curl -X DELETE http://localhost:8000/nights/1
```

---

## Build phases

| Phase | Status | Description |
|-------|--------|-------------|
| A     | Done | Repo setup, mock data scripts |
| B1    | Done | FastAPI skeleton, SQLite schema, ingestion endpoints |
| B2    | Done | Integration guards A1-A10 as hard rejections |
| B3    | Done | Night summary, ODI, read endpoints |
| C     | Done | Mock integration test (apnea/normal/short end-to-end) |
| D     | Done | 4-page HTML frontend + LLM chat |
| E     | Done | scikit-learn random forest, sensitivity/specificity |

---

## Common errors

| Error | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'backend'` | Run `uvicorn` from `Sleep-apnea/`, not inside `backend/` |
| `Address already in use` | Another server is on port 8000; use `--port 8001` |
| `409 session already finalized` | Session already has data. Use a different `session_id`, or delete the session first. |
| `400 bad CSV header` | File must start with exactly `t,spo2,hr,flag` (no spaces, no BOM) |
| `GROQ_API_KEY not set` | Copy `.env.example` to `.env` and add your Groq key |
| RF verdict shows placeholder | Run `python train_rf.py` then restart the server |
