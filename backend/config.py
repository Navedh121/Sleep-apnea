# config.py — all named constants for the SpO2 backend.
#
# Every magic number in the spec lives here so there is ONE place to change it.
# A10 (integration guard): MIN_DURATION_S is printed on import so a dev value
# can never silently ship into a real-night demo.

# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------
SAMPLE_RATE_HZ = 1          # samples per second (spec §11)
T_UNITS = "milliseconds"    # contract-level, do not change (spec §11)

# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------
BASELINE_WINDOW_S = 180     # rolling-median window = last 3 minutes (spec §11)

# ---------------------------------------------------------------------------
# Desaturation / ODI
# ---------------------------------------------------------------------------
DESAT_THRESHOLD_PCT = 4     # a drop ≥ 4 % below baseline counts as an event (ODI-4)

# ---------------------------------------------------------------------------
# Time-below thresholds (used in night summary §5)
# ---------------------------------------------------------------------------
TIME_BELOW_90_THRESHOLD = 90   # % SpO2 — count seconds spent below this
TIME_BELOW_88_THRESHOLD = 88   # % SpO2 — count seconds spent below this

# ---------------------------------------------------------------------------
# ML window
# ---------------------------------------------------------------------------
WINDOW_S = 60               # each RF window is exactly 60 s (spec §6, NOT settable)

# ---------------------------------------------------------------------------
# Duration gate (spec §7 + A10)
# ---------------------------------------------------------------------------
# DEV value = 4 minutes so you can test with short mock files.
# Change to 14400 (4 hours) before any real overnight use.
MIN_DURATION_S = 240        # seconds — gate floor for Phase B1/B2/B3/C/D/E dev

# ---------------------------------------------------------------------------
# Band cutoffs — ODI events/hr → severity band (spec §5, NOT settable)
# ---------------------------------------------------------------------------
BAND_NORMAL_MAX   = 5       # ODI < 5  → normal
BAND_MILD_MAX     = 15      # ODI < 15 → mild
BAND_MODERATE_MAX = 30      # ODI < 30 → moderate
                            # ODI ≥ 30 → severe

# ---------------------------------------------------------------------------
# A1 guard — expected inter-sample gap range (milliseconds)
# At 1 Hz each gap should be ~1000 ms; allow 500–2000 ms for jitter.
# ---------------------------------------------------------------------------
A1_GAP_MIN_MS = 500
A1_GAP_MAX_MS = 2000

# ---------------------------------------------------------------------------
# A1 live-stream buffer — how many t-values to keep per session for gap check
# ---------------------------------------------------------------------------
A1_LIVE_BUFFER_SIZE = 20

# ---------------------------------------------------------------------------
# A10: print gate value at import time so it's visible in the server log
# ---------------------------------------------------------------------------
print(f"GATE: MIN_DURATION_S={MIN_DURATION_S} (DEV — change to 14400 for production)")
