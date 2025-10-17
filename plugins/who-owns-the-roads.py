#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Add the "Who Owns the Roads" search application.
"""

from datetime import datetime, timedelta, timezone
import meerschaum as mrsm
from meerschaum.config import get_plugin_config, write_plugin_config
from meerschaum.plugins import web_page, dash_plugin

__version__ = '0.2.0'

required: list[str] = ['dash-leaflet']

TYPES_COLORS = {
    'US Highway': {
        'hex': '#C7962E',
        'button': {
            'color': 'warning',
            'outline': True,
        },
    },
    'State Road': {
        'hex': '#C7962E',
        'button': {
            'color': 'warning',
            'outline': True,
        }
    },
    'County Road': {
        'hex': '#999999',
        'button': {
            'color': 'light',
            'outline': True,
        },
    },
    'Municipal Road': {
        'hex': '#6F9920',
        'button': {
            'color': 'success',
            'outline': True,
        },
    },
    'Private Drive': {
        'hex': '#577DAB',
        'button': {
            'color': 'info',
            'outline': True,
        },
    },
    'Subdivision': {
        'hex': '#577DAB',
        'button': {
            'color': 'info',
            'outline': True,
        },
    },
}

ALIASES: dict[str, str] = {
    'street': 'st',
    'road': 'rd',
    'avenue': 'ave',
    'court': 'ct',
    'east': 'e',
    'west': 'w',
    'north': 'n',
    'south': 's',
    'northeast': 'ne',
    'southeast': 'se',
}


@dash_plugin
def init_dash(dash_app):
    """Initialize the Plotly Dash application."""

    import json
    import re
    from urllib.parse import parse_qs

    import dash.html as html
    import dash.dcc as dcc
    import plotly.express as px
    import plotly.graph_objects as go
    from dash import Input, Output, State, no_update, MATCH, ALL, callback_context
    from dash.exceptions import PreventUpdate
    import dash_bootstrap_components as dbc

    dl, dlx = mrsm.attempt_import('dash_leaflet', 'dash_leaflet.express', venv='who-owns-the-roads', lazy=False)
    gpd = mrsm.attempt_import('geopandas', lazy=False)

    from meerschaum.utils.dtypes import serialize_geometry
    from meerschaum.utils.dataframe import query_df
    from meerschaum.api.dash.components import build_cards_grid

    roads_pipe = mrsm.Pipe('sql:bwg', 'roads', 'Roads', instance='sql:bwg')
    link_prefixes = {
        'Email': 'mailto:',
        'Phone': 'tel:',
        'Online Form': '',
    }

    @web_page(
        'who-owns-the-roads',
        login_required=False,
        skip_navbar=True,
        page_group="Bike Walk Greenville",
    )
    def page_layout():
        """Return the layout objects for this page."""
        return [
            dcc.Location(id='who-owns-the-roads-location'),
            html.Div(id='who-owns-the-roads-output-div'),
        ]
        #  return dbc.Container([
            #  dcc.Location(id='who-owns-the-roads-location'),
            #  html.Div(id='who-owns-the-roads-output-div'),
        #  ])

    @dash_app.callback(
        Output('who-owns-the-roads-output-div', 'children'),
        Input('who-owns-the-roads-location', 'pathname'),
        State('who-owns-the-roads-location', 'search'),
    )
    def render_page_on_url_change(pathname: str, search: str):
        """Reload page contents when the URL path changes."""
        url_params = {
            param: val[0]
            for param, val in parse_qs(search.lstrip('?')).items()
            if val
        }
        initial_search_value = url_params.get('search', '')
        submit_button = dbc.Button(
            "Search",
            id="wotr-submit-button",
            n_clicks=0,
        )
        form = html.Div([
            dbc.InputGroup(
                [
                    dbc.Input(
                        value=initial_search_value,
                        placeholder="Road Name",
                        id="wotr-road-name-input",
                        type='text',
                        debounce=30,
                    ),
                    submit_button,
                ],
                size='lg',
            ),
            html.Div(id='wotr-search-suggestions'),
        ])
        is_embed = url_params.get('embed', None) in ('true', 'True', '1')
        initial_map_layout = build_initial_map_layout(iframe_scroll=is_embed)
        page_children = [
            dcc.Store(id='wotr-click-store'),
            html.Br(),
            dbc.Row([
                dbc.Col(form),
            ]),
            html.Br(),
            html.Div(
                initial_map_layout,
                style={'visibility': 'hidden'},
                id='wotr-map-div',
            ),
            html.Div(id="wotr-results-table"),
        ]

        return (
            dbc.Container(page_children)
            if not is_embed
            else html.Div(
                page_children,
                style={
                    'padding-left': '10px',
                    'padding-right': '10px',
                    'width': '100vw',
                    'overflow-x': 'hidden',
                },
            )
        )

    @dash_app.callback(
        Output('wotr-submit-button', 'n_clicks'),
        Output('wotr-road-name-input', 'value'),
        Input({'type': 'wotr-search-suggest-button', 'ix': ALL}, 'n_clicks'),
        State('wotr-submit-button', 'n_clicks'),
        prevent_initial_call=True,
    )
    def suggest_click(search_suggest_n_clicks, search_n_clicks):
        if not any(search_suggest_n_clicks):
            raise PreventUpdate

        try:
            click_ix_val = callback_context.triggered_id['ix']
            road_dict = json.loads(click_ix_val)
        except Exception:
            raise PreventUpdate

        road_name = road_dict['Name']
        return ((search_n_clicks or 0) + 1), road_name

    @dash_app.callback(
        Output('wotr-lines-group', 'children'),
        Input({'type': 'wotr-card-name-button', 'ix': ALL}, 'n_clicks'),
        prevent_initial_call=False,
    )
    def card_name_click(card_names_n_clicks):
        if not any(card_names_n_clicks):
            raise PreventUpdate

        try:
            click_ix_val = callback_context.triggered_id['ix']
            road_dict = json.loads(click_ix_val)
        except Exception:
            raise PreventUpdate

        road_name = road_dict['Name']
        owner = road_dict.get('Owner', None)
        road_type = road_dict.get('Type', None)
        road_name_clean = get_road_name_clean(road_name)
        query = (
            "SELECT\n"
            "  \"Name\",\n"
            "  \"Type\",\n"
            "  \"Owner\",\n"
            "  \"Phone\",\n"
            "  \"Email\",\n"
            "  \"Online Form\",\n"
            "  ST_Union(\"geometry\") AS \"geometry\"\n"
            "FROM \"Roads\".roads\n"
            "WHERE\n"
            "  LOWER(REGEXP_REPLACE(\"Name\", '[^a-zA-Z0-9 ]', '', 'g')) = '" + road_name_clean + "'\n" + (
                ("  AND \"Owner\" = '" + owner + "'\n")
                if owner
                else ""
            ) + (
                ("  AND \"Type\" = '" + road_type + "'\n")
                if road_type
                else ""
            ) + "GROUP BY\n"
            "  \"Name\",\n"
            "  \"Type\",\n"
            "  \"Owner\",\n"
            "  \"Contact\",\n"
            "  \"Phone\",\n"
            "  \"Email\",\n"
            "  \"Online Form\"\n"
            "ORDER BY \"Name\" ASC\n"
            "LIMIT 100"
        )
        df = gpd.read_postgis(query, roads_pipe.instance_connector.engine, geom_col='geometry')
        road_types_geojson = get_road_types_geojson(df)
        return list(road_types_geojson.values())

    def get_road_name_clean(input_value: str) -> str:
        """
        Return the cleaned, normalized road name from the input string.
        """
        road_name_clean = re.sub(r'[^a-zA-Z0-9]', ' ', input_value)
        road_name_clean = re.sub(r'\s+', ' ', road_name_clean).lower()
        name_parts = road_name_clean.split(' ')
        return ' '.join([
            ALIASES.get(part, part)
            for part in name_parts
        ])

    def get_road_types_geojson(df, layer_type: str = 'wotr-lines'):
        """
        Get the road types to GeoJSON dictionary from a query result dataframe.
        """
        road_types = df['Type'].unique()
        road_types_geojson_data = {}
        for road_type in road_types:
            try:
                road_types_geojson_data[road_type] = json.loads(
                    query_df(df, {'Type': road_type}).to_json(to_wgs84=True, drop_id=True)
                )
            except ValueError:
                pass

        return {
            road_type: dl.GeoJSON(
                data=geojson_data,
                id={'type': layer_type, 'ix': road_type},
                zoomToBounds=True,
                interactive=True,
                hoverStyle={'color': TYPES_COLORS[road_type]['hex'], 'weight': 8},
                style={'color': TYPES_COLORS[road_type]['hex'], 'weight': 4},
                bubblingMouseEvents=True,
                children=[
                    dl.Tooltip(
                        id={'type': 'wotr-tooltip', 'ix': road_type},
                        children=None,
                        sticky=True,
                        permanent=False,
                    )
                ],
            )
            for road_type in road_types
            if (geojson_data := road_types_geojson_data.get(road_type))
        }

    @dash_app.callback(
        Output('wotr-search-suggestions', 'children'),
        Output('wotr-submit-button', 'disabled'),
        Input('wotr-road-name-input', 'value'),
    )
    def search_input_suggest(input_value: str):
        """
        Populate the search suggestions div.
        """
        road_name_clean = get_road_name_clean(input_value)
        if len(road_name_clean) < 3:
            return [], True

        query = (
            "SELECT DISTINCT \"Name\"\n"
            "FROM \"Roads\".roads_clip\n"
            "WHERE\n"
            "  LOWER(REGEXP_REPLACE(\"Name\", '[^a-zA-Z0-9 ]', '', 'g')) LIKE '%%" + road_name_clean.replace(' ', '%%') + "%%'\n"
        )
        results = roads_pipe.instance_connector.exec(query).fetchall()
        buttons = [
            dbc.Button(
                row[0],
                color='link',
                id={
                    'type': 'wotr-search-suggest-button',
                    'ix': json.dumps(
                        {'Name': row[0]},
                        separators=(',', ':'),
                        sort_keys=True,
                    ),
                },
                size='sm',
                style={'text-decoration': 'none'},
            )
            for row in results
        ]
        return buttons, False

    @dash_app.callback(
        Output('wotr-results-table', 'children'),
        Output('wotr-lines-group', 'children'),
        Output('wotr-map-div', 'style'),
        Input('wotr-submit-button', 'n_clicks'),
        Input('wotr-road-name-input', 'n_submit'),
        State('wotr-road-name-input', 'value'),
    )
    def submit_click(n_clicks, n_submit, road_name):
        if not road_name or len(road_name) < 3:
            raise PreventUpdate

        road_name_clean = get_road_name_clean(road_name)
        query = (
            "SELECT\n"
            "  \"Name\",\n"
            "  \"Type\",\n"
            "  \"Owner\",\n"
            "  \"Phone\",\n"
            "  \"Email\",\n"
            "  \"Online Form\",\n"
            "  ST_Union(\"geometry\") AS \"geometry\"\n"
            "FROM \"Roads\".roads\n"
            "WHERE\n"
            "  LOWER(REGEXP_REPLACE(\"Name\", '[^a-zA-Z0-9 ]', '', 'g')) LIKE '%%" + road_name_clean.replace(' ', '%%') + "%%'\n"
            "GROUP BY\n"
            "  \"Name\",\n"
            "  \"Type\",\n"
            "  \"Owner\",\n"
            "  \"Contact\",\n"
            "  \"Phone\",\n"
            "  \"Email\",\n"
            "  \"Online Form\"\n"
            "ORDER BY \"Name\" ASC\n"
            "LIMIT 100"
        )
        df = gpd.read_postgis(query, roads_pipe.instance_connector.engine, geom_col='geometry')
        non_geo_cols = [col for col in df.columns if col != 'geometry']
        road_types_geojson = get_road_types_geojson(df)
        cards = [
            dbc.Card(
                dbc.CardBody(
                    [
                        dbc.Button(
                            html.B(doc['Name']),
                            size='lg',
                            id={
                                'type': 'wotr-card-name-button',
                                'ix': json.dumps(doc, separators=(',', ':'), sort_keys=True),
                            },
                            **TYPES_COLORS[doc['Type']]['button']
                        ),
                    ] + build_tooltip_contents(doc, include_name=False)
                ),
                style={'margin-top': '15px'},
                **TYPES_COLORS[doc['Type']]['button']
            )
            for doc in df[non_geo_cols].to_dict(orient='records')
        ]

        table_children = [
            html.Div(
                html.P(
                    (f"{len(df)} result" + ('s' if len(df) != 1 else '') + '.'),
                    style={'color': '#999999', 'font-size': '12px', 'text-align': 'right'},
                )
            ),
            build_cards_grid(cards, 3),
        ]

        return table_children, list(road_types_geojson.values()), {'visibility': 'visible'}

    @dash_app.callback(
        Output({'type': 'wotr-tooltip', 'ix': MATCH}, 'children', allow_duplicate=True),
        Input({'type': 'wotr-lines', 'ix': MATCH}, 'hoverData'),
        prevent_initial_call=True,
    )
    def update_tooltip_on_hover(hoverData):
        if not (props := (hoverData or {}).get('properties')):
            raise PreventUpdate

        return build_tooltip_contents(props)

    @dash_app.callback(
        Output('wotr-click-marker-container', 'children'),
        Input({'type': 'wotr-lines', 'ix': ALL}, 'clickData'),
        State('wotr-map', 'clickData'), 
        prevent_initial_call=True,
    )
    def drop_marker_on_click(lines_click_data, map_click_data):
        if not callback_context.inputs:
            raise PreventUpdate
        line_click_data = callback_context.args_grouping[0][0]['value']
        if not line_click_data or not map_click_data:
            raise PreventUpdate

        props = line_click_data['properties']
        latlng = map_click_data.get('latlng')
       
        return dl.Popup(
            build_tooltip_contents(props),
            keepInView=False,
            position=latlng,
            closeButton=True,
            autoClose=False,
            closeOnClick=False,
        )

    def build_tooltip_contents(props, include_name: bool = True):
        content = []
        table_props = ['Type', 'Owner', 'Online Form', 'Email', 'Phone']
        road_type = props['Type']
        table = html.Table(html.Tbody([
            html.Tr(
                [
                    html.Td(
                        prop,
                        style={'font-weight': 'bold'},
                    ),
                    html.Td(
                        (
                            val
                            if (link_prefix := link_prefixes.get(prop)) is None
                            else html.A(
                                (val if prop != 'Online Form' else 'Report an Issue'),
                                href=(link_prefix + (re.sub(r'\D', '', val) if prop == 'Phone' else val)),
                                target="_blank",
                                style={'color': TYPES_COLORS[road_type]['hex']}
                            )
                        ),
                    )
                ],
            )
            for prop in table_props
            if (val := props.get(prop)) and prop != 'Name' and val != 'N/A'
        ]),
            style={
                'borderCollapse': 'separate',
                'borderSpacing': '20px 5px',
            },
        )
        if include_name:
            content.append(html.H6(html.B(props.get('Name', 'N/A'))))
        content.append(table)
        return content

    def build_initial_map_layout(
        boundary_opacity: float = 0.4,
        fill_opacity: float = 0.1,
        iframe_scroll: bool = False,
    ):
        return dl.Map(
            children=[
                dl.TileLayer(
                    url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
                    attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
                )] + ([dl.GestureHandling()] if iframe_scroll else []) + [
                dl.GeoJSON(
                    url='https://meerschaum.io/files/bwg/output/geojson/Boundaries/boundaries_greenville.geojson',
                    id='wotr-boundaries-greenville',
                    style={'color': '#A3CF1F', 'fillColor': '#A3CF1F', 'weight': 2, 'opacity': boundary_opacity, 'fillOpacity': fill_opacity},
                ),
                dl.GeoJSON(
                    url='https://meerschaum.io/files/bwg/output/geojson/Boundaries/boundaries_mauldin.geojson',
                    id='wotr-boundaries-mauldin',
                    style={'color': '#44B6Ca', 'fillColor': '#44B6CA', 'weight': 2, 'opacity': boundary_opacity, 'fillOpacity': fill_opacity},
                ),
                dl.GeoJSON(
                    url='https://meerschaum.io/files/bwg/output/geojson/Boundaries/boundaries_simpsonville.geojson',
                    id='wotr-boundaries-simpsonville',
                    style={'color': '#c6c6c6', 'fillColor': '#c6c6c6', 'weight': 2, 'opacity': 0.8, 'fillOpacity': 0.3},
                ),
                dl.GeoJSON(
                    url='https://meerschaum.io/files/bwg/output/geojson/Boundaries/boundaries_travelers_rest.geojson',
                    id='wotr-boundaries-travelers-rest',
                    style={'color': '#9b6088', 'fillColor': '#9b6088', 'weight': 2, 'opacity': boundary_opacity, 'fillOpacity': fill_opacity},
                ),
                dl.GeoJSON(
                    url='https://meerschaum.io/files/bwg/output/geojson/Boundaries/boundaries_fountain-inn-clipped.geojson',
                    id='wotr-boundaries-fountain-inn',
                    style={'color': '#f2da3a', 'fillColor': '#f2da3a', 'weight': 2, 'opacity': boundary_opacity, 'fillOpacity': fill_opacity},
                ),
                dl.GeoJSON(
                    url='https://meerschaum.io/files/bwg/output/geojson/Boundaries/boundaries_greer-clipped.geojson',
                    id='wotr-boundaries-greer',
                    style={'color': '#eb9360', 'fillColor': '#eb9360', 'weight': 2, 'opacity': boundary_opacity, 'fillOpacity': fill_opacity},
                ),
                dl.GeoJSON(
                    url='https://meerschaum.io/files/bwg/output/geojson/county/BND_GVCNTY.geojson',
                    id='wotr-boundaries-county',
                    style={'color': '#333333', 'weight': 3, 'opacity': 0.5, 'fill': False,},
                ),
                dl.FeatureGroup(id='wotr-highlight-line-group', interactive=True),
                dl.FeatureGroup(id='wotr-lines-group', interactive=True),
                html.Div(id='wotr-click-marker-container'),
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
            style={'height': '30vh'},
            center=[34.843739, -82.393905],
            id='wotr-map',
        )
