import dash  # pip install dash
from dash import dcc,html,callback,Output,Input
#import dash_labs as dl  # pip install dash-labs
import dash_bootstrap_components as dbc # pip install dash-bootstrap-components
import logging
import os 
# Code from: https://github.com/plotly/dash-labs/tree/main/docs/demos/multi_page_example1

app = dash.Dash(
    __name__,external_stylesheets = [dbc.themes.BOOTSTRAP],use_pages = True
)

logging.basicConfig(level = logging.INFO,format = ' %(asctime)s - %(levelname)a - %(message)s')
logging.info(__name__ + ' Starting app server')

server = app.server
navbar = dbc.NavbarSimple(
    dbc.DropdownMenu(
        [
            dbc.DropdownMenuItem(page["name"], href=page["path"])
            for page in dash.page_registry.values()
            if page["module"] != "pages.not_found_404"
        ],
        nav=True,
        label="Applications",
    ),
    brand="Inventory and Failure Analytics" + os.getcwd(),
    color="white",
    dark=False,
    className="mb-2",
)
app.layout = dbc.Container(
    [navbar,dash.page_container],
    fluid=True,
)

if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

if __name__ == "__main__":
    app.run(debug=False, port = 8007,host = '0.0.0.0')
#    app.run_server(debug=False, port = 80,host = '0.0.0.0')

