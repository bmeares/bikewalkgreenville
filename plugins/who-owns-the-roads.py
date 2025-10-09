#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Add the "Who Owns the Roads" search application.
"""

from datetime import datetime, timedelta, timezone
import meerschaum as mrsm
from meerschaum.config import get_plugin_config, write_plugin_config
from meerschaum.plugins import web_page, dash_plugin

__version__ = '0.1.0'

required: list[str] = ['dash-leaflet']


@dash_plugin
def init_dash(dash_app):
    """Initialize the Plotly Dash application."""

    import dash.html as html
    import dash.dcc as dcc
    import plotly.express as px
    import plotly.graph_objects as go
    from dash import Input, Output, State, no_update
    from dash.exceptions import PreventUpdate
    import dash_bootstrap_components as dbc

    dl = mrsm.attempt_import('dash_leaflet', venv='who-owns-the-roads', lazy=False)
    gpd = mrsm.attempt_import('geopandas', lazy=False)

    from meerschaum.utils.dtypes import serialize_geometry

    roads_pipe = mrsm.Pipe('sql:bwg', 'roads', 'Roads', instance='sql:bwg')

    @web_page(
        'who-owns-the-roads',
        login_required=False,
        skip_navbar=True,
        page_group="Bike Walk Greenville",
    )
    def page_layout():
        """Return the layout objects for this page."""
        return dbc.Container([
            dcc.Location(id='who-owns-the-roads-location'),
            html.Div(id='output-div'),
        ])

    @dash_app.callback(
        Output('output-div', 'children'),
        Input('who-owns-the-roads-location', 'pathname'),
    )
    def render_page_on_url_change(pathname: str):
        """Reload page contents when the URL path changes."""
        form = html.Div([
            dbc.Input(placeholder="Road Name", id="wotr-road-name-input"),
        ])
        submit_button = dbc.Button("Search", id="wotr-submit-button")
        return [
            html.Br(),
            dbc.Row([
                dbc.Col(form),
            ]),
            html.Br(),
            dbc.Row([
                dbc.Col([submit_button]),
            ]),
            html.Br(),
            html.Div(id="wotr-output-div"),
        ]

    @dash_app.callback(
        Output('wotr-output-div', 'children'),
        Input('wotr-submit-button', 'n_clicks'),
        State('wotr-road-name-input', 'value'),
    )
    def submit_click(n_clicks, road_name):
        if not n_clicks or not road_name:
            raise PreventUpdate

        road_name_clean = road_name.strip("'").strip(";").strip('-').lower()
        query = (
            "SELECT\n"
            "  \"Name\",\n"
            "  \"Type\",\n"
            "  \"Owner\",\n"
            "  \"Contact\",\n"
            "  \"Phone\",\n"
            "  \"Email\",\n"
            "  \"Online Form\",\n"
            "  \"geometry\"\n"
            "FROM \"Roads\".roads\n"
            "WHERE\n"
            "  LOWER(\"Name\") LIKE '%%" + road_name_clean + "%%'\n"
            "LIMIT 20"
        )
        df = gpd.read_postgis(query, roads_pipe.instance_connector.engine, geom_col='geometry')
        non_geo_cols = [col for col in df.columns if col != 'geometry']
        geojson_data = df.to_json(to_wgs84=True)
        print(df)
        print(geojson_data)

        return [
            dbc.Table.from_dataframe(df[non_geo_cols], striped=True, bordered=True, hover=True),
            dl.Map(
                children=[
                    dl.TileLayer(),
                    dl.GeoJSON(data=geojson_data),
                ],
                zoom=10,
                style={'height': '50vh'},
                center=[34.843739, -82.393905],
            )
        ]
