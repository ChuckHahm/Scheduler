# USAGE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CAMM (Customer Appointment Minutes Manager) is a single-file Dash application (`CAMM.py`) that displays appointment booking load percentages across five utility service districts: Beach Cities (BC), Metro (CM), Eastern (EA), North Coast (NC), and North East (NE).

## Running

```bash
# Start the multi-page app (development)
python3 CAMMMain.py          # serves on 0.0.0.0:8007

# Production (gunicorn picks up the `server` export from CAMMMain.py)
gunicorn CAMMMain:server

# Standalone CAMM page only (triggers display_output at module load)
python3 CAMM.py
```

Dependencies: `dash`, `dash-bootstrap-components`, `pandas`, `numpy`, `plotly`, `flask`

## Data Flow Architecture

```
CSV files (read on every callback)
  FutureOrders.csv       → get_order_data()  → get_appt_min_sums()  ─┐
  CurrentFutureShift.csv → get_shift_data()  → make_shift_table()    ─┼→ make_load_pct() → display_output()
  ApptJobCodes*.csv      → merged in get_order_data()                ─┘
```

All data files are read fresh on each callback — no in-memory caching.

Two private helpers are shared across the data pipeline to avoid duplication:
- `_assign_district(df)` — maps `AREA` → `District` with override rules (CMN→EA, OCN/OCS→NC, CMHC→BC)
- `_classify_appt_type(df)` — adds `ApptType` column (AM / PM / AllDay) from ELIGIBLE/EXPIRES hours

## Key Configuration Constants

| Constant | Default | Purpose |
|---|---|---|
| `APPT_LOAD_FACTOR` | 0.85 | Fraction of shift minutes available for appointments |
| `THRESH` | 120 | Percent threshold; above this → red "E", below → green "U" |
| `DISPLAY_DAYS` | 20 | (defined but not actively used in current callbacks) |
| `TEST_MODE` | False | When True, skips auto-calling `display_output` at module load |
| `PAUSE_INTERRUPT` | True | When True, sets slow refresh interval (200,000 s); False = 1 s |

## District / Area Mapping

Supervisors map to districts via `SUPDict`. Some areas override the default 2-char prefix rule:
- `CMN` → EA, `OCN`/`OCS` → NC, `CMHC` → BC

## Callback Structure

- **`update_interval`** — fires every 10 s, updates clock display only.
- **`display_output`** — fires on slow interval or view toggle; reads all CSV data, computes `LoadPct`, and populates the five district `DataTable`s. In `CallCenterView` mode only the "Loading Pct" row is shown; `CSFView` shows all rows.
- **`update_table`** — fires on cell click in any district table; calls `GetApptSlotData()` to drill into the appointment and shift detail for that district/date/slot.
- Two download callbacks export `ApptMinTable.csv` and `ShiftTable.csv` from the `data/` directory.

## Style / Color Logic (`GetStyle`)

Conditional styles are applied to `DataTable` columns by date. Cells containing `"U"` (under threshold) → green; `"E"` (exceeded) → crimson; `"NO SHIFT"` → black. The suffix is appended to the LoadPct string inside `display_output`.

## Data File Paths

Production paths are hardcoded to `/app/Projects/ApptWin/data/`. The `data/` relative path is used for output files (`Appointments.csv`, `ShiftTable.csv`, `ApptMinTable.csv`). When running locally, create a `data/` directory and update the `ORDER_INFILE` / `FUTURE_SHIFT_FILE` / `JOB_TABLE_FILE` constants.
