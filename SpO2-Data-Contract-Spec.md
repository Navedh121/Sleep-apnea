# SpO₂ Sleep-Apnea Screening Monitor — Data-Contract Spec (v1.1, FROZEN)

> **v1.1 adds §A "Integration guards"** at the end — binding rules that turn every
> likely device↔app mismatch into a loud, located error instead of a silent wrong
> answer. Where §A differs from an earlier section, **§A wins.**

> **Read alongside** `sleep-apnea-monitor-project-brief.md` and
> `SpO2-Monitor-Project-State-Handoff.md`. This is the deliverable named in
> handoff §15. It freezes the interface between the two halves of the project so
> they can be built **independently and in parallel**, then snapped together.
>
> Mental model (bridge to what you know): this is the **register map / bus
> interface spec** between two chips. Once both sides agree on the byte order
> and the meaning of each field, the firmware person and the app person never
> have to talk again — exactly like wiring an I²C peripheral from its datasheet
> without knowing the chip's internals. **Freeze it, then nobody renegotiates.**
>
> Two roles consume this:
> - **Firmware side** (ESP32, the 4-person team): only needs §3 (live sample),
>   §4 (SD CSV row), §8 (modes), §11 (constants). It emits raw rows. Nothing else.
> - **App/ML side** (you, solo, pre-buildable in Claude Code before 25 Jun):
>   needs everything. Builds backend + DB + 4 pages + RF, all against the mock
>   generator in §12 — **no hardware required**.

---

## 0. The one rule that makes this work

The **firmware emits only raw samples**. All interpretation — windowing,
features, baseline, ODI, band, the ML verdict — happens in the **laptop
backend**. The ESP32's own threshold detector (handoff Layer 1) still runs
on-device for the OLED/buzzer/standalone demo, but it does **not** push derived
numbers over the wire. The wire carries `t, spo2, hr, flag` and nothing else.

Why: keeps firmware dead-simple and frozen forever; lets you change features,
baseline window, band cutoffs, even the whole ML model, **without reflashing**.
This is the same discipline as "sensor outputs raw counts, the MCU does the
maths" — never bake policy into the part that's hard to update.

---

## 1. Transport & backend

| Decision | Frozen choice | Upgrade path |
|---|---|---|
| Transport | **HTTP POST** (request/response, simplest) | **MQTT** later (survives flaky WiFi) |
| Backend | **Python + FastAPI** on the laptop | same |
| Database | **SQLite** (one file, zero setup) | Postgres if it ever scales |
| Serialisation | **JSON** over HTTP | same |
| Device role | **client** (ESP32 POSTs out) | same |
| Direction | **one-way: device → laptop only.** No command path back to the ESP32 (handoff §2 — device is button-driven) | — |
| Sampling rate | **1 Hz** (1 sample/sec) | — |

FastAPI is chosen over Flask because its Pydantic models *are* a machine-checked
copy of this contract — if the device sends a malformed row, the backend rejects
it automatically. SQLite is a single `.db` file, no server process: right size
for one user and a handful of nights.

Backend base URL during development: `http://<laptop-LAN-IP>:8000`.

---

## 2. Endpoints (FROZEN)

**Ingestion (device → backend):**

| Method | Path | Body | Purpose | Mode |
|---|---|---|---|---|
| POST | `/reading` | live-sample (§3) | one streamed sample | Live |
| POST | `/night` | night-upload (§3) | whole stored CSV in one batch | Fast-playback |

**Read (browser/app → backend):**

| Method | Path | Returns | Page |
|---|---|---|---|
| GET | `/nights` | list of night rows (`session_id, received_date, band, duration_s, insufficient`) | Logs |
| GET | `/nights/{id}/summary` | night-summary (§5) | Logs / ML |
| GET | `/nights/{id}/samples?step=N` | downsampled time series for plotting | Live / ML |
| GET | `/nights/{id}/verdict` | `{band, insufficient, rf_index, rf_confidence}` | ML verdict |
| GET | `/live/active` | `{session_id}` or `null` | Live |
| GET | `/live/recent?session_id=&since_t=` | samples with `t > since_t` | Live |
| POST | `/nights/{id}/chat` | `{question}` → `{answer}` (calls Groq) | LLM chat |

The Live page **polls** `/live/recent` once a second carrying the last `t` it
saw (`since_t`). Polling is the beginner-robust default; Server-Sent Events is a
clean later upgrade, but don't start there.

---

## 3. Wire schemas (FROZEN)

### 3.1 Live sample — body of `POST /reading`
```json
{
  "session_id": 1,
  "t":          43000,
  "spo2":       95,
  "hr":         58,
  "flag":       "ok"
}
```

### 3.2 Night upload — body of `POST /night`
```json
{
  "session_id": 1,
  "samples": [
    { "t": 0,    "spo2": 96, "hr": 57, "flag": "ok" },
    { "t": 1000, "spo2": 96, "hr": 58, "flag": "ok" }
  ]
}
```

### 3.3 Field definitions (apply to both)

| Field | Type | Units / range | Meaning |
|---|---|---|---|
| `session_id` | int ≥ 1 | — | the night's number. Equals the SD file's sequence number (`night_001.csv` → 1). Assigned by the **persisted counter on the device** (handoff §6). |
| `t` | int ≥ 0 | **milliseconds since session start** | relative time only — **no wall clock**. See note below. |
| `spo2` | int | 0–100 (%) | blood-oxygen saturation. |
| `hr` | int | 30–180 (bpm); clamp outside | heart rate. |
| `flag` | string | `"ok"` \| `"invalid"` | `"invalid"` = sensor library rejected this reading (no finger / motion). When invalid, `spo2`/`hr` are present but **must not be trusted**. |

**Why `t` is elapsed milliseconds, not a sample index:** ODI is *events per
hour* — it needs **real elapsed time**. If the loop stalls or a sample is
dropped, a sample-counter would under-count time and inflate the rate. Derive
`t` on the device as `millis() - session_start_millis`. It is monotonic and
robust to jitter; the backend never assumes evenly-spaced samples. (Interview
point: rate = events ÷ *real* duration, never ÷ sample count.)

**Why milliseconds, not seconds:** removes any two-samples-in-one-second
ambiguity and survives a future faster sample rate without a contract change.
8 h × 1 Hz ≈ 28.8 M ms — fits a signed 32-bit int with room to spare.

### 3.4 Anti-mismatch rules (device ⇄ app) — the integration guarantee

These exist so the firmware (built by the team, months from now) and the app
(built by you, now) **cannot silently disagree.** Every rule below is a place
where two reasonable people would otherwise make different choices. Both sides
must obey these to the letter.

1. **Exact key names, lowercase snake_case:** `session_id`, `t`, `spo2`, `hr`,
   `flag`. Never `sessionId`, `SpO2`, `heartRate`, `Flag`. A renamed key = a
   silent mismatch.
2. **`t`, `spo2`, `hr` are integers.** Round on the device before sending; never
   send floats (`95.4`) or strings (`"95"`). The MAX30102 library returns
   floats — `(int)round(value)` first.
3. **`flag` is exactly the string `"ok"` or `"invalid"`** — lowercase. Never
   `1`/`0`, `true`/`false`, `"OK"`, `"valid"`, or an empty string.
4. **Send every sample, including invalid ones**, marked `flag:"invalid"`. The
   device must **not** drop bad readings — the backend needs them present to
   account for gaps and compute `valid_duration_s`. Dropping them is a mismatch.
5. **`t` is milliseconds since session start** (`millis() - session_start`),
   monotonic. Never seconds, never a sample counter. (This resolves the
   handoff's "ms or sample index" open question — it is **ms**, frozen.)
6. **`session_id` is owned by the device**, from its persisted counter, and
   equals the SD file number. The **backend never invents its own** `session_id`
   — it trusts the value the device sends. Live samples and the later `/night`
   upload for the same night carry the **same** `session_id`.
7. **CSV files always start with the exact header line** `t,spo2,hr,flag`. This
   applies to the device's SD files, the pre-loaded demo dataset CSVs, and the
   mock generator alike. The device's CSV reader assumes the header is present.
8. **Out-of-range values don't crash anything.** If `spo2` is outside 0–100 or
   `hr` outside 30–180, the **backend** re-marks that sample `flag:"invalid"`
   rather than rejecting the whole upload. (Defensive: bad data is expected, not
   exceptional.)
9. **All time-based maths uses `t`, never sample count.** Windowing (§6), the
   duration gate (§7), and ODI (§5) are computed from `t` in milliseconds. This
   is what makes a different real sample rate on the device **harmless** — the
   app keeps working without a code change.

If the firmware obeys 1–9 and the app obeys 1–9, the integration step is "plug
together," not "debug for a week." That is the entire purpose of this document.

---

## 4. SD-card file format (FROZEN) — the same contract on disk

One file per night, named by the persisted counter: `night_001.csv`,
`night_002.csv`, … (handoff §6 — no date in the name, because there's no clock).

**Exact format — header then one row per sample, column order fixed:**
```
t,spo2,hr,flag
0,96,57,ok
1000,96,58,ok
2000,95,58,ok
...
```

This is **byte-identical** to what the mock generator (§12) produces, so a file
made on a laptop and a file made by the ESP32 are interchangeable. Fast-playback
(§8) just reads this file, wraps the rows in the §3.2 envelope, and POSTs it.

---

## 5. Night-summary schema (FROZEN) — returned by `/nights/{id}/summary`

Computed by the backend from the raw samples. The device never sends this.
```json
{
  "session_id":        1,
  "received_date":     "2026-06-14",
  "duration_s":        28740,
  "valid_duration_s":  28100,
  "sample_count":      28741,
  "valid_sample_count":28101,
  "spo2_baseline":     96,
  "spo2_min":          84,
  "spo2_mean":         95.1,
  "time_below_90_s":   540,
  "time_below_88_s":   120,
  "events":            47,
  "odi":               6.0,
  "band":              "mild",
  "insufficient":      false,
  "hourly": [
    { "hour": 0, "events": 5, "spo2_min": 90 },
    { "hour": 1, "events": 7, "spo2_min": 88 }
  ],
  "event_list": [
    { "start_t": 123000, "end_t": 145000, "nadir_spo2": 88, "drop": 5, "duration_s": 22 }
  ]
}
```

| Field | Meaning |
|---|---|
| `received_date` | **backend-stamped on arrival** (the laptop always knows the date; the device doesn't). The only wall-clock value anywhere — and it never touches firmware. |
| `valid_duration_s` | elapsed time covered by `flag == "ok"` samples. The gate (§7) uses **this**, not `duration_s`. |
| `spo2_baseline` | rolling-median baseline (see §11) — the personalised reference, not a fixed 96. |
| `odi` | `events ÷ (valid_duration_s / 3600)` — events per **valid** hour. The firmware threshold detector's number, recomputed in the backend from raw samples for consistency. |
| `band` | `normal` \| `mild` \| `moderate` \| `severe` \| `insufficient` (§7). |
| `event_list` | each detected desaturation: when it started/ended, lowest SpO₂ reached, % drop below baseline, length. Drives the chart markers and the LLM's "what happened at 3am?" answers. |

**Band cutoffs (ODI as an AHI proxy) — FROZEN:**

| ODI (events/hr) | band |
|---|---|
| < 5 | normal |
| 5 – < 15 | mild |
| 15 – < 30 | moderate |
| ≥ 30 | severe |
| (gate failed) | insufficient |

---

## 6. RF feature vector (FROZEN order)

The random forest is the **second-opinion** detector (handoff §2). It never
replaces the firmware threshold — both bands are shown.

**Granularity:** the night is cut into **non-overlapping 60-second windows**.
The RF classifies **each window** as `apnea` / `normal` (binary). This matches
how public datasets are labelled (per-minute apnea scoring), which is what makes
the model trainable and the choice defensible in an interview.

**Per-window feature vector — fixed order, index 0…9. Do not reorder; the
trained model depends on position.** Features are computed in the **backend**
(handoff §9), so they can change without reflashing.

| idx | name | definition (within the window) |
|---|---|---|
| 0 | `spo2_min` | lowest SpO₂ |
| 1 | `spo2_mean` | mean SpO₂ |
| 2 | `spo2_max` | highest SpO₂ |
| 3 | `spo2_range` | `spo2_max − spo2_min` (biggest swing) |
| 4 | `spo2_std` | standard deviation (variability) |
| 5 | `desat_depth` | `baseline − spo2_min` (drop below rolling baseline) |
| 6 | `desat_count` | number of ≥ `DESAT_THRESHOLD_PCT` dips |
| 7 | `secs_below_baseline_minus_thr` | seconds spent ≥ threshold below baseline |
| 8 | `hr_mean` | mean heart rate |
| 9 | `hr_std` | heart-rate variability (arousals cause swings) |

Windows containing only `invalid` samples are **skipped** (not fed to the model).

**How per-window predictions become a band:**
`rf_index = (apnea-labelled windows) ÷ (valid recording hours)`, then mapped with
the **same cutoffs as §5**. Report `rf_confidence` = mean predicted-class
probability over apnea windows. This `rf_index` is an *estimate/proxy*, shown
beside the firmware ODI — say so in the UI; do not present it as ground truth.

**Honest deferred item (handoff §9):** a model trained on a public dataset may
not generalise to *this* MAX30102's real signal. Build the whole pipeline now on
public + mock data; **final validation waits for real captured data.** Report
**sensitivity & specificity** on a held-out split, never accuracy alone.

---

## 7. Invalid flag + duration gate (FROZEN)

Two **separate** decisions — keep them separate (handoff §7):

1. **Is the file long enough?** Pure arithmetic: `valid_duration_s ≥ MIN_DURATION_S`. **Not ML.**
2. **Does it show apnea?** The RF — runs **only if** (1) passes.

If (1) fails → `band = "insufficient"`, `insufficient = true`, **no verdict
computed**. Same message firmware-side on the OLED: *"Session too short for a
result."* App shows: *"Insufficient data — short recording."*

**Why the gate is your job, not the model's:** a random forest will happily
output `apnea`/`normal` on a 4-minute clip — it never refuses garbage. Validate
the input *before* trusting the prediction. (Bridge: same as checking a sensor's
data-valid flag before you latch the reading. Interview-grade point.)

`invalid` samples are excluded from: feature computation, baseline, ODI,
`valid_duration_s`. So a night where the finger fell off for hours can be
`insufficient` even if wall-clock duration looks long — which is the honest
result.

---

## 8. The three data modes (FROZEN)

All three are just *when* and *how* the same rows move; the rows never change.

| Mode | Trigger | Path | Endpoint |
|---|---|---|---|
| **1. Live stream** | session running, WiFi on | one sample POSTed ~1/sec | `POST /reading` |
| **2. Overnight SD record** | every session | every sample written to SD (WiFi may be off to save power) | none (local file) |
| **3. Fast playback** | button press | whole CSV read + sent in one batch | `POST /night` |

SD is the **authoritative** full-night record (handoff §5); the live stream is a
real-time preview. If the stream drops mid-night, the SD file + a later
fast-playback fills the gap. Fast-playback is **not** real-time replay — it
dumps the file as fast as the link allows; the verdict depends only on the
in-file `t` values, not on transfer time.

---

## 9. The 4-page app → endpoint map (FROZEN)

| Page | Shows | Calls | When it runs |
|---|---|---|---|
| **1. Logs / history** | past nights by `session_id` (+ `received_date`), each with its band; pick the active night | `GET /nights`, `GET /nights/{id}/summary` | on load |
| **2. Live data** | real-time SpO₂/HR graph | `GET /live/active`, then poll `GET /live/recent?since_t=` | 1 Hz poll |
| **3. ML verdict** | RF second-opinion band for the selected night | `GET /nights/{id}/verdict` | **auto** (local, instant, free), gated by §7 |
| **4. LLM chat** | ask about the night ("why apnea?", "what at 3am?") | `POST /nights/{id}/chat` | **on demand only** (each call = one Groq request) |

LLM context: pass the **night-summary (§5) directly in the prompt** — one
night's structured summary fits easily. **No RAG, no vector DB** (handoff §8):
it would add complexity for zero benefit at one-night scale. Put the provider
behind one backend function `ask_llm(question, night_summary)` so Groq is
swappable; confirm the live Groq model name at build time.

---

## 10. SQL schema — SQLite (FROZEN shape)

```sql
-- one row per night
CREATE TABLE nights (
  session_id          INTEGER PRIMARY KEY,   -- = the device counter / file number
  received_date       TEXT NOT NULL,         -- backend-stamped 'YYYY-MM-DD'
  duration_s          INTEGER NOT NULL,
  valid_duration_s    INTEGER NOT NULL,
  sample_count        INTEGER NOT NULL,
  valid_sample_count  INTEGER NOT NULL,
  spo2_baseline       INTEGER,
  spo2_min            INTEGER,
  spo2_mean           REAL,
  time_below_90_s     INTEGER,
  time_below_88_s     INTEGER,
  events              INTEGER,
  odi                 REAL,
  band                TEXT NOT NULL,          -- normal|mild|moderate|severe|insufficient
  insufficient        INTEGER NOT NULL,       -- 0/1 (SQLite has no bool)
  rf_index            REAL,                   -- second-opinion rate; NULL if gated out
  rf_confidence       REAL,
  hourly_json         TEXT,                   -- §5 hourly[]  as JSON text
  event_list_json     TEXT,                   -- §5 event_list[] as JSON text
  created_at          TEXT DEFAULT (datetime('now'))
);

-- raw samples (needed for the graph + recomputing features)
CREATE TABLE samples (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  INTEGER NOT NULL REFERENCES nights(session_id),
  t           INTEGER NOT NULL,               -- ms since session start
  spo2        INTEGER NOT NULL,
  hr          INTEGER NOT NULL,
  flag        TEXT NOT NULL                   -- 'ok'|'invalid'
);
CREATE INDEX idx_samples_session_t ON samples(session_id, t);
```

Scalars live in columns; the two variable-length lists (`hourly`, `event_list`)
live as JSON text — fine for SQLite at this scale, and it keeps the schema flat.

---

## 11. Frozen constants (defaults — all settable parameters)

| Name | Default | Settable? | Notes |
|---|---|---|---|
| `SAMPLE_RATE_HZ` | 1 | yes | apnea unfolds over tens of seconds; 1 Hz is plenty |
| `T_UNITS` | milliseconds | **no** | contract-level |
| `DESAT_THRESHOLD_PCT` | 4 | yes | drop ≥4% below baseline = a desaturation (ODI-4). 3% is the alternative clinical convention |
| `BASELINE_WINDOW_S` | 180 | yes | rolling **median** of the last 3 min = baseline |
| `SUSTAINED_LOW_ALERT_S` | 10 | yes | firmware buzzer if SpO₂ stays low this long |
| `TIME_BELOW_THRESHOLDS` | 90, 88 (%) | yes | for `time_below_90_s`, `time_below_88_s` |
| `WINDOW_S` | 60 | **no** | ML window; must match dataset's per-minute labels |
| `MIN_DURATION_S` | 14400 (4 h) | yes | gate floor in production; **lower for dev** (e.g. 240 = 4 min) |
| Band cutoffs | 5 / 15 / 30 | no | §5 table |

`WINDOW_S` and band cutoffs are marked not-settable because changing them
invalidates a trained model / breaks the AHI mapping — change only with a
re-train and a contract bump to v2.

---

## 12. Mock data generator (spec + runnable code)

**Purpose:** unblock the entire app/ML build **before any hardware exists**. It
emits contract-exact CSVs (§4). The backend cannot tell a mock file from a real
ESP32 file — that's the whole point.

**Must produce three files for the demo + tests:**
- `night_normal.csv` — ~8 h, rare shallow dips → lands in **normal**.
- `night_apnea.csv` — ~8 h, frequent 5–10% dips → lands in **moderate/severe**.
- `night_short.csv` — ~4 min normal data → must trip the **gate → insufficient**.

It also sprinkles short `invalid` stretches (finger slipped) so your invalid
handling (§7) gets exercised. A companion `replay_live.py` streams any CSV to
`POST /reading` so the Live page (§9 page 2) can be built and watched without a
device.

`mock_night.py` and `replay_live.py` are delivered as separate runnable files
alongside this spec. Run order to build the app:
```
python mock_night.py apnea  --hours 8   --out night_apnea.csv
python mock_night.py normal --hours 8   --out night_normal.csv
python mock_night.py short  --minutes 4 --out night_short.csv
# then point the backend's /night importer at these, and:
python replay_live.py night_apnea.csv     # watch the Live page update
```

---

## 13. Frozen vs. still-open

**Frozen by this doc** (do not renegotiate without a v2 bump): transport, all
endpoints, both wire schemas, SD CSV format, summary schema, feature-vector
order, the two-step gate, band cutoffs, the three modes, the page→endpoint map,
the SQL shape, `T_UNITS`, `WINDOW_S`.

**Still open (handoff §16 — confirm early, none block app pre-build):**
1. `MIN_DURATION_S` exact production value (4 h is the defensible floor).
2. HTTP-first vs MQTT (HTTP first — already chosen here).
3. The SpO₂-**native** training dataset (PhysioNet UCD / St. Vincent's candidate;
   Apnea-ECG is ECG-only → wrong signal). Confirm at build time.
4. Groq model name (runtime, confirm at build time).

Everything the app/ML side needs is frozen. You can build all four pages, the
backend, the DB, and the RF pipeline against the mock generator today.

---

## A. Integration guards (v1.1 — BINDING; overrides earlier sections where they differ)

These exist to kill the **integration-mismatch class of bugs**: the firmware
(built in 5 months) and the app (built now) silently disagreeing about a field.
Each guard converts a possible silent disagreement into an immediate, named
rejection. **Build the backend so every guard below is an actual check that
returns an error — do not "be lenient."** Lenient parsing is how a 1000× wrong
duration sails through to a fake verdict.

**A1 — `t` is milliseconds, and the backend *enforces* it (overrides any "ms or
sample index" wording anywhere in the knowledge base).**
Device: `t = millis() - session_start` (integer ms). Backend ingest computes the
median gap between consecutive `t` values; if it is **not** between 500–2000 ms
(i.e. not ~1 Hz), reject with `400 "t units look wrong — expected milliseconds at ~1 Hz"`.
*This single guard catches the most dangerous mismatch:* a firmware dev sending
seconds or a sample counter instead of ms.

**A2 — `flag` is exactly lowercase `"ok"` or `"invalid"`.**
Backend rejects any other value with `400 "unknown flag value"`. No silent
coercion of `"OK"`, `"valid"`, `true`, `1`, etc. The firmware must map its
sensor-validity result onto exactly these two strings.

**A3 — `POST /night` takes the CSV file as the body (`Content-Type: text/csv`),
NOT a giant JSON array (overrides §3.2 as the primary form).**
Reason: the ESP32 has ~520 KB RAM and **cannot build a multi-megabyte JSON array
of a full night in memory**. It streams the SD file line-by-line. A CSV body is
byte-identical to the SD file (§4) — zero re-encoding on the device. The JSON
array form in §3.2 is kept only for tiny hand-made test payloads. Backend parses
the CSV and applies A4.

**A4 — the SD/upload file MUST begin with the exact header `t,spo2,hr,flag` in
that column order.**
Backend `/night` rejects a file whose first line isn't exactly that, with
`400 "bad CSV header — expected t,spo2,hr,flag"`. Catches reordered or missing
columns from the firmware side.

**A5 — live samples create a *provisional* night; the SD upload is *authoritative*
(fixes the foreign-key gap in §10).**
The first `/reading` of a new `session_id` makes the backend **upsert a stub row
in `nights`** (`band = "pending"`) so the sample has a parent row to attach to. A
later `/night` for the same `session_id` **replaces that session's samples with
the full SD record** and recomputes the summary. This is exactly handoff §5's
"if the stream drops, the SD sync fills the gap."

**A6 — `session_id` is persisted + monotonic on the device (NVS/Preferences,
survives reflash).**
A second *finalized* `/night` for an already-finalized `session_id` is rejected
with `409 "session already finalized"` — the backend never silently overwrites a
real night. If you reflash and the counter resets, bump it past the highest
number already on the laptop.

**A7 — one baseline function, computed in one place.**
The rolling-median baseline (§11) is a single backend function reused by **both**
the summary/ODI **and** the RF features (§6). Never compute baseline two
different ways — the two outputs must agree by construction.

**A8 — the firmware has ONE producer struct for a reading.**
Define a single struct mirroring the contract row, e.g.
`struct Reading { uint32_t t; uint8_t spo2; uint8_t hr; bool valid; };`. The
**same struct value** is written to SD **and** serialized to the POST. SD and
wire cannot diverge because they come from one source. `getReading()` returns
this struct (see §13 of the handoff: in simulation it's filled from a replayed
CSV value, on hardware from the MAX30102 — everything downstream is identical).

**A9 — invalid rows carry sentinel `0` values in the mock generator.**
So that any code path that forgets to filter `flag == "ok"` before computing
stats produces **obviously broken** numbers (means collapse toward 0), failing
loudly in testing rather than quietly skewing a verdict.

**A10 — `MIN_DURATION_S` is one named constant, printed at startup.**
The backend logs e.g. `GATE: MIN_DURATION_S=240 (DEV)` on boot, so a development
value (4 min) can never silently ship into a real-night demo (which needs ~4 h).

### What this buys you
Integration is now "plug together" because **every field the two sides share is
either (a) validated on arrival and rejected if wrong, or (b) produced from a
single source that both outputs share.** That is how the mismatch class is
designed out — not by hoping both sides remember the same thing.
