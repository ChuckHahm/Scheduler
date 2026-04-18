#
# Customer Appointment Minutes Manager (CAMM)
#
import dash
import pandas as pd
import numpy as np
from datetime import datetime as dt
import logging
import warnings

import dash_bootstrap_components as dbc
from dash import dcc, html, Input, Output, State, dash_table, callback
from dash.dash_table import FormatTemplate
from flask import request

warnings.simplefilter(action='ignore', category=FutureWarning)

# --- Constants ---
APPT_LOAD_FACTOR  = 0.85
THRESH            = 120
DISPLAY_DAYS      = 20
DAY_SHIFT_MINS    = 420
PAUSE_INTERRUPT   = True
TEST_MODE         = False

SUP_DISTRICT = {'SUP01': 'BC', 'SUP02': 'NE', 'SUP03': 'EA', 'SUP04': 'CM', 'SUP05': 'NC'}
DB_SECOND_INTERVAL = 200_000 if PAUSE_INTERRUPT else 1

ORDER_INFILE      = '/app/Projects/ApptWin/data/FutureOrders.csv'
FUTURE_SHIFT_FILE = '/app/Projects/ApptWin/data/CurrentFutureShift.csv'
JOB_TABLE_FILE    = '/app/Projects/ApptWin/data/ApptJobCodesAug25_2024.csv'

# Row index 0/1/2 from an active_cell click maps to these ApptType short labels
_DISTRICTS    = ['BC', 'CM', 'EA', 'NC', 'NE']
_CELL_ROW_LUT = ['AM', 'PM', 'AllDay']
_ROW_RENAME   = {
    'AM': 'Num AM Appts', 'PM': 'Num PM Appts', 'AllDay': 'Num All Day Appts',
    'PERIOD_NAME': 'Open-Closed State', 'LoadPct': 'Loading Pct',
    'TotalAppts': 'Total Appts', 'count': 'Num Shifts',
}
_ROW_ORDER = ['Num AM Appts', 'Num PM Appts', 'Num All Day Appts',
              'Total Appts', 'Loading Pct', 'Open-Closed State', 'Num Shifts']

pd.set_option('display.max_columns', None)
logging.basicConfig(level=logging.INFO, format=' %(asctime)s - %(levelname)s - %(message)s')

try:
    dash.register_page(__name__, path='/')
except Exception as e:
    logging.info(f'CAMM register_page: {e}')

# =============================================================================
# Data Layer
# =============================================================================

def _assign_district(df):
    """Map AREA → District with override rules for areas that cross district boundaries."""
    df = df.copy()
    df['District'] = df['AREA'].astype(str).str[:2]
    df.loc[df.AREA == 'CMN', 'District'] = 'EA'
    df.loc[df.AREA.isin(['OCN', 'OCS']), 'District'] = 'NC'
    df.loc[df.AREA == 'CMHC', 'District'] = 'BC'
    return df


def _classify_appt_type(df):
    """Adds ApptType column (AM / PM / AllDay) based on ELIGIBLE and EXPIRES hour."""
    df = df.copy()
    hour = df.ELIGIBLE.dt.hour
    exp  = df.EXPIRES.dt.hour
    df.loc[(hour == 8)  & (exp == 12), 'ApptType'] = 'AM'
    df.loc[(hour == 12) & (exp == 16), 'ApptType'] = 'PM'
    df.loc[(hour == 8)  & (exp == 23), 'ApptType'] = 'AllDay'
    return df


def get_date_list():
    start = dt.now().date() + pd.to_timedelta('1 day')
    return [x.strftime('%a %Y-%m-%d')
            for x in pd.date_range(start=start, periods=30)
            if x.weekday() < 5]


def get_shift_data():
    df = pd.read_csv(FUTURE_SHIFT_FILE)
    df = df[df.SUPERVISOR_ID.isin(SUP_DISTRICT)]
    df['District'] = df['SUPERVISOR_ID'].map(SUP_DISTRICT)
    df = df[~df.TECH_NAME.str.contains('zz')]
    df = df[pd.to_datetime(df.SHIFT_DATE) > pd.to_datetime(dt.now())]
    return df


def get_agc_state_list(district):
    df = get_shift_data()
    closed = df.loc[df.PERIOD_NAME.str.contains('Closed'),
                    ['SHIFT_DATE', 'District']].drop_duplicates()
    closed['SHIFT_DATE'] = pd.to_datetime(closed['SHIFT_DATE']).dt.strftime('%Y-%m-%d %a')
    return closed[closed.District == district]


def get_order_data():
    orders = pd.read_csv(ORDER_INFILE)
    old_jc = pd.read_csv('data/JobCodesMar8_2022.csv')
    new_jc = pd.read_csv('data/ApptJobCodesAug25_2024.csv')
    jc = old_jc.merge(new_jc, left_on='JOBCODE', right_on='JOB_CODE_NAME', how='outer')
    for col in ('CREATED', 'EXPIRES', 'ELIGIBLE'):
        orders[col] = (pd.to_datetime(orders[col])
                       .dt.tz_localize('UTC')
                       .dt.tz_convert('America/Los_Angeles')
                       .apply(lambda x: x.tz_localize(None)))
    df = orders.merge(jc, on='JOBCODE')
    return df[df.APPT_BOOKING_FLAG == 'Y']


def get_appt_min_sums():
    date_list = get_date_list()
    df = _classify_appt_type(_assign_district(get_order_data()))
    df['APPT_DATE'] = df.ELIGIBLE.dt.date.astype(str)
    df['StdNumMin'] = df['StdNumMin'] + 13
    date_index = pd.DataFrame(index=[x[4:] for x in date_list])
    df = df.merge(date_index, left_on='APPT_DATE', right_index=True)
    return (df.groupby(['District', 'APPT_DATE', 'ApptType'])
              .agg({'StdNumMin': ['count', 'sum']})
              .droplevel(0, axis=1)
              .sort_index())


def make_shift_table(load_factor=APPT_LOAD_FACTOR):
    shift = get_shift_data()
    working_fltr = shift.PERIOD_NAME.str.contains('Closed|Working Loading')
    working = (shift.loc[working_fltr, ['District', 'TECH_NAME', 'SHIFT_DATE']]
               .drop_duplicates()
               .set_index(['District', 'TECH_NAME', 'SHIFT_DATE']))
    tech_sum = (shift.groupby(['District', 'SHIFT_DATE', 'TECH_NAME', 'SHIFT_TYPE'])
                .agg({'SHIFT_MIN': 'sum'})
                .unstack().fillna(0)
                .droplevel(0, axis=1))
    tech_sum['AvailMins'] = (tech_sum['BASE'] - tech_sum['EXCP']) * load_factor
    shift_state = (shift[working_fltr]
                   .groupby(['District', 'SHIFT_DATE'])
                   .agg({'PERIOD_NAME': lambda x: any(x.str.contains('Closed'))}))
    shift_state['PERIOD_NAME'] = shift_state['PERIOD_NAME'].map({True: 'Closed', False: 'Open'})
    tech_working  = working.merge(tech_sum, left_index=True, right_index=True)
    district_stats = (tech_working.groupby(level=['District', 'SHIFT_DATE'])
                      .agg({'AvailMins': ['sum', 'count']})
                      .droplevel(0, axis=1))
    return {'TechWorkingTime': tech_working, 'DistrictShiftStats': district_stats, 'ShiftState': shift_state}


def make_load_pct():
    appt_sums = get_appt_min_sums()
    ms = make_shift_table()
    district_shift = ms['DistrictShiftStats']['sum']
    district_appt  = appt_sums.groupby(level=['District', 'APPT_DATE'])['sum'].sum().sort_index()
    df = pd.concat([district_shift, district_appt], axis=1, keys=['Shift', 'Appt']).fillna(0)
    df['LoadPct'] = (100 * df['Appt'] / df['Shift']).replace([np.inf, -np.inf], 0).clip(lower=0)
    return {'LoadPct': df, 'MS': ms, 'ApptMinSums': appt_sums}

# =============================================================================
# Styles
# =============================================================================

def get_style(district):
    cols = [f"{x[4:14]} {x[:3]}" for x in get_date_list()]
    return (
        [{'if': {'filter_query': f'{{{c}}} contains "U"', 'column_id': c},
          'background_color': 'green', 'color': 'white'} for c in cols] +
        [{'if': {'filter_query': f'{{{c}}} contains "E"', 'column_id': c},
          'background_color': 'crimson', 'color': 'white'} for c in cols] +
        [{'if': {'filter_query': f'{{{c}}} contains "NO SHIFT"', 'column_id': c},
          'backgroundColor': 'black', 'color': 'white'} for c in cols]
    )

# =============================================================================
# Layout
# =============================================================================

def _district_table(table_id, sortable=False):
    return dash_table.DataTable(
        id=table_id,
        style_header={'whiteSpace': 'normal', 'height': 'auto'},
        style_data_conditional=[],
        style_table={'overflowX': 'auto'},
        sort_action='native' if sortable else 'none',
        filter_action='none',
    )


layout = html.Div(style={'marginTop': 0}, children=[
    dcc.Interval(id='CWZIntervalCWID',    interval=DB_SECOND_INTERVAL * 10 * 1000, n_intervals=0),
    dcc.Interval(id='CWZIntervalSecCWID', interval=10_000, n_intervals=0),
    html.Br(),
    html.H2('Customer Appointment Minutes Manager',
            style={'textAlign': 'center', 'color': 'black', 'marginTop': 0}),
    dbc.Row([
        dbc.Col(html.H5(f'Shift Loading Factor: {APPT_LOAD_FACTOR}')),
        dbc.Col(html.H5(f'Workload Threshold Percent: {THRESH}')),
        dbc.Col(html.H5('Current Time:')),
        dbc.Col(html.H5(id='CWZTimestamp2ID')),
        dbc.Col(dcc.RadioItems(
            id='CCC_CSFSelectID',
            options=[{'label': 'Call Center View', 'value': 'CallCenterView'},
                     {'label': 'CSF View',         'value': 'CSFView'}],
            value='CallCenterView',
            inline=False,
        )),
    ]),
    dbc.Row([dbc.Col(dcc.Markdown(
        f'*Workload color coding: red if bookings exceed {THRESH}% capacity, green if below*'
    ))]),
    html.H2('Beach Cities Percent Booked'),
    html.H4('District/Area: BCNE,BCNW,BCSE,BCSW,BCMC,CMHC'),
    _district_table('BCTableID'),
    html.Br(), html.Br(),
    html.H2('Metro Percent Booked'),
    html.H4('District/Area: CMCV,CMHB,CMNC,CMS,CMOT'),
    _district_table('CMTableID'),
    html.Br(), html.Br(),
    html.H2('Eastern Percent Booked'),
    html.H4('District/Area: EAW,EAS,EAN,EAME,EACE,CMN,EAE'),
    _district_table('EATableID'),
    html.Br(), html.Br(),
    html.H2('North Coast Percent Booked'),
    html.H4('District/Area: NCCB,NCOC,NCRS,NCVS,NCLC,OCN,OCS'),
    _district_table('NCTableID', sortable=True),
    html.Br(), html.Br(),
    html.H2('North East Percent Booked'),
    html.H4('District/Area: NEJU,NENE,NEPW,NERA,NESE,NESM,NEFB,NEVC'),
    _district_table('NETableID', sortable=True),
    dbc.Row([
        dbc.Col([
            html.Br(),
            html.H2('Appointments'),
            html.H5(id='APPT_MINUTES_ID', style={'whiteSpace': 'pre'}),
            dash_table.DataTable(
                id='MostRecentApptTableID',
                sort_action='native',
                style_header={'whiteSpace': 'normal', 'height': 'auto'},
            ),
            html.Div([
                html.Button('Appointment Download', id='APPT_DOWNLOAD_BTN_ID'),
                dcc.Download(id='APPT_DOWNLOADFILE_ID'),
            ]),
        ]),
        dbc.Col([
            html.Br(),
            html.H2('Shift'),
            html.H5(id='SHIFT_MINUTES_ID', style={'whiteSpace': 'pre'}),
            dash_table.DataTable(
                id='ShiftTableID',
                style_header={'whiteSpace': 'normal', 'height': 'auto'},
            ),
            html.Div([
                html.Button('Shift Download', id='SHIFT_DOWNLOAD_BTN_ID'),
                dcc.Download(id='SHIFT_DOWNLOADFILE_ID'),
            ]),
        ]),
    ]),
])

# =============================================================================
# Callbacks
# =============================================================================

@callback(Output('CWZTimestamp2ID', 'children'),
          Input('CWZIntervalSecCWID', 'n_intervals'))
def update_clock(n):
    return dt.now().strftime('%H:%M:%S')


@callback(
    Output('APPT_DOWNLOADFILE_ID', 'data'),
    Input('APPT_DOWNLOAD_BTN_ID', 'n_clicks'),
    prevent_initial_call=True,
)
def download_appointments(n_clicks):
    df = pd.read_csv('data/ApptMinTable.csv')
    return dcc.send_data_frame(df.to_csv, f"ApptTable_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv")


@callback(
    Output('SHIFT_DOWNLOADFILE_ID', 'data'),
    Input('SHIFT_DOWNLOAD_BTN_ID', 'n_clicks'),
    prevent_initial_call=True,
)
def download_shift(n_clicks):
    df = pd.read_csv('data/ShiftTable.csv')
    return dcc.send_data_frame(df.to_csv, f"ShiftTable_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv")


@callback(
    [Output('BCTableID', 'columns'), Output('BCTableID', 'data'), Output('BCTableID', 'style_data_conditional'),
     Output('CMTableID', 'columns'), Output('CMTableID', 'data'), Output('CMTableID', 'style_data_conditional'),
     Output('EATableID', 'columns'), Output('EATableID', 'data'), Output('EATableID', 'style_data_conditional'),
     Output('NCTableID', 'columns'), Output('NCTableID', 'data'), Output('NCTableID', 'style_data_conditional'),
     Output('NETableID', 'columns'), Output('NETableID', 'data'), Output('NETableID', 'style_data_conditional')],
    [Input('CWZIntervalCWID', 'n_intervals'), Input('CCC_CSFSelectID', 'value')],
)
def display_output(n, view_select):
    try:
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        logging.info(f'CAMM display_output IP: {ip}')
    except Exception:
        logging.info('CAMM display_output: IP lookup failed')

    date_list    = get_date_list()
    data         = make_load_pct()
    appt_sums    = data['ApptMinSums']
    load_pct_df  = data['LoadPct'].rename_axis(['District', 'DATE'])
    shift_state  = data['MS']['ShiftState']
    shift_counts = data['MS']['DistrictShiftStats']['count']

    result = []
    for district in _DISTRICTS:
        num_appts = appt_sums.xs(district, level='District')['count'].unstack(1).fillna(0)
        num_appts['TotalAppts'] = num_appts.sum(axis=1)

        date_df = pd.DataFrame(index=[d[4:] for d in date_list])
        dft = (num_appts.merge(date_df, left_index=True, right_index=True, how='right')
                        .fillna(0).astype(int).astype(str))
        dft = shift_state.xs(district, level=0).merge(dft, left_index=True, right_index=True, how='outer')
        dft = dft.merge(shift_counts.xs(district, level=0), left_index=True, right_index=True, how='outer')

        load_col = load_pct_df.xs(district, level='District')[['LoadPct']].astype(int).astype(str)
        load_col['LoadPct'] = load_col['LoadPct'].apply(
            lambda x: f"{x}% E" if int(x) > THRESH else f"{x}% U"
        )
        dft = load_col.merge(dft, left_index=True, right_index=True, how='left').T
        dft.rename(index=_ROW_RENAME, inplace=True)
        dft = dft.fillna('NO SHIFT')
        dft.columns = [pd.to_datetime(c).strftime('%Y-%m-%d %a') for c in dft.columns]
        dft = dft.reindex(_ROW_ORDER)

        if view_select == 'CallCenterView':
            dft = dft.loc[dft.index == 'Loading Pct']

        dft.reset_index(inplace=True)
        cols    = [{'name': c, 'id': c, 'type': 'numeric'} for c in dft.columns]
        records = dft.to_dict('records')
        result.extend([cols, records, get_style(district)])

    return result


@callback(
    [Output('MostRecentApptTableID', 'columns'),
     Output('MostRecentApptTableID', 'data'),
     Output('ShiftTableID', 'columns'),
     Output('ShiftTableID', 'data'),
     Output('SHIFT_MINUTES_ID', 'children'),
     Output('APPT_MINUTES_ID', 'children'),
     Output('BCTableID', 'active_cell'),
     Output('CMTableID', 'active_cell'),
     Output('EATableID', 'active_cell'),
     Output('NCTableID', 'active_cell'),
     Output('NETableID', 'active_cell')],
    [Input('BCTableID',  'active_cell'),
     State('BCTableID',  'columns'), State('BCTableID',  'data'),
     State('BCTableID',  'derived_virtual_data'), State('BCTableID',  'derived_virtual_selected_rows'),
     Input('CMTableID',  'active_cell'),
     State('CMTableID',  'columns'), State('CMTableID',  'data'),
     State('CMTableID',  'derived_virtual_data'), State('CMTableID',  'derived_virtual_selected_rows'),
     Input('EATableID',  'active_cell'),
     State('EATableID',  'columns'), State('EATableID',  'data'),
     State('EATableID',  'derived_virtual_data'), State('EATableID',  'derived_virtual_selected_rows'),
     Input('NCTableID',  'active_cell'),
     State('NCTableID',  'columns'), State('NCTableID',  'data'),
     State('NCTableID',  'derived_virtual_data'), State('NCTableID',  'derived_virtual_selected_rows'),
     Input('NETableID',  'active_cell'),
     State('NETableID',  'columns'), State('NETableID',  'data'),
     State('NETableID',  'derived_virtual_data'), State('NETableID',  'derived_virtual_selected_rows')],
)
def update_table(
    ac_bc, col_bc, data_bc, dvd_bc, dva_bc,
    ac_cm, col_cm, data_cm, dvd_cm, dva_cm,
    ac_ea, col_ea, data_ea, dvd_ea, dva_ea,
    ac_nc, col_nc, data_nc, dvd_nc, dva_nc,
    ac_ne, col_ne, data_ne, dvd_ne, dva_ne,
):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    logging.info(f'CAMM update_table IP: {ip}')

    district_inputs = [
        ('BC', ac_bc, col_bc, data_bc, dvd_bc, dva_bc),
        ('CM', ac_cm, col_cm, data_cm, dvd_cm, dva_cm),
        ('EA', ac_ea, col_ea, data_ea, dvd_ea, dva_ea),
        ('NC', ac_nc, col_nc, data_nc, dvd_nc, dva_nc),
        ('NE', ac_ne, col_ne, data_ne, dvd_ne, dva_ne),
    ]
    for district, active_cell, cols, tdata, dvd, dva in district_inputs:
        if active_cell is not None:
            slot = _get_appt_slot_data(active_cell, cols, tdata, dvd, dva, district)
            return (*slot, None, None, None, None, None)

    return (None,) * 11


def _get_appt_slot_data(active_cell, columns, tdata, dvd, dva, district):
    cell_date = active_cell['column_id'][:10]
    appt_type = _CELL_ROW_LUT[active_cell['row']]
    logging.info(f'CAMM slot: {district}  date={cell_date}  type={appt_type}')

    # --- Appointment detail table ---
    orders = _classify_appt_type(_assign_district(get_order_data()))
    orders.rename(columns={'MinReqSkill': 'Skill', 'JOB_TYPE': 'Job',
                            'StdNumMin': 'TotMin', 'WO_AGGREGATION': 'ADDRESS'}, inplace=True)
    orders['YearMonthDay'] = orders.ELIGIBLE.dt.strftime('%Y-%m-%d')

    try:
        fltr = ((orders.YearMonthDay == cell_date) &
                (orders.APPT_BOOKING_FLAG == 'Y') &
                (orders.ApptType == appt_type) &
                (orders.District == district))
    except Exception as e:
        logging.info(f'CAMM slot filter error: {e}')
        return None, None, None, None, None, None

    order_sel = orders.loc[fltr, ['ORDER_NUM', 'JOBCODE', 'Job', 'Skill', 'JOB_CODE_DESCRIPTION',
                                   'AREA', 'ADDRESS', 'TotMin', 'REASON', 'SVCGNTY', 'BOOKING_FLAG']]
    order_sel.to_csv('data/Appointments.csv', index=False)
    order_cols    = [{'name': c, 'id': c} for c in order_sel.columns]
    order_records = order_sel[:500].to_dict('records')

    # --- Summary stats ---
    data = make_load_pct()
    appt_sums = data['ApptMinSums']
    try:
        appt_counts  = int(appt_sums.xs((district, cell_date, appt_type))['count'])
        appt_minutes = int(appt_sums.xs((district, cell_date, appt_type))['sum'])
    except Exception as e:
        appt_counts = appt_minutes = 0
        logging.info(f'CAMM slot ApptMins lookup failed: {e}')
    appt_str = (f'Tot Mins: {appt_minutes}  Appt Count: {appt_counts}  '
                f'District: {district}  Date: {cell_date}  Appt Type: {appt_type}')

    try:
        shift_minutes = int(data['MS']['DistrictShiftStats'].xs((district, cell_date))['sum'])
        shift_count   = int(data['MS']['DistrictShiftStats'].xs((district, cell_date))['count'])
    except Exception as e:
        shift_minutes = shift_count = 0
        logging.info(f'CAMM slot ShiftTable lookup failed: {e}')
    shift_str = f'District: {district}  Shift Minutes: {shift_minutes}  Shift Count: {shift_count}'

    # --- Shift detail table ---
    shift = get_shift_data()
    shift['YearMonthDay'] = shift['SHIFT_DATE'].str[:10]
    shift_fltr  = (shift.YearMonthDay == cell_date) & (shift.District == district)
    shift_sel   = shift.loc[shift_fltr, ['District', 'SHIFT_DATE', 'TECH_NAME', 'SKILL',
                                          'SHIFT', 'SHIFT_NAME', 'PERIOD_NAME', 'SHIFT_MIN']]
    working_sel = shift_sel[shift_sel.PERIOD_NAME.str.contains('Closed|Working Loading')]
    shift_sel.loc[shift_sel.PERIOD_NAME.isin(['Busy', 'Safety Meeting']), 'SHIFT_NAME'] = 'Exception'
    shift_sel   = shift_sel[shift_sel.TECH_NAME.isin(working_sel.TECH_NAME)].sort_values('TECH_NAME')
    shift_sel.to_csv('data/ShiftTable.csv', index=False)
    shift_cols    = [{'name': c, 'id': c} for c in shift_sel.columns]
    shift_records = shift_sel[:500].to_dict('records')

    return order_cols, order_records, shift_cols, shift_records, shift_str, appt_str
