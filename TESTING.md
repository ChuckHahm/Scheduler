# Running Tests

Tests are in `test_schedule.py` and use `pytest`. No data files or a running Dash app are needed — all file I/O is mocked, and Dash/Flask imports are stubbed at the top of the test module.

## Run all tests

```bash
python3 -m pytest test_schedule.py
```

## Run with verbose output (shows each test name)

```bash
python3 -m pytest test_schedule.py -v
```

## Run a single test class

```bash
python3 -m pytest test_schedule.py::TestAssignDistrict
python3 -m pytest test_schedule.py::TestClassifyApptType
python3 -m pytest test_schedule.py::TestGetDateList
python3 -m pytest test_schedule.py::TestGetStyle
python3 -m pytest test_schedule.py::TestMakeShiftTable
python3 -m pytest test_schedule.py::TestMakeLoadPct
```

## Run a single test

```bash
python3 -m pytest test_schedule.py::TestMakeShiftTable::test_avail_mins_with_unit_load_factor
```

## Install pytest if needed

```bash
pip install pytest
```

## What is covered

| Class | Functions tested |
|---|---|
| `TestAssignDistrict` | `_assign_district` — all district override rules, no-mutation |
| `TestClassifyApptType` | `_classify_appt_type` — AM/PM/AllDay slots, unmatched rows, no-mutation |
| `TestGetDateList` | `get_date_list` — weekdays only, format, sort order |
| `TestGetStyle` | `get_style` — green/crimson/black style groups and filter queries |
| `TestMakeShiftTable` | `make_shift_table` — AvailMins math, load factor, Closed/Open state, tech count |
| `TestMakeLoadPct` | `make_load_pct` — LoadPct calculation, zero-division, negative clamp, over-threshold |
