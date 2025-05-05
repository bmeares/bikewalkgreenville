#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Implement the plugin 'who-owns-what-roads'.

See the Writing Plugins guide for more information:
https://meerschaum.io/reference/plugins/writing-plugins/
"""

from datetime import datetime, timedelta, timezone
import meerschaum as mrsm
from meerschaum.config import get_plugin_config, write_plugin_config
from meerschaum.plugins import web_page, dash_plugin

__version__ = '0.0.1'

required: list[str] = ['dash-deck', 'pydeck']


def setup(**kwargs) -> mrsm.SuccessTuple:
    """Executed during installation and `mrsm setup plugin who-owns-what-roads`."""
    return True, "Success"


@dash_plugin
def init_dash(dash_app):
    """Initialize the Plotly Dash application."""

    import dash.html as html
    import dash.dcc as dcc
    import plotly.express as px
    import plotly.graph_objects as go
    from dash import Input, Output, State, no_update
    from dash.exceptions import PreventUpdate
    dash_deck = mrsm.attempt_import('dash_deck', venv='who-owns-what-roads', lazy=False)
    pdk = mrsm.attempt_import('pydeck', venv='who-owns-what-roads', lazy=False)
    import dash_bootstrap_components as dbc

    from meerschaum.utils.dtypes import serialize_geometry

    # Create a new page at the path `/dash/who-owns-what-roads`.
    @web_page('who-owns-what-roads', login_required=False, skip_navbar=True, page_group="Bike Walk Greenville")
    def page_layout():
        """Return the layout objects for this page."""
        return dbc.Container([
            dcc.Location(id='who-owns-what-roads-location'),
            html.Div(id='output-div'),
        ])

    @dash_app.callback(
        Output('output-div', 'children'),
        Input('who-owns-what-roads-location', 'pathname'),
    )
    def render_page_on_url_change(pathname: str):
        """Reload page contents when the URL path changes."""
        title = html.H2("Road Ownership Lookup")
        form = html.Div([
            dbc.Input(placeholder="Address", id="wowr-address-input"),
        ])
        submit_button = dbc.Button("Search", id="wowr-submit-button")
        pipe = mrsm.Pipe('sql:bwg', 'boundaries', 'greenville', instance='sql:bwg')
        df = pipe.get_data().set_crs(6570).to_crs(epsg=4326)
        geometry_data = serialize_geometry(df['geometry'][0], 'geojson')
        print(geometry_data)
        raise ValueError()

        deckgl_json = pdk.Deck(
            map_style="mapbox://styles/mapbox/light-v9",
            initial_view_state=pdk.ViewState(
                latitude=df.geometry.centroid.y.mean(),
                longitude=df.geometry.centroid.x.mean(),
                zoom=10,
                pitch=0,
            ),
            layers=[
                pdk.Layer(
                    "GeoJsonLayer",
                    data=geometry_data,
                    get_fill_color="[255, 0, 0, 100]",
                    get_line_color=[0, 0, 0],
                    pickable=True,
                )
            ],
        )

        deck = dash_deck.DeckGL(
            id='wowr-deck-gl',
            data=deckgl_json,
        )

        return [
            title,
            html.Br(),
            dbc.Row([
                dbc.Col(form),
            ]),
            html.Br(),
            dbc.Row([
                dbc.Col([submit_button]),
            ]),
            html.Div(id="wowr-output-div"),
            deck,
            #  dcc.Graph(figure=fig),
        ]

    @dash_app.callback(
        Output('wowr-output-div', 'children'),
        Input('wowr-submit-button', 'n_clicks'),
        State('wowr-address-input', 'value'),
    )
    def submit_click(n_clicks, address):
        if not n_clicks:
            raise PreventUpdate
        return html.P("Hello, World!")
