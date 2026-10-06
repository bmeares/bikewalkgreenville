#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Explore existing geometry pipes on an interactive map.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Union, List, Dict
import meerschaum as mrsm
from meerschaum.config import get_plugin_config, write_plugin_config
from meerschaum.plugins import web_page, dash_plugin

__version__ = '0.0.1'

required: list[str] = ['dash-leaflet']


@dash_plugin
def init_dash(dash_app):
    """Initialize the Plotly Dash application."""

    import dash.html as html
    import dash.dcc as dcc
    from dash import Input, Output, State, no_update
    from dash.exceptions import PreventUpdate
    import dash_bootstrap_components as dbc

    dl = mrsm.attempt_import('dash_leaflet', venv='explore-map', lazy=False)

    @web_page(
        'explore-map',
        page_group='Bike Walk Greenville',
        login_required=False,
        skip_navbar=True,
    )
    def page_layout():
        """Return the layout objects for this page."""
        return [
            dcc.Location(id='explore-map-location'),
            html.Div(id='explore-map-output-div'),
        ]

    @dash_app.callback(
        Output('explore-map-output-div', 'children'),
        Input('explore-map-location', 'pathname'),
    )
    def render_page_on_url_change(pathname: str):
        """Reload page contents when the URL path changes."""
        return dl.Map(
            children=[
                html.Div([dcc.Dropdown(id='explore-map-dropdown')], id='explore-map-overlay-div'),
                dl.FeatureGroup(id='explore-map-output-feature-group'),
                dl.TileLayer(id='explore-map-tile-layer'),
                dl.EasyButton("Hello", icon='fa-globe', id='explore-map-easy-button'),

                dl.FullScreenControl(),
                dl.LocateControl(
                    locateOptions={"enableHighAccuracy": True},
                    drawCircle=False,
                    flyTo=True,
                    showPopup=False,
                    showCompass=True,
                )

            ],
            zoom=10,
            style={'height': '100vh', 'width': '100vw'},
            center=[34.843739, -82.393905],
            id='explore-map',
        )

    @dash_app.callback(
        Output('explore-map-output-feature-group', 'children'),
        Input('explore-map-easy-button', 'n_clicks'),
        prevent_initial_call=True,
    )
    def easy_button_click(n_clicks):
        if not n_clicks:
            raise PreventUpdate

        return [
            dl.GeoJSON(
                id='explore-map-geojson'
            )
        ]
