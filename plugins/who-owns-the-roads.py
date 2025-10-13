#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Add the "Who Owns the Roads" search application.
"""

from datetime import datetime, timedelta, timezone
import meerschaum as mrsm
from meerschaum.config import get_plugin_config, write_plugin_config
from meerschaum.plugins import web_page, dash_plugin

__version__ = '0.1.1'

required: list[str] = ['dash-leaflet']

TYPES_COLORS: dict[str, str] = {
    'US Highway': '#C7962E',
    'State Road': '#C7962E',
    'County Road': '#B9B9B9',
    'Municipal Road': '#6F9920',
    'Private Drive': '#577DAB',
    'Subdivision': '#577DAB',
}


@dash_plugin
def init_dash(dash_app):
    """Initialize the Plotly Dash application."""

    import json
    
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
        return dbc.Container([
            dcc.Location(id='who-owns-the-roads-location'),
            html.Div(id='who-owns-the-roads-output-div'),
        ])

    @dash_app.callback(
        Output('who-owns-the-roads-output-div', 'children'),
        Input('who-owns-the-roads-location', 'pathname'),
    )
    def render_page_on_url_change(pathname: str):
        """Reload page contents when the URL path changes."""
        form = html.Div([
            dbc.Input(
                placeholder="Road Name",
                id="wotr-road-name-input",
                type='text',
            ),
        ])
        submit_button = dbc.Button(
            "Search",
            id="wotr-submit-button",
            n_clicks=0,
        )
        initial_map_layout = build_initial_map_layout()
        return [
            dcc.Store(id='wotr-click-store'),
            html.Br(),
            dbc.Row([
                dbc.Col(form),
            ]),
            html.Br(),
            dbc.Row([
                dbc.Col([submit_button]),
            ]),
            html.Br(),
            initial_map_layout,
            html.Div(id="wotr-results-table"),
        ]

    @dash_app.callback(
        Output('wotr-results-table', 'children'),
        Output('wotr-lines-group', 'children'),
        Input('wotr-submit-button', 'n_clicks'),
        Input('wotr-road-name-input', 'n_submit'),
        State('wotr-road-name-input', 'value'),
    )
    def submit_click(n_clicks, n_submit, road_name):
        if not (n_clicks or n_submit) or not road_name:
            raise PreventUpdate

        road_name_clean = road_name.strip("'").strip(";").strip('-').lower()
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
            "  LOWER(\"Name\") LIKE '%%" + road_name_clean + "%%'\n"
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
        road_types = df['Type'].unique()
        non_geo_cols = [col for col in df.columns if col != 'geometry']
        road_types_geojson_data = {}
        for road_type in road_types:
            try:
                road_types_geojson_data[road_type] = json.loads(
                    query_df(df, {'Type': road_type}).to_json(to_wgs84=True, drop_id=True)
                )
            except ValueError:
                pass

        table = dbc.Table(
            [html.Thead([html.Tr([html.Th(col) for col in non_geo_cols])])] + [html.Tbody([
                html.Tr([
                    html.Td(
                        val
                        if (link_prefix := link_prefixes.get(col)) is None
                        else (
                            html.A(val, href=(link_prefix + val), style={'textDecoration': 'none'})
                            if col != 'Online Form'
                            else html.A(
                                href=val,
                                target="_blank",
                                children=[dbc.Button("Report an Issue", class_name="w-100")],
                                style={'textDecoration': 'none'},
                            )
                        )
                    ) if ((val := doc.get(col)) and val != 'N/A') else html.Td()
                    for col in non_geo_cols
                ])
                for doc in df[non_geo_cols].to_dict(orient='records')
            ])],
            striped=True,
            bordered=True,
            hover=True,
        )

        road_types_geojson = {
            road_type: dl.GeoJSON(
                data=geojson_data,
                id={'type': 'wotr-lines', 'ix': road_type},
                zoomToBounds=True,
                interactive=True,
                hoverStyle={'color': TYPES_COLORS[road_type], 'weight': 6},
                style={'color': TYPES_COLORS[road_type], 'weight': 3},
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

        table_children = [
            html.Div(
                html.P(
                    (f"{len(df)} result" + ('s' if len(df) != 1 else '') + '.'),
                    style={'color': '#999999', 'font-size': '12px', 'text-align': 'right'},
                )
            ),
            table,
        ]

        return table_children, list(road_types_geojson.values())

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

    def build_tooltip_contents(props):
        content = []
        table_props = ['Type', 'Owner', 'Online Form', 'Email', 'Phone']
        table = html.Table(html.Tbody([
            html.Tr(
                [
                    html.Td(prop, style={'color': '#888888'}),
                    html.Td(
                        (
                            val
                            if not (link_prefix := link_prefixes.get(prop)) is not None
                            else html.A(
                                (val if prop != 'Online Form' else 'Report an Issue'),
                                href=(link_prefix + val),
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
        content.extend([
            html.H6(html.B(props.get('Name', 'N/A'))),
            table,
        ])
        return content


    def build_initial_map_layout(boundary_opacity: float = 0.4, fill_opacity: float = 0.1,):
        return dl.Map(
            children=[
                dl.TileLayer(
                    url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
                    attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
                ),
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
                    style={'color': '#c6c6c6', 'fillColor': '#c6c6c6', 'weight': 2, 'opacity': boundary_opacity, 'fillOpacity': fill_opacity},
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
            style={'height': '60vh'},
            center=[34.843739, -82.393905],
            id='wotr-map',
        )
