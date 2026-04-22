import re
import sys
import types
import unittest
from unittest.mock import patch, MagicMock
from datetime import date, timedelta

import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Stub out Dash / Flask so Schedule.py can be imported in a plain Python env
# ---------------------------------------------------------------------------
def _stub_module(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

_noop = lambda *a, **kw: None
_html_elem = lambda *a, **kw: None

dbc_mod = _stub_module('dash_bootstrap_components',
    Row=_html_elem, Col=_html_elem, NavbarSimple=_html_elem,
    DropdownMenu=_html_elem, DropdownMenuItem=_html_elem,
    themes=types.SimpleNamespace(BOOTSTRAP='bootstrap'),
    Container=_html_elem,
)

dash_mod = _stub_module('dash',
    Dash=MagicMock, register_page=_noop,
    page_registry=[], page_container=None,
    dcc=types.SimpleNamespace(
        Interval=_html_elem, RadioItems=_html_elem, Markdown=_html_elem,
        Download=_html_elem, send_data_frame=_noop,
    ),
    html=types.SimpleNamespace(
        Div=_html_elem, H2=_html_elem, H4=_html_elem, H5=_html_elem,
        Br=_html_elem, Button=_html_elem,
    ),
    dash_table=types.SimpleNamespace(DataTable=_html_elem),
    Input=MagicMock, Output=MagicMock, State=MagicMock, callback=lambda *a, **kw: (lambda f: f),
)
_stub_module('dash.dash_table', DataTable=_html_elem,
             FormatTemplate=types.SimpleNamespace(percentage=lambda n: None))
_stub_module('dash.dash_table.FormatTemplate', percentage=lambda n: None)

flask_mod = _stub_module('flask', request=MagicMock())

import Schedule
from Schedule import (
    _assign_district,
    _classify_appt_type,
    get_date_list,
    get_style,
    make_shift_table,
    make_load_pct,
    APPT_LOAD_FACTOR,
    THRESH,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_shift_df(period_names=None):
    """Minimal shift DataFrame as returned by get_shift_data()."""
    tomorrow = (pd.Timestamp.now() + pd.Timedelta('1 day')).strftime('%Y-%m-%d')
    if period_names is None:
        period_names = ['Working Loading', 'Working Loading',
                        'Appt Gate Closed', 'Working Loading']
    return pd.DataFrame({
        'SUPERVISOR_ID': ['SUP01'] * 4,
        'District':      ['BC']    * 4,
        'TECH_NAME':     ['Alice', 'Alice', 'Bob', 'Bob'],
        'SHIFT_DATE':    [tomorrow] * 4,
        'PERIOD_NAME':   period_names,
        'SHIFT_TYPE':    ['BASE', 'EXCP', 'BASE', 'EXCP'],
        'SHIFT_MIN':     [480,     60,     480,     30],
        'SKILL':         ['S1']   * 4,
        'SHIFT':         ['D']    * 4,
        'SHIFT_NAME':    ['Day', 'Lunch', 'Day', 'Lunch'],
    })


def _make_appt_sums(district='BC', appt_date=None, appt_type='AM',
                    count=5, total_min=200):
    if appt_date is None:
        appt_date = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
    idx = pd.MultiIndex.from_tuples(
        [(district, appt_date, appt_type)],
        names=['District', 'APPT_DATE', 'ApptType'],
    )
    return pd.DataFrame({'count': [count], 'sum': [total_min]}, index=idx)


def _make_district_stats(district='BC', shift_date=None, avail_sum=400, avail_count=2):
    if shift_date is None:
        shift_date = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
    idx = pd.MultiIndex.from_tuples(
        [(district, shift_date)],
        names=['District', 'SHIFT_DATE'],
    )
    return pd.DataFrame({'sum': [avail_sum], 'count': [avail_count]}, index=idx)


# ---------------------------------------------------------------------------
# _assign_district
# ---------------------------------------------------------------------------

class TestAssignDistrict(unittest.TestCase):

    def _df(self, areas):
        return pd.DataFrame({'AREA': areas})

    def test_standard_two_char_prefix(self):
        df = self._df(['BCNE', 'NESM', 'CMCV', 'EAW', 'NCCB'])
        result = _assign_district(df)
        self.assertEqual(list(result['District']), ['BC', 'NE', 'CM', 'EA', 'NC'])

    def test_cmn_overrides_to_ea(self):
        self.assertEqual(_assign_district(self._df(['CMN']))['District'].iloc[0], 'EA')

    def test_ocn_overrides_to_nc(self):
        self.assertEqual(_assign_district(self._df(['OCN']))['District'].iloc[0], 'NC')

    def test_ocs_overrides_to_nc(self):
        self.assertEqual(_assign_district(self._df(['OCS']))['District'].iloc[0], 'NC')

    def test_cmhc_overrides_to_bc(self):
        self.assertEqual(_assign_district(self._df(['CMHC']))['District'].iloc[0], 'BC')

    def test_mixed_overrides(self):
        df = self._df(['CMN', 'OCN', 'OCS', 'CMHC', 'BCNE'])
        expected = ['EA', 'NC', 'NC', 'BC', 'BC']
        self.assertEqual(list(_assign_district(df)['District']), expected)

    def test_does_not_mutate_input(self):
        df = self._df(['CMN'])
        _assign_district(df)
        self.assertNotIn('District', df.columns)


# ---------------------------------------------------------------------------
# _classify_appt_type
# ---------------------------------------------------------------------------

class TestClassifyApptType(unittest.TestCase):

    def _df(self, eligible_hours, expires_hours):
        elig = pd.to_datetime([f'2025-06-10 {h:02d}:00:00' for h in eligible_hours])
        exp  = pd.to_datetime([f'2025-06-10 {h:02d}:00:00' for h in expires_hours])
        return pd.DataFrame({'ELIGIBLE': elig, 'EXPIRES': exp})

    def test_am(self):
        self.assertEqual(_classify_appt_type(self._df([8], [12]))['ApptType'].iloc[0], 'AM')

    def test_pm(self):
        self.assertEqual(_classify_appt_type(self._df([12], [16]))['ApptType'].iloc[0], 'PM')

    def test_allday(self):
        self.assertEqual(_classify_appt_type(self._df([8], [23]))['ApptType'].iloc[0], 'AllDay')

    def test_unmatched_combo_not_classified(self):
        result = _classify_appt_type(self._df([9], [17]))
        # Row doesn't match any known slot — value should not be a valid type label.
        # (pandas 2.1 stores 'na' rather than float NaN for unset string columns)
        val = result['ApptType'].iloc[0] if 'ApptType' in result.columns else None
        self.assertNotIn(val, ('AM', 'PM', 'AllDay'))

    def test_mixed_rows(self):
        result = _classify_appt_type(self._df([8, 12, 8, 9], [12, 16, 23, 17]))
        types = result['ApptType'].tolist()
        self.assertEqual(types[0], 'AM')
        self.assertEqual(types[1], 'PM')
        self.assertEqual(types[2], 'AllDay')
        self.assertNotIn(types[3], ('AM', 'PM', 'AllDay'))

    def test_does_not_mutate_input(self):
        df = self._df([8], [12])
        _classify_appt_type(df)
        self.assertNotIn('ApptType', df.columns)


# ---------------------------------------------------------------------------
# get_date_list
# ---------------------------------------------------------------------------

class TestGetDateList(unittest.TestCase):

    def test_all_weekdays(self):
        for entry in get_date_list():
            day = pd.to_datetime(entry[4:])
            self.assertLess(day.weekday(), 5, f'{entry} is a weekend day')

    def test_starts_tomorrow_or_later(self):
        tomorrow = date.today() + timedelta(days=1)
        first = pd.to_datetime(get_date_list()[0][4:]).date()
        self.assertGreaterEqual(first, tomorrow)

    def test_format_matches_pattern(self):
        pattern = re.compile(r'^[A-Z][a-z]{2} \d{4}-\d{2}-\d{2}$')
        for entry in get_date_list():
            self.assertRegex(entry, pattern)

    def test_returns_entries(self):
        self.assertGreater(len(get_date_list()), 0)

    def test_dates_are_sorted_ascending(self):
        dates = [pd.to_datetime(e[4:]) for e in get_date_list()]
        self.assertEqual(dates, sorted(dates))


# ---------------------------------------------------------------------------
# get_style
# ---------------------------------------------------------------------------

class TestGetStyle(unittest.TestCase):

    def test_length_is_three_times_date_list(self):
        n = len(get_date_list())
        self.assertEqual(len(get_style('BC')), 3 * n)

    def test_green_group_contains_U(self):
        n = len(get_date_list())
        for s in get_style('BC')[:n]:
            self.assertIn('"U"', s['if']['filter_query'])
            self.assertEqual(s['background_color'], 'green')

    def test_crimson_group_contains_E(self):
        n = len(get_date_list())
        for s in get_style('BC')[n:2 * n]:
            self.assertIn('"E"', s['if']['filter_query'])
            self.assertEqual(s['background_color'], 'crimson')

    def test_black_group_contains_no_shift(self):
        n = len(get_date_list())
        for s in get_style('BC')[2 * n:]:
            self.assertIn('"NO SHIFT"', s['if']['filter_query'])
            self.assertEqual(s['backgroundColor'], 'black')

    def test_column_ids_match_date_list_format(self):
        # Columns in style dicts are formatted as 'YYYY-MM-DD Ddd'
        date_list = get_date_list()
        expected = [f"{x[4:14]} {x[:3]}" for x in date_list]
        n = len(date_list)
        col_ids = [s['if']['column_id'] for s in get_style('BC')[:n]]
        self.assertEqual(col_ids, expected)


# ---------------------------------------------------------------------------
# make_shift_table
# ---------------------------------------------------------------------------

class TestMakeShiftTable(unittest.TestCase):

    @patch('Schedule.get_shift_data')
    def test_result_keys(self, mock_shift):
        mock_shift.return_value = _make_shift_df()
        result = make_shift_table()
        self.assertIn('TechWorkingTime', result)
        self.assertIn('DistrictShiftStats', result)
        self.assertIn('ShiftState', result)

    @patch('Schedule.get_shift_data')
    def test_avail_mins_with_unit_load_factor(self, mock_shift):
        mock_shift.return_value = _make_shift_df()
        tomorrow = (pd.Timestamp.now() + pd.Timedelta('1 day')).strftime('%Y-%m-%d')
        stats = make_shift_table(load_factor=1.0)['DistrictShiftStats']
        # Alice: (480-60)*1.0=420, Bob: (480-30)*1.0=450 → sum=870
        total = stats.xs(('BC', tomorrow))['sum']
        self.assertAlmostEqual(total, 870.0)

    @patch('Schedule.get_shift_data')
    def test_load_factor_scales_linearly(self, mock_shift):
        mock_shift.return_value = _make_shift_df()
        tomorrow = (pd.Timestamp.now() + pd.Timedelta('1 day')).strftime('%Y-%m-%d')
        full = make_shift_table(load_factor=1.0)['DistrictShiftStats'].xs(('BC', tomorrow))['sum']
        half = make_shift_table(load_factor=0.5)['DistrictShiftStats'].xs(('BC', tomorrow))['sum']
        self.assertAlmostEqual(half, full * 0.5)

    @patch('Schedule.get_shift_data')
    def test_default_load_factor_matches_constant(self, mock_shift):
        mock_shift.return_value = _make_shift_df()
        tomorrow = (pd.Timestamp.now() + pd.Timedelta('1 day')).strftime('%Y-%m-%d')
        default = make_shift_table()['DistrictShiftStats'].xs(('BC', tomorrow))['sum']
        explicit = make_shift_table(load_factor=APPT_LOAD_FACTOR)['DistrictShiftStats'].xs(('BC', tomorrow))['sum']
        self.assertAlmostEqual(default, explicit)

    @patch('Schedule.get_shift_data')
    def test_shift_state_closed_when_any_closed_period(self, mock_shift):
        # 'Appt Gate Closed' contains 'Closed' → state should be 'Closed'
        mock_shift.return_value = _make_shift_df()
        tomorrow = (pd.Timestamp.now() + pd.Timedelta('1 day')).strftime('%Y-%m-%d')
        state = make_shift_table()['ShiftState'].xs(('BC', tomorrow))['PERIOD_NAME']
        self.assertEqual(state, 'Closed')

    @patch('Schedule.get_shift_data')
    def test_shift_state_open_when_no_closed_period(self, mock_shift):
        df = _make_shift_df(period_names=['Working Loading'] * 4)
        mock_shift.return_value = df
        tomorrow = (pd.Timestamp.now() + pd.Timedelta('1 day')).strftime('%Y-%m-%d')
        state = make_shift_table()['ShiftState'].xs(('BC', tomorrow))['PERIOD_NAME']
        self.assertEqual(state, 'Open')

    @patch('Schedule.get_shift_data')
    def test_tech_count_per_district(self, mock_shift):
        mock_shift.return_value = _make_shift_df()
        tomorrow = (pd.Timestamp.now() + pd.Timedelta('1 day')).strftime('%Y-%m-%d')
        count = make_shift_table()['DistrictShiftStats'].xs(('BC', tomorrow))['count']
        self.assertEqual(count, 2)   # Alice and Bob


# ---------------------------------------------------------------------------
# make_load_pct
# ---------------------------------------------------------------------------

class TestMakeLoadPct(unittest.TestCase):

    def _mock_ms(self, shift_date=None, avail_sum=400):
        return {
            'DistrictShiftStats': _make_district_stats(shift_date=shift_date, avail_sum=avail_sum),
            'ShiftState': pd.DataFrame(),
            'TechWorkingTime': pd.DataFrame(),
        }

    @patch('Schedule.make_shift_table')
    @patch('Schedule.get_appt_min_sums')
    def test_result_keys(self, mock_appt, mock_shift):
        d = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        mock_appt.return_value = _make_appt_sums(appt_date=d)
        mock_shift.return_value = self._mock_ms(shift_date=d)
        result = make_load_pct()
        self.assertIn('LoadPct', result)
        self.assertIn('MS', result)
        self.assertIn('ApptMinSums', result)

    @patch('Schedule.make_shift_table')
    @patch('Schedule.get_appt_min_sums')
    def test_load_pct_calculation(self, mock_appt, mock_shift):
        d = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        mock_appt.return_value = _make_appt_sums(appt_date=d, total_min=200)
        mock_shift.return_value = self._mock_ms(shift_date=d, avail_sum=400)
        load_pct = make_load_pct()['LoadPct'].xs(('BC', d))['LoadPct']
        self.assertAlmostEqual(load_pct, 50.0)   # 200/400 * 100

    @patch('Schedule.make_shift_table')
    @patch('Schedule.get_appt_min_sums')
    def test_zero_shift_mins_gives_zero_pct(self, mock_appt, mock_shift):
        d = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        mock_appt.return_value = _make_appt_sums(appt_date=d, total_min=200)
        mock_shift.return_value = self._mock_ms(shift_date=d, avail_sum=0)
        load_pct = make_load_pct()['LoadPct'].xs(('BC', d))['LoadPct']
        self.assertEqual(load_pct, 0.0)

    @patch('Schedule.make_shift_table')
    @patch('Schedule.get_appt_min_sums')
    def test_load_pct_never_negative(self, mock_appt, mock_shift):
        d = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        mock_appt.return_value = _make_appt_sums(appt_date=d, total_min=0)
        mock_shift.return_value = self._mock_ms(shift_date=d, avail_sum=400)
        load_pct = make_load_pct()['LoadPct'].xs(('BC', d))['LoadPct']
        self.assertGreaterEqual(load_pct, 0.0)

    @patch('Schedule.make_shift_table')
    @patch('Schedule.get_appt_min_sums')
    def test_over_threshold_exceeds_thresh_constant(self, mock_appt, mock_shift):
        d = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        # 600 appt minutes / 400 shift minutes = 150% > THRESH(120)
        mock_appt.return_value = _make_appt_sums(appt_date=d, total_min=600)
        mock_shift.return_value = self._mock_ms(shift_date=d, avail_sum=400)
        load_pct = make_load_pct()['LoadPct'].xs(('BC', d))['LoadPct']
        self.assertGreater(load_pct, THRESH)


# ---------------------------------------------------------------------------
# Date-drop regression (root cause: how='left' on load_col in display_output)
# ---------------------------------------------------------------------------

class TestDisplayDateCompleteness(unittest.TestCase):
    """
    Reproduces the bug where a date with no shift AND no appointment data is
    silently dropped from the displayed table.

    Root cause — display_output line:
        dft = load_col.merge(dft, left_index=True, right_index=True, how='left').T

    load_col only contains dates present in make_load_pct() output, which is
    built from the union of shift and appointment CSV data.  A date that appears
    in get_date_list() but has no records in either CSV (e.g. May 13 if no
    shifts are scheduled and no orders exist) is absent from load_col, so the
    how='left' join removes it even though all three earlier merges preserved it.

    Fix: reindex load_col against the full date_list before merging so that
    data-less dates receive '0% U' instead of being dropped.
    """

    D_WITH_DATA    = '2026-04-23'
    D_WITHOUT_DATA = '2026-05-13'   # the "missing" date

    def _build_load_col(self, dates_with_data):
        """Simulate load_col as built inside display_output."""
        idx = pd.Index(dates_with_data)
        col = pd.DataFrame({'LoadPct': ['50'] * len(idx)}, index=idx)
        col['LoadPct'] = col['LoadPct'].apply(lambda x: f"{x}% U")
        return col

    def _build_dft(self, all_dates):
        """Simulate dft after the three earlier (right/outer) merges — all dates present."""
        return pd.DataFrame(
            {'AM': ['3', '0'], 'PERIOD_NAME': ['Open', 'NO SHIFT']},
            index=pd.Index(all_dates),
        )

    def test_date_without_data_is_dropped_by_left_merge(self):
        """Demonstrates the bug: how='left' drops D_WITHOUT_DATA."""
        load_col = self._build_load_col([self.D_WITH_DATA])          # no May 13
        dft      = self._build_dft([self.D_WITH_DATA, self.D_WITHOUT_DATA])

        result = load_col.merge(dft, left_index=True, right_index=True, how='left').T
        self.assertNotIn(self.D_WITHOUT_DATA, result.columns,
                         "Bug confirmed: date without data is absent from output")

    def test_reindex_fix_preserves_date_without_data(self):
        """Verifies the fix: reindexing load_col before merging keeps D_WITHOUT_DATA."""
        all_dates = [self.D_WITH_DATA, self.D_WITHOUT_DATA]
        load_col  = self._build_load_col([self.D_WITH_DATA])         # no May 13
        dft       = self._build_dft(all_dates)

        # Fix: reindex load_col to the full date list before merging
        load_col = load_col.reindex(pd.Index(all_dates), fill_value='0% U')
        result = load_col.merge(dft, left_index=True, right_index=True, how='left').T

        self.assertIn(self.D_WITH_DATA,    result.columns)
        self.assertIn(self.D_WITHOUT_DATA, result.columns,
                      "After fix: date without data must appear in output")
        self.assertEqual(result.loc['LoadPct', self.D_WITHOUT_DATA], '0% U')


if __name__ == '__main__':
    unittest.main(verbosity=2)
