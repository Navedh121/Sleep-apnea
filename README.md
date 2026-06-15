# SpO₂ Sleep-Apnea Screening Monitor — Laptop Software

A low-cost overnight blood-oxygen screening tool.
**This is a screening tool, not a medical device.**
All results are estimated risk bands. Always consult a sleep specialist.

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

- Python 3.10 or newer
- `pip` (comes with Python)

---

## First-time setup

```bash
# 1. Move into the project folder
cd "Sleep-apnea"

# 2. (Optional but recommended) create a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env file for the Groq API key (needed for Phase D chat)
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux
# Then open .env and replace 'your_groq_api_key_here' with your real key.
```

---

## Running the backend server

```bash
# Always run from the Sleep-apnea/ root directory, not from inside backend/
uvicorn backend.main:app --reload
```

You should see output like:
```
GATE: MIN_DURATION_S=240 (DEV — change to 14400 for production)
Database initialised. Server ready.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

Open `http://localhost:8000/docs` in a browser for the interactive API docs.

---

## Generating mock data (no hardware needed)

```bash
# 8-hour apnea night → should score moderate/severe
python mock_night.py apnea  --hours 8   --out night_apnea.csv  --seed 42

# 8-hour normal night → should score normal
python mock_night.py normal --hours 8   --out night_normal.csv --seed 42

# 4-minute recording → should fail the duration gate (insufficient)
python mock_night.py short  --minutes 4 --out night_short.csv  --seed 42
```

---

## Uploading a mock night to the backend

```bash
# session_id=1 for the apnea night
curl -X POST "http://localhost:8000/night?session_id=1" \
     -H "Content-Type: text/csv" \
     --data-binary @night_apnea.csv
```

---

## Streaming a mock night to the live endpoint (tests the Live page)

```bash
# In one terminal: server must be running
uvicorn backend.main:app --reload

# In another terminal: stream at 60× speed (so you don't wait 8 hours)
python replay_live.py night_apnea.csv --speed 60 --session-id 4
```

---

## Build phases

| Phase | Status | Description |
|-------|--------|-------------|
| A     | ✅ Done | Repo setup, mock data scripts |
| B1    | ✅ Done | FastAPI skeleton, SQLite schema, ingestion endpoints |
| B2    | ✅ Done | Integration guards A1–A10 as hard rejections |
| B3    | ⏳ Next | Night summary, ODI, read endpoints |
| C     | ⬜      | Mock integration test (apnea/normal/short end-to-end) |
| D     | ⬜      | 4-page HTML frontend + LLM chat |
| E     | ⬜      | scikit-learn random forest, sensitivity/specificity |

---

## Common errors

| Error | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'backend'` | Run `uvicorn` from `Sleep-apnea/`, not inside `backend/` |
| `Address already in use` | Another server is on port 8000; use `--port 8001` |
| `409 session already finalized` | The DB already has this session. Delete `spo2.db` to reset, or use a different `session_id` |
| `400 bad CSV header` | Check the file starts with exactly `t,spo2,hr,flag` (no spaces, no BOM) |
| `GROQ_API_KEY not set` | Copy `.env.example` → `.env` and fill in your key |
