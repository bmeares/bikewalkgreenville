#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Vulnerable Road Users incidents map.

Replicates the Grafana "Vulnerable Road Users" dashboard map panel using
Dash Leaflet: clustered fatalities + injuries markers over the Greenville
County boundary, with a year-range slider.
"""

from datetime import datetime, timezone
import meerschaum as mrsm
from meerschaum.plugins import web_page, dash_plugin

__version__ = '0.0.1'

required: list[str] = ['dash-leaflet']

LAYER_STYLES = {
    'fatalities': {
        'label': 'Fatalities',
        'color': '#C8102E',
        'pipe_keys': ('sql:bwg', 'fatalities', 'vulnerable'),
        'schema': 'Ped',
        'target': 'fatalities_vulnerable',
    },
    'injuries': {
        'label': 'Injuries',
        'color': '#E67E22',
        'pipe_keys': ('sql:bwg', 'injuries', 'vulnerable'),
        'schema': 'Ped',
        'target': 'injuries_vulnerable',
    },
}

LOG_PIPE: mrsm.Pipe = mrsm.Pipe(
    'dash', 'usage', 'VRU',
    instance='sql:bwg',
    parameters={
        'autotime': True,
        'schema': 'Ped',
        'target': 'dash_usage',
        'columns': {
            'datetime': 'ts',
            'ip': 'ip',
        },
        'dtypes': {
            'ts': 'datetime',
            'ip': 'string',
            'user_agent': 'string',
            'year_min': 'int',
            'year_max': 'int',
            'layers': 'string',
            'is_embed': 'bool',
        },
        'verify': {
            'chunk_minutes': (1440 * 365.25),
        },
    },
)


@dash_plugin
def init_dash(dash_app):
    """Initialize the Plotly Dash application."""
    from urllib.parse import parse_qs, urlencode

    import dash.html as html
    import dash.dcc as dcc
    from dash import Input, Output, State, clientside_callback
    from dash.exceptions import PreventUpdate
    import dash_bootstrap_components as dbc
    from flask import request

    dl = mrsm.attempt_import('dash_leaflet', venv='vru', lazy=False)

    crashes_pipe = mrsm.Pipe('sql:bwg', 'crashes', 'vulnerable', instance='sql:bwg')
    layer_pipes = {
        key: mrsm.Pipe(*meta['pipe_keys'], instance='sql:bwg')
        for key, meta in LAYER_STYLES.items()
    }

    _bounds_cache: dict[str, tuple[int, int]] = {}
    _layer_cache: dict[tuple[str, int, int], dict] = {}

    MAX_POINTS_PER_LAYER = 5000

    @web_page(
        'vru',
        login_required=False,
        skip_navbar=True,
        page_group="Bike Walk Greenville",
    )
    def page_layout():
        """Return the layout objects for this page."""
        return [
            dcc.Location(id='vru-location'),
            html.Div(id='vru-output-div'),
        ]

    def get_url_params(search: str) -> dict[str, str]:
        return {
            param: val[0]
            for param, val in parse_qs((search or '').lstrip('?')).items()
            if val
        }

    def get_embed_status(search: str) -> bool:
        return get_url_params(search).get('embed') in ('true', 'True', '1')

    def get_year_bounds() -> tuple[int, int]:
        if 'bounds' in _bounds_cache:
            return _bounds_cache['bounds']
        current_year = datetime.now().year
        try:
            oldest = crashes_pipe.get_sync_time(newest=False)
            newest = crashes_pipe.get_sync_time(newest=True)
        except Exception:
            oldest = newest = None
        bounds = (
            (oldest.year, newest.year)
            if oldest is not None and newest is not None
            else (current_year - 10, current_year)
        )
        _bounds_cache['bounds'] = bounds
        return bounds

    @dash_app.callback(
        Output('vru-output-div', 'children'),
        Input('vru-location', 'pathname'),
        State('vru-location', 'search'),
        State('vru-location', 'href'),
    )
    def render_page_on_url_change(pathname: str, search: str, href: str):
        url_params = get_url_params(search)
        is_embed = get_embed_status(search)
        non_embed_params = {k: v for k, v in url_params.items() if k != 'embed'}

        year_min, year_max = get_year_bounds()
        default_start = max(year_min, year_max - 4)
        try:
            start_year = max(year_min, int(url_params.get('year_min', default_start)))
        except (TypeError, ValueError):
            start_year = default_start
        try:
            end_year = min(year_max, int(url_params.get('year_max', year_max)))
        except (TypeError, ValueError):
            end_year = year_max

        active_layers_param = url_params.get('layers', 'fatalities,injuries')
        active_layers = [
            layer
            for layer in active_layers_param.split(',')
            if layer in LAYER_STYLES
        ] or list(LAYER_STYLES.keys())

        fullscreen_button = dbc.Button(
            '⛶',
            color='secondary',
            outline=True,
            style={'color': '#FFFFFF'},
            href=(
                (href or '').split('?', maxsplit=1)[0]
                + (('?' + urlencode(non_embed_params)) if non_embed_params else '')
            ),
            target='_blank',
        )

        slider_marks: dict[int, dict] = {}
        if year_max > year_min:
            step = max(1, (year_max - year_min) // 6)
            for year in range(year_min, year_max + 1, step):
                slider_marks[year] = {'label': str(year), 'style': {'color': '#CCCCCC'}}
            slider_marks[year_max] = {'label': str(year_max), 'style': {'color': '#CCCCCC'}}

        controls_overlay = html.Div(
            dbc.Card(
                dbc.CardBody(
                    [
                        dbc.Row(
                            [
                                dbc.Col(
                                    dbc.Checklist(
                                        id='vru-layer-checklist',
                                        options=[
                                            {
                                                'label': html.Span(
                                                    meta['label'],
                                                    style={
                                                        'color': meta['color'],
                                                        'font-weight': 'bold',
                                                        'margin-left': '6px',
                                                    },
                                                ),
                                                'value': key,
                                            }
                                            for key, meta in LAYER_STYLES.items()
                                        ],
                                        value=active_layers,
                                        inline=True,
                                        switch=True,
                                    ),
                                    md=4,
                                    xs=12,
                                ),
                                dbc.Col(
                                    html.Div([
                                        html.Div(
                                            id='vru-year-label',
                                            style={
                                                'color': '#EEEEEE',
                                                'font-size': 'small',
                                                'margin-bottom': '4px',
                                            },
                                        ),
                                        dcc.RangeSlider(
                                            id='vru-year-slider',
                                            min=year_min,
                                            max=year_max,
                                            step=1,
                                            value=[start_year, end_year],
                                            marks=slider_marks,
                                            allowCross=False,
                                            tooltip={
                                                'always_visible': False,
                                                'placement': 'top',
                                            },
                                        ),
                                    ]),
                                    md=7,
                                    xs=12,
                                ),
                                dbc.Col(
                                    fullscreen_button,
                                    md=1,
                                    xs=12,
                                    className='d-flex justify-content-end align-items-center',
                                ) if is_embed else None,
                            ],
                            align='center',
                        ),
                    ],
                    style={'padding': '10px 14px'},
                ),
                style={
                    'background-color': 'rgba(20,20,20,0.85)',
                    'backdrop-filter': 'blur(4px)',
                    'border': '1px solid rgba(255,255,255,0.1)',
                },
            ),
            style={
                'position': 'absolute',
                'top': '10px',
                'left': '10px',
                'right': '60px',
                'zIndex': 1000,
                'pointerEvents': 'auto',
            },
        )

        map_layout = build_initial_map_layout(iframe_scroll=is_embed)

        return html.Div(
            [
                map_layout,
                controls_overlay,
                html.Div(id='vru-dummy-for-clientside', style={'display': 'none'}),
            ],
            style={
                'position': 'fixed',
                'top': 0,
                'left': 0,
                'right': 0,
                'bottom': 0,
                'width': '100vw',
                'height': '100vh',
                'margin': 0,
                'padding': 0,
                'overflow': 'hidden',
            },
        )

    clientside_callback(
        """
        function(pathname) {
            const params = new URLSearchParams(window.location.search);
            if (params.get('embed') === 'true') {
                document.body.style.overflowX = 'hidden';
            }
            return '';
        }
        """,
        Output('vru-dummy-for-clientside', 'children'),
        Input('vru-location', 'pathname'),
    )

    @dash_app.callback(
        Output('vru-year-label', 'children'),
        Input('vru-year-slider', 'value'),
    )
    def update_year_label(year_range):
        if not year_range:
            raise PreventUpdate
        return f"Years: {year_range[0]} – {year_range[1]}"

    @dash_app.callback(
        Output('vru-fatalities-layer', 'children'),
        Output('vru-injuries-layer', 'children'),
        Input('vru-year-slider', 'value'),
        Input('vru-layer-checklist', 'value'),
        State('vru-location', 'search'),
        prevent_initial_call=False,
    )
    def refresh_layers(year_range, active_layers, search):
        if not year_range:
            raise PreventUpdate

        year_min, year_max = int(year_range[0]), int(year_range[1])
        active_layers = active_layers or []
        is_embed = get_embed_status(search)

        #  try:
            #  LOG_PIPE.sync(
                #  [{
                    #  'ip': request.remote_addr,
                    #  'user_agent': str(request.user_agent),
                    #  'year_min': year_min,
                    #  'year_max': year_max,
                    #  'layers': ','.join(active_layers),
                    #  'is_embed': is_embed,
                #  }],
                #  blocking=False,
            #  )
        #  except Exception:
            #  pass

        fatalities_markers = (
            fetch_layer_markers('fatalities', year_min, year_max)
            if 'fatalities' in active_layers
            else []
        )
        injuries_markers = (
            fetch_layer_markers('injuries', year_min, year_max)
            if 'injuries' in active_layers
            else []
        )
        return fatalities_markers, injuries_markers

    def fetch_layer_markers(layer_key: str, year_min: int, year_max: int) -> list:
        cache_key = (layer_key, year_min, year_max)
        if cache_key in _layer_cache:
            return _layer_cache[cache_key]

        pipe = layer_pipes[layer_key]
        meta = LAYER_STYLES[layer_key]
        schema = meta['schema']
        target = meta['target']
        color = meta['color']
        sql = (
            "SELECT "
            "  ST_Y(\"geometry\") AS lat, "
            "  ST_X(\"geometry\") AS lng, "
            "  to_char(\"timestamp\", 'YYYY-MM-DD') AS ts, "
            "  persons_killed, "
            "  persons_injured, "
            "  collision_type, "
            "  primary_contributing_factor "
            f"FROM \"{schema}\".\"{target}\" "
            "WHERE \"geometry\" IS NOT NULL "
            f"  AND \"timestamp\" >= '{year_min}-01-01' "
            f"  AND \"timestamp\" <  '{year_max + 1}-01-01' "
            f"LIMIT {MAX_POINTS_PER_LAYER}"
        )
        rows = pipe.instance_connector.exec(sql).fetchall() or []

        markers = [
            dl.CircleMarker(
                center=[float(row[0]), float(row[1])],
                radius=6,
                color=color,
                weight=1,
                fillColor=color,
                fillOpacity=0.7,
                children=[
                    dl.Tooltip(build_tooltip(row), sticky=True),
                ],
            )
            for row in rows
            if row[0] is not None and row[1] is not None
        ]
        _layer_cache[cache_key] = markers
        return markers

    def build_tooltip(row) -> list:
        ts, killed, injured, collision_type, factor = row[2], row[3], row[4], row[5], row[6]
        lines = []
        if ts:
            lines.append(html.B(ts))
        if killed and killed > 0:
            lines.append(html.Br())
            lines.append(f"Killed: {killed}")
        if injured and injured > 0:
            lines.append(html.Br())
            lines.append(f"Injured: {injured}")
        if collision_type:
            lines.append(html.Br())
            lines.append(f"Type: {collision_type}")
        if factor:
            lines.append(html.Br())
            lines.append(f"Cause: {factor}")
        return lines

    def build_initial_map_layout(iframe_scroll: bool = False):
        fatalities_layer = dl.LayerGroup(id='vru-fatalities-layer', children=[])
        injuries_layer = dl.LayerGroup(id='vru-injuries-layer', children=[])

        return dl.Map(
            id='vru-map',
            center=[34.815751, -82.388822],
            zoom=10,
            style={'height': '100vh', 'width': '100vw'},
            children=(
                [
                    dl.TileLayer(
                        url='https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
                        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
                    ),
                ]
                + ([dl.GestureHandling()] if iframe_scroll else [])
                + [
                    dl.GeoJSON(
                        url='https://meerschaum.io/files/bwg/output/geojson/county/BND_GVCNTY.geojson',
                        id='vru-county-boundary',
                        style={
                            'color': '#A3CF1F',
                            'weight': 2,
                            'opacity': 0.5,
                            'fill': False,
                        },
                    ),
                    injuries_layer,
                    fatalities_layer,
                    dl.FullScreenControl(),
                    dl.LocateControl(
                        locateOptions={'enableHighAccuracy': True},
                        drawCircle=False,
                        flyTo=True,
                        showPopup=False,
                        showCompass=True,
                    ),
                ]
            ),
        )
