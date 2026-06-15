# CLAUDE.md — project memory for the SpO₂ sleep-apnea screening app

This file is read at the start of **every** Claude Code session. It is the durable
memory for this project: rules, plan, and current state. **Keep the "CURRENT STATE"
block at the bottom up to date and commit it after every phase** — that is what lets
a fresh session resume exactly where the last one stopped.

## What this project is
A low-cost overnight blood-oxygen (SpO₂) **screening** monitor. Hardware (ESP32 +
finger sensor, built later by a team) measures SpO₂/HR overnight; **this repo is the
laptop software** that receives the data and shows a live graph, a severity band, and
a chatbot. It is a SCREENING tool — never claim it diagnoses or cures. Output wording
is always an estimated risk band + "consult a sleep specialist," never "you have
sleep apnea."

## Source of truth
`SpO2-Data-Contract-Spec.md` (v1.1) in this folder is binding. Obey it exactly: field
names, types, units, endpoints, schemas, the RF feature-vector order, the duration
gate, and Integration Guards A1–A10. Never rename or invent fields. If anything is
ambiguous or missing, STOP and ask the human — do not guess.
`SpO2-Monitor-Project-State-Handoff.md` (if present) is background/product context
only; the contract overrides it on any technical detail.

## Scope of THIS repo
Laptop software only: FastAPI backend + SQLite + a simple web app + a scikit-learn ML
pipeline. **Do NOT write ESP32 firmware here.** The device is built later and plugs
into this software through the contract.

## Stack
Python + FastAPI + SQLite. Frontend: keep it simple — plain HTML/CSS/JS + a charting
library served by FastAPI is fine; avoid heavy build tooling unless justified to the
human.

## Hard rules
- Don't change the contract to make code easier. If the contract is wrong, propose a
  versioned edit (v1.2) and wait — never silently diverge.
- The human is a software/ML beginner: say what you'll do before doing it, comment all
  code, no clever one-liners.
- Secrets: the Groq LLM key lives in a gitignored `.env`, never hardcoded. The LLM call
  sits behind one function `ask_llm(question, night_summary)`; pass the night summary
  directly in the prompt — no RAG, no vector DB.
- Duration gate is automatic (short file → "insufficient", ML skipped), never a manual
  button.

## Build phases (do in order; pause after each for the human to test)
- **B1 Backend skeleton** — endpoints + SQL schema (spec §2, §10); ingestion first
  (`POST /reading`, `POST /night`) writing into the samples table.
- **B2 Integration guards** — A1–A10 as HARD rejections with clear messages and correct
  HTTP codes; add the automatic duration gate.
- **B3 Summary + ODI** — night-summary (§5) from raw samples; ONE baseline function
  reused everywhere (A7); wire the read endpoints (`/nights`, `/summary`, `/samples`,
  `/verdict`, `/live/*`).
- **C  Mock integration** — use `mock_night.py` + `replay_live.py` to prove end-to-end
  with no hardware: apnea→severe, normal→normal, short→insufficient, and the live
  endpoint receives the replay.
- **D  4-page app (§9)** — logs/history, live graph, ML verdict, LLM chat.
- **E  ML pipeline (§6)** — backend feature extraction (fixed order); scikit-learn
  random forest reporting SENSITIVITY & SPECIFICITY (not just accuracy). Real SpO₂
  dataset not chosen yet → train on labelled windows from `mock_night.py` for now, keep
  the dataset source swappable; mark real-data validation as deferred to when hardware
  exists.

## Testing / no dead ends
After each phase, give the human: (1) the exact run command, (2) the expected output,
(3) a short "common errors + fixes" list. Run tests yourself where you can. Keep
`README.md` updated each phase.

## Git
This folder is a git repo with a private GitHub remote. Commit after each working phase
with a clear message; also commit the updated CURRENT STATE block below. Never
force-push, delete history, or change repo visibility.

## RESUME PROTOCOL (every new session)
1. Read this file (especially CURRENT STATE) and `SpO2-Data-Contract-Spec.md`.
2. Run `git log --oneline -10` to see recent progress.
3. Tell the human where we are and the next step, then continue from there.
4. When a phase finishes: update CURRENT STATE (done / next / how to test / open
   questions), then commit it.

---

## CURRENT STATE — RESUME HERE
_Last updated: 15 June 2026 — update this block after every phase and commit it._

- **Stage A (setup): DONE** — private GitHub repo created; folder holds the 3 starter
  files: `SpO2-Data-Contract-Spec.md` (v1.1), `mock_night.py`, `replay_live.py` (and
  this `CLAUDE.md`).
- **Contract: FROZEN at v1.1**, including Integration Guards A1–A10.
- **mock_night.py + replay_live.py: BUILT and VERIFIED** — apnea→severe,
  normal→normal, short→insufficient; sample spacing ~1 Hz (passes guard A1); invalid
  rows carry sentinel 0 values (guard A9).
- **Phase B1: DONE** — FastAPI + SQLite skeleton.
  - `backend/config.py` — all §11 constants; `MIN_DURATION_S=240` (DEV); A10 print on import.
  - `backend/database.py` — `init_db()` creates both tables exactly from §10.
  - `backend/models.py` — Pydantic schemas for all wire types.
  - `backend/main.py` — `POST /reading` (stub upsert A5, flag A2) + `POST /night`
    (CSV body A3, header A4, finalize guard A6, A1 gap check on batch).
  - `requirements.txt`, `.gitignore`, `.env.example`, `README.md` added.
  - **Verified:** live sample → 200; CSV upload 28800 rows → 200; duplicate → 409;
    bad flag → 400; wrong header → 400. DB contains correct rows.
- **NEXT: Phase B2 — integration guards A1–A10 as hard rejections** (A1 live-stream
  buffer already partially wired; B2 will harden all remaining guards and add tests).
- **How to test the current state:**
  ```
  uvicorn backend.main:app --reload
  # In another terminal:
  python mock_night.py apnea --hours 8 --out night_apnea.csv --seed 42
  curl -X POST "http://localhost:8000/night?session_id=1" -H "Content-Type: text/csv" --data-binary @night_apnea.csv
  # Expected: {"status":"ok","rows_inserted":28800}
  ```
- **Open decisions (confirm at build time):** SpO₂-native training dataset; Groq model
  name; final production `MIN_DURATION_S` (≈4 h floor).
