#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Map Layers: serve infrastructure layers (bus routes, bike lanes, sidewalks,
PCC bike stress, ...) as mobile-friendly GeoJSON for the BWG app.

Routes (mounted on the Meerschaum API FastAPI app, i.e. https://bwg.mrsm.io):

  GET /map-layers/index.json        -> catalog of available layers + style hints
  GET /map-layers/{layer}.geojson   -> FeatureCollection (pregenerated file if
                                       present, else generated on demand)
  GET /map-layers/route             -> multi-modal directions; see
                                       `map_layers_route` for the parameters
                                       and `_route_multimodal` for the plans

Pregeneration (run nightly after the source pipes sync):

  mrsm compose exec export map_layers

writes `<data>/output/geojson/app/<layer>.geojson` alongside the existing
`export geojson` output tree.

Geometries are simplified in their native SC state-plane CRS (ft units) before
reprojection to WGS84 to keep payloads small enough for flet_map on-device.
"""

import math
import threading
import time
from typing import Any

import meerschaum as mrsm
from meerschaum.actions import make_action
from meerschaum.plugins import api_plugin
from meerschaum.utils.warnings import info, warn

__version__ = '0.16.0'

bwg = mrsm.Plugin('bwg')

FT_PER_M = 3.28084

#: Single source of truth for app map layers.
#: `pipe`: (connector, metric[, location]) on instance sql:bwg.
#: `props`: output property name -> DB column name.
#: `tolerance_m`: Douglas-Peucker tolerance in meters (lines only; applied in
#:   the native ft-based CRS before reprojection).
LAYERS: dict[str, dict[str, Any]] = {
    'bus-routes': {
        'label': 'Bus Routes',
        'kind': 'line',
        'pipe': ('plugin:gtfs', 'route_shapes', 'greenlink'),
        'props': {'route': 'short_name', 'name': 'long_name', 'color': 'color'},
        'tolerance_m': 5,
        'color': '#7B1FA2',
    },
    'bus-stops': {
        'label': 'Bus Stops',
        'kind': 'point',
        'pipe': ('plugin:gtfs', 'stops', 'greenlink'),
        'props': {'name': 'name', 'routes': 'routes'},
        'color': '#7B1FA2',
        'icon': 'directions_bus',
    },
    'bike-lanes': {
        'label': 'Bike Lanes',
        'kind': 'line',
        'pipe': ('plugin:city-gis', 'BicycleInfrastructure'),
        'props': {'bike_type': 'BIKE_TYPE', 'street_name': 'STREET_NAM'},
        'tolerance_m': 5,
        'color': '#2E7D32',
    },
    'sidewalks-city': {
        'label': 'Sidewalks (City)',
        'kind': 'line',
        'pipe': ('plugin:city-gis', 'Sidewalks'),
        'props': {},
        'detail_props': {
            'material': 'MATERIAL',
            'side_of_street': 'SIDE_OF_ST',
            'street_name': 'STREET_NAM',
        },
        'tolerance_m': 8,
        'dissolve_by': True,
        'color': '#1565C0',
    },
    'sidewalks-county': {
        'label': 'Sidewalks (County)',
        'kind': 'line',
        'pipe': ('plugin:greenville-county', 'sidewalks'),
        'props': {},
        'tolerance_m': 8,
        'dissolve_by': True,
        'color': '#0288D1',
    },
    'srt': {
        'label': 'Prisma Health Swamp Rabbit Trail',
        'kind': 'line',
        'pipe': ('sql:bwg', 'srt_segments', 'owners'),
        'props': {'owner': 'Owner', 'segment': 'Segment'},
        'tolerance_m': 3,
        'color': '#FF6F00',
    },
    # County lines plus the city lines that aren't the same sidewalk drawn
    # twice (the two sources overlap heavily inside city limits). Built by
    # `_build_merged_sidewalks`; the per-source layers stay served for
    # back-compat but the app renders this one.
    'sidewalks': {
        'label': 'Sidewalks',
        'kind': 'line',
        'builder': '_build_merged_sidewalks',
        'props': {},
        'color': '#1565C0',
    },
    # Downtown land use: roadway pavement, surface parking lots and parking
    # garages — the same split the parking Grafana dashboard reports on.
    'parking-landuse': {
        'label': 'Parking Land Use',
        'kind': 'fill',
        'builder': '_build_parking_landuse',
        'props': {'kind': 'kind', 'name': 'name'},
        'color': '#8D6E63',
        'color_by': {
            'property': 'kind',
            'map': {
                'roadway': '#66BB6A',
                'lot': '#F57C00',
                'garage': '#FBC02D',
            },
        },
    },
    # Curated, hand-maintained: extend CUSTOM_PATHS below as riders report
    # more shortcuts. Also fed into the routing graph as category 'path'.
    'custom-paths': {
        'label': 'Shortcuts & Tunnels',
        'kind': 'line',
        'builder': '_build_custom_paths',
        'props': {'name': 'name', 'note': 'note'},
        'color': '#AD1457',
    },
    # Named places riders navigate by that no basemap labels — trail
    # landmarks like The Paperclip. Curated in LANDMARKS below.
    'landmarks': {
        'label': 'Trail Landmarks',
        'kind': 'point',
        'builder': '_build_landmarks',
        'props': {'name': 'name', 'note': 'note'},
        'color': '#5D4037',
        'icon': 'flag',
    },
    # Curated, hand-maintained: extend BIKE_BUSINESSES below as more sign on.
    'bike-businesses': {
        'label': 'Bike Friendly Businesses',
        'kind': 'point',
        'builder': '_build_bike_businesses',
        'props': {'name': 'name', 'address': 'address', 'url': 'url', 'note': 'note'},
        'color': '#00897B',
        'icon': 'storefront',
    },
    # Ten years of vulnerable-road-user crashes (Ped.crashes_vulnerable,
    # SCDPS 2014-2024). Served raw; the app renders a heatmap weighted by
    # severity so one death outweighs a cluster of fender-benders.
    'vulnerable-crashes': {
        'label': 'Crash History (Bike/Ped)',
        'kind': 'heatmap',
        'pipe': ('sql:bwg', 'crashes', 'vulnerable'),
        'props': {'killed': 'persons_killed', 'injured': 'persons_injured'},
        'color': '#D32F2F',
    },
    # Duke Energy streetlight poles (Ped.lighting, ~40k points). The router
    # also uses these: unlit streets cost extra after dark.
    'street-lights': {
        'label': 'Street Lights',
        'kind': 'circle',
        'pipe': ('sql:bwg', 'lighting', 'greenville'),
        'props': {},
        'color': '#FFD54F',
    },
    'bike-stress': {
        'label': 'Bike Stress',
        'kind': 'line',
        'pipe': ('sql:bwg', 'stress_levels', 'greenville'),
        'props': {'stress_level': 'stress_level'},
        'detail_props': {'street_name': 'street_name'},
        'tolerance_m': 30,
        'min_part_length_m': 300,
        'dissolve_by': 'stress_level',
        'color_by': {
            'property': 'stress_level',
            'map': {
                'H': '#d73027',
                'MH': '#fc8d59',
                'M': '#fee08b',
                'ML': '#91cf60',
                'L': '#1a9850',
            },
        },
    },
}

#: On-demand generation cache: layer id -> (epoch, geojson string).
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL_SECONDS = 6 * 60 * 60


def _layer_pipe(layer: dict[str, Any]) -> mrsm.Pipe:
    keys = layer['pipe']
    return mrsm.Pipe(*keys, instance='sql:bwg')


def _output_dir():
    """Pregenerated layer files live with the `export geojson` output tree.
    Falls back to the Meerschaum root (e.g. inside the API container, where the
    `bwg` plugin and its data_path config aren't installed)."""
    if bwg.module is not None:
        return bwg.module.get_data_path() / 'output' / 'geojson' / 'app'
    from pathlib import Path
    from meerschaum.config.paths import ROOT_DIR_PATH
    return Path(ROOT_DIR_PATH) / 'output' / 'geojson' / 'app'


#: Bike-friendly businesses, curated by hand. Add a dict here (name, lat, lon
#: in WGS84, plus whatever address/url/note help a rider) and redeploy; the
#: layer serves straight from this list, no ETL involved.
BIKE_BUSINESSES: list[dict[str, Any]] = [
    {
        'name': 'Swamp Rabbit Cafe & Grocery',
        'lat': 34.86993,
        'lon': -82.42195,
        'address': '205 Cedar Lane Rd',
        'url': 'https://www.swamprabbitcafe.com',
        'note': 'Grocery, bakery & cafe right on the trail',
    },
]

#: Off-grid connectors riders actually use but no GIS layer maps: tunnels,
#: cut-throughs, paths across parking lots. Each entry is fed into the routing
#: graph (category 'path' -- a car-free surface, discounted like a bike lane)
#: AND served as the `custom-paths` map layer, so the routes exist on the map
#: and the router stops fighting riders who know them. coords are [lon, lat]
#: WGS84, drawn end to end. Endpoints within ~ROUTE_CONNECT_M (120 m) of the
#: street grid get junction connectors at graph build, so an entry does not
#: need to touch a mapped street exactly. Add an entry and redeploy.
#: v0.13.0: the old hand-drawn "Springer St tunnel path" entry (which cut
#: through the apartment lot and crossed Church St mid-air — a junction
#: connector turned that crossing into a fake ramp that bypassed the actual
#: tunnel) is replaced by the real alignment. OSM supplies the tunnel itself
#: (way 338586347, highway=residential); what NO dataset maps is Springer
#: St's continuation east of the tunnel through Southernside to Briar St —
#: the county centerline, PCC and OSM all stop at the east portal, so the
#: tunnel dead-ended and routes escaped over Church St. Entries here are
#: for connectors that exist on the ground but in no dataset.
#: v0.16.0: entries may carry `route_coords` — the coords fed to the routing
#: graph when they must differ from the drawn line. The Springer entry draws
#: from the WEST tunnel portal (so the shortcuts layer shows one line meeting
#: Springer St) and follows the South Ridge lot's actual drive (OSM ways
#: 339268048/365924783), but only the east-of-Church stretch joins the graph:
#: the OSM tunnel way already routes the bore, and surface coords under it
#: would re-fuse with the S Church St edges above (the v0.13 lesson).
CUSTOM_PATHS: list[dict[str, Any]] = [
    {
        'name': 'Springer St',
        'note': 'Unmapped continuation of Springer St through the '
                'South Ridge Apartments parking lot to Briar St',
        'coords': [
            [-82.40085, 34.83804],      # west tunnel portal (= Springer St)
            [-82.40045, 34.83806],      # east tunnel portal (under Church St)
            [-82.4003886, 34.8380472],
            [-82.400182, 34.838152],    # lot entrance drive
            [-82.400165, 34.838293],
            [-82.400145, 34.838344],
            [-82.40009, 34.838378],
            [-82.400023, 34.838391],    # north drive, west end
            [-82.399553, 34.838378],
            [-82.399382, 34.838373],
            [-82.398772, 34.838372],
            [-82.398552, 34.838381],    # lot NE corner
            [-82.39856, 34.83819],      # Briar St's south end
        ],
        'route_coords': [
            [-82.4003886, 34.8380472],  # just east of the east portal
            [-82.400182, 34.838152],
            [-82.400165, 34.838293],
            [-82.400145, 34.838344],
            [-82.40009, 34.838378],
            [-82.400023, 34.838391],
            [-82.399553, 34.838378],
            [-82.399382, 34.838373],
            [-82.398772, 34.838372],
            [-82.398552, 34.838381],
            [-82.39856, 34.83819],
        ],
    },
]


def _build_custom_paths(debug: bool = False) -> str | None:
    import json
    return json.dumps({
        'type': 'FeatureCollection',
        'features': [
            {
                'type': 'Feature',
                'geometry': {'type': 'LineString', 'coordinates': p['coords']},
                'properties': {
                    k: v for k, v in p.items()
                    if k not in ('coords', 'route_coords')
                },
            }
            for p in CUSTOM_PATHS
        ],
    })


#: How close a city sidewalk line must run to a county line to count as the
#: same sidewalk digitized twice. Both CRSes are ft-based (see DATA.md).
SIDEWALK_DEDUPE_FT = 80.0


#: Named spots riders navigate by that no dataset labels. "The Paperclip" is
#: the Prisma Health Swamp Rabbit Trail's stacked switchback climb just north
#: of Cleveland Park, between the trail's second Lakehurst St crossing and
#: Traxler St. Add a dict (name, lat, lon, note) and redeploy — served as the
#: `landmarks` layer and searchable by name.
LANDMARKS: list[dict[str, Any]] = [
    {
        'name': 'The Paperclip',
        # ON the trail line at the top of the switchback stack (the trail's
        # own vertex nearest due-north of the switchbacks) — the first draft
        # sat ~240 m south, off the trail.
        'lat': 34.8509,
        'lon': -82.3834,
        'note': "The trail's switchback climb between Lakehurst St and "
                'Traxler St — how the Prisma Health Swamp Rabbit Trail '
                'scales the hill',
    },
]


def _build_landmarks(debug: bool = False) -> str | None:
    import json
    return json.dumps({
        'type': 'FeatureCollection',
        'features': [
            {
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [p['lon'], p['lat']]},
                'properties': {k: v for k, v in p.items() if k not in ('lat', 'lon')},
            }
            for p in LANDMARKS
        ],
    })


#: Streets renamed on the ground before the GIS caught up. Keys are the
#: upper-case GIS spelling; values are the real, display-cased name. Applied
#: where a name leaves the data — graph ingest (so steps and voice say the
#: new name), search labels, road-info — never to the source tables, so the
#: mapping simply becomes a no-op once the county updates. Searches for the
#: NEW name are aliased back to the GIS name (see _search_local).
STREET_RENAMES = {
    # Renamed for Fred Garrett Sr., 2024; county centerlines still say Howe.
    'HOWE ST': 'Fred Garrett St',
    'HOWE STREET': 'Fred Garrett St',
}


def _gis_rename(name: str | None) -> str | None:
    """The real street name for a GIS row's name (exact match)."""
    if not name:
        return name
    return STREET_RENAMES.get(str(name).strip().upper(), name)


def _rename_label(label: str | None) -> str | None:
    """Apply STREET_RENAMES inside a longer label ("4 Howe St" and
    "HOWE ST" alike)."""
    import re
    if not label:
        return label
    out = str(label)
    for gis, real in STREET_RENAMES.items():
        out = re.sub(rf'\b{re.escape(gis)}\b', real, out, flags=re.IGNORECASE)
    return out


#: Suffix words dropped when matching street names across datasets
#: ("Springer Street" (OSM) == "SPRINGER ST" (county) == "Springer St").
_STREET_SUFFIXES = {
    'ST', 'STREET', 'RD', 'ROAD', 'AVE', 'AVENUE', 'DR', 'DRIVE', 'LN',
    'LANE', 'CT', 'COURT', 'WAY', 'BLVD', 'BOULEVARD', 'PL', 'PLACE',
    'CIR', 'CIRCLE', 'PKWY', 'PARKWAY', 'HWY', 'HIGHWAY', 'EXT',
}


def _street_base(name: str | None) -> str | None:
    """Casefolded street name with trailing type suffixes dropped, for
    matching the same street across datasets that spell it differently."""
    import re
    if not name:
        return None
    words = re.sub(r'[^A-Za-z0-9 ]', ' ', str(name)).upper().split()
    while words and words[-1] in _STREET_SUFFIXES:
        words.pop()
    return ' '.join(words) or None


#: Surface rows that are really the ROOF of a tunnel: PCC and the county
#: centerline digitized Springer St straight across S Church St, but on the
#: ground Springer passes UNDER Church — the OSM tunnel way (namespaced
#: nodes, portal connectors) is the real edge, and these surface rows minted
#: a node where riders could "turn left onto Church St" out of the tunnel.
#: A row part whose (suffix-stripped) name matches and that PASSES THROUGH
#: the bbox is dropped at ingest — segment-overlap, not vertex-in-box: the
#: PCC stub is a 2-vertex straight line whose endpoints both sit just
#: outside any between-the-portals box. Bboxes sit strictly BETWEEN the
#: portals so rows that legitimately end at a portal survive.
TUNNEL_ROOF_ROWS: list[tuple[str, tuple[float, float, float, float]]] = [
    # (name base, (min lon, min lat, max lon, max lat))
    ('SPRINGER', (-82.40086, 34.8378, -82.40048, 34.8384)),
]


#: At-grade crossings that exist in the data but not on the ground: the
#: S Church St embankment over the Springer tunnel severs the side streets
#: beside it, yet TIGER/county/PCC digitize them straight across (OSM even
#: shares nodes). Unlike TUNNEL_ROOF_ROWS the street is real on BOTH sides —
#: only the crossing is fiction — so the row is CLIPPED, not dropped: any
#: vertex of a matching (suffix-stripped) name inside the bbox is removed,
#: splitting the part, so its chunk endpoints can never fuse with the
#: Church St nodes. Boxes reach ~40 m past each carriageway so the surviving
#: endpoints land well outside the 12 m grid cell of any Church node.
GRADE_SEPARATED_ROWS: list[tuple[str, tuple[float, float, float, float]]] = [
    # (name base, (min lon, min lat, max lon, max lat))
    ('WAKEFIELD', (-82.4009, 34.8383, -82.3999, 34.8391)),
    ('JUDSON', (-82.4008, 34.8387, -82.3997, 34.8395)),
]


def _clip_grade_separated(name: str | None, part: list) -> list[list]:
    """Split a row part at GRADE_SEPARATED_ROWS boxes, dropping the vertices
    inside (see the constant). Returns the surviving sub-parts."""
    base = _street_base(name)
    boxes = (
        [b for nb, b in GRADE_SEPARATED_ROWS if nb == base] if base else []
    )
    if not boxes:
        return [part]

    def _inside(pt) -> bool:
        return any(
            minlon <= pt[0] <= maxlon and minlat <= pt[1] <= maxlat
            for minlon, minlat, maxlon, maxlat in boxes
        )

    parts, run = [], []
    for pt in part:
        if _inside(pt):
            if len(run) >= 2:
                parts.append(run)
            run = []
        else:
            run.append(pt)
    if len(run) >= 2:
        parts.append(run)
    return parts


def _crosses_grade_separation(
    alat: float, alon: float, blat: float, blon: float,
) -> bool:
    """Does the a->b segment's bbox overlap any tunnel-roof / grade-separated
    window? A synthetic connector through one is a ramp between grades that
    do not meet: the S Church St bike lane's chunk end sat 10 m from the
    Springer tunnel's WEST portal (on the bridge above it) and the junction
    pass ramped it straight down onto Springer St."""
    for _name, (minlon, minlat, maxlon, maxlat) in (
        TUNNEL_ROOF_ROWS + GRADE_SEPARATED_ROWS
    ):
        if (
            max(min(alon, blon), minlon) <= min(max(alon, blon), maxlon)
            and max(min(alat, blat), minlat) <= min(max(alat, blat), maxlat)
        ):
            return True
    return False


#: Car-free streets that function as Prisma Health Swamp Rabbit Trail access
#: ramps: Furman College Way is closed to cars and connects the University St
#: / University Ridge roundabouts to the trail. Rows whose suffix-stripped
#: name matches are priced as 'srt' at graph build — routing bias only; the
#: map layers still draw them as the streets they are.
TRAIL_TIER_STREETS = {'FURMAN COLLEGE'}


def _tunnel_roof(name: str | None, part: list) -> bool:
    """Is this row part a tunnel-roof artifact (see TUNNEL_ROOF_ROWS)?"""
    base = _street_base(name)
    if not base:
        return False
    for name_base, (minlon, minlat, maxlon, maxlat) in TUNNEL_ROOF_ROWS:
        if base != name_base:
            continue
        for (x1, y1), (x2, y2) in zip(part, part[1:]):
            # Segment-bbox overlap (conservative rectangle test).
            if (
                max(min(x1, x2), minlon) <= min(max(x1, x2), maxlon)
                and max(min(y1, y2), minlat) <= min(max(y1, y2), maxlat)
            ):
                return True
    return False


def _build_bike_businesses(debug: bool = False) -> str | None:
    import json
    return json.dumps({
        'type': 'FeatureCollection',
        'features': [
            {
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [b['lon'], b['lat']]},
                'properties': {k: v for k, v in b.items() if k not in ('lat', 'lon')},
            }
            for b in BIKE_BUSINESSES
        ],
    })


def _build_merged_sidewalks(debug: bool = False) -> str | None:
    """County sidewalk lines plus the city lines that are NOT within
    SIDEWALK_DEDUPE_FT of any county line — one layer instead of two mostly
    overlapping ones. Dissolved + simplified like the per-source layers."""
    import pandas as pd

    county_pipe = mrsm.Pipe('plugin:greenville-county', 'sidewalks', instance='sql:bwg')
    city_pipe = mrsm.Pipe('plugin:city-gis', 'Sidewalks', instance='sql:bwg')
    frames = []
    for pipe in (county_pipe, city_pipe):
        if not pipe.exists(debug=debug):
            continue
        geom_cols = [
            col for col, typ in pipe.dtypes.items()
            if 'geometry' in typ.lower() or 'geography' in typ.lower()
        ]
        if not geom_cols:
            continue
        df = pipe.get_data([geom_cols[0]], debug=debug)
        if df is None or len(df) == 0:
            continue
        try:
            df.geometry = df.geometry.force_2d()
        except Exception:
            pass
        frames.append(df)
    if not frames:
        warn('No sidewalk sources available for the merged layer.')
        return None

    county = frames[0]
    merged = county
    if len(frames) > 1:
        city = frames[1].to_crs(county.crs)
        # A city line running alongside a county line is the same sidewalk in
        # two datasets; keep the county copy. sindex.query pairs are
        # (city positions, county positions).
        near = county.geometry.sindex.query(
            city.geometry, predicate='dwithin', distance=SIDEWALK_DEDUPE_FT,
        )
        dupes = set(near[0].tolist())
        city_only = city.iloc[[i for i in range(len(city)) if i not in dupes]]
        merged = pd.concat(
            [county[['geometry']], city_only[['geometry']]],
            ignore_index=True,
        ).set_crs(county.crs)

    merged = merged.dissolve()
    merged.geometry = merged.geometry.line_merge()
    merged.geometry = merged.geometry.simplify(8 * FT_PER_M, preserve_topology=True)
    merged = merged.to_crs(4326)
    try:
        merged.geometry = merged.geometry.set_precision(1e-5)
    except Exception:
        pass
    return merged.to_json(drop_id=True)


def _build_parking_landuse(debug: bool = False) -> str | None:
    """Downtown parking land use: roadway pavement, surface lots (paved/unpaved
    city parking polygons in the DTMP study area) and garage building
    footprints. Roadway rows come first so lots and garages draw on top of the
    street surface they overlap."""
    import json

    conn = mrsm.get_connector('sql:bwg')
    query = f'''
    SELECT
        ST_AsGeoJSON(
            ST_Force2D(ST_Transform(
                ST_SimplifyPreserveTopology(ST_MakeValid("geometry"), 5), 4326
            )), 5
        ) AS "gj",
        'roadway' AS "kind",
        NULL AS "name"
    FROM "Parking".dtmp_pavement
    UNION ALL
    SELECT
        ST_AsGeoJSON(
            ST_Force2D(ST_Transform(
                ST_SimplifyPreserveTopology(ST_MakeValid("geometry"), 5), 4326
            )), 5
        ) AS "gj",
        'lot' AS "kind",
        NULL AS "name"
    FROM "Parking".dtmp_parking
    WHERE "FEAT_CODE" IN (121, 122)
    UNION ALL
    SELECT
        ST_AsGeoJSON(
            ST_Force2D(ST_Transform(
                ST_SimplifyPreserveTopology(ST_MakeValid("geometry"), 5), 4326
            )), 5
        ) AS "gj",
        'garage' AS "kind",
        SMART_CAPITALIZE("FAC_LABEL") AS "name"
    FROM "Parking".parking_facilities_greenville
    '''
    df = conn.read(query, debug=debug)
    if df is None or not len(df):
        return None
    features = []
    for row in df.to_dict(orient='records'):
        gj = row.pop('gj', None)
        if not gj:
            continue
        features.append({
            'type': 'Feature',
            'geometry': json.loads(gj),
            'properties': {
                k: (None if v is None or (isinstance(v, float) and v != v) else v)
                for k, v in row.items()
            },
        })
    return json.dumps({'type': 'FeatureCollection', 'features': features})


def _build_layer_geojson(layer_id: str, debug: bool = False) -> str | None:
    """Read the layer's pipe, simplify + reproject, return a GeoJSON string."""
    layer = LAYERS[layer_id]
    builder = layer.get('builder')
    if builder:
        return globals()[builder](debug=debug)
    pipe = _layer_pipe(layer)
    if not pipe.exists(debug=debug):
        warn(f"Pipe for layer '{layer_id}' does not exist: {pipe}")
        return None

    geometry_cols = [
        col
        for col, typ in pipe.dtypes.items()
        if 'geometry' in typ.lower() or 'geography' in typ.lower()
    ]
    if not geometry_cols:
        warn(f"No geometry column for layer '{layer_id}' ({pipe}).")
        return None
    geom_col = geometry_cols[0]

    props = layer.get('props', {})
    df = pipe.get_data([geom_col] + list(props.values()), debug=debug)
    if df is None or len(df) == 0:
        warn(f"No data for layer '{layer_id}' ({pipe}).")
        return None

    df = df.rename(columns={db_col: out_name for out_name, db_col in props.items()})

    try:
        df.geometry = df.geometry.force_2d()  # Z coords (e.g. SRT) waste bytes
    except Exception:
        pass

    # Merge contiguous segments (huge feature-count + payload reduction for
    # sidewalks/stress, which arrive as thousands of tiny per-block segments).
    dissolve_by = layer.get('dissolve_by')
    if dissolve_by:
        df = (
            df.dissolve(by=dissolve_by, as_index=False)
            if dissolve_by is not True
            else df.dissolve()
        )
        df.geometry = df.geometry.line_merge()

    tolerance_m = layer.get('tolerance_m')
    crs_is_ft = df.crs is not None and df.crs.is_projected
    if tolerance_m and layer.get('kind') == 'line' and crs_is_ft:
        df.geometry = df.geometry.simplify(
            tolerance_m * FT_PER_M,
            preserve_topology=True,
        )

    # Dissolved Multi* features hide thousands of tiny disconnected slivers;
    # exploding + dropping the short tail cuts the coord count dramatically
    # without visibly changing the overview.
    min_part_length_m = layer.get('min_part_length_m')
    if min_part_length_m and crs_is_ft:
        df = df.explode(index_parts=False)
        df = df[df.geometry.length >= min_part_length_m * FT_PER_M]

    df = df.to_crs(4326)
    try:
        # ~1.1 m grid: 5-decimal coords serialize ~40% smaller.
        df.geometry = df.geometry.set_precision(1e-5)
    except Exception:
        pass
    return df.to_json(drop_id=True)


#: Cached native SRID per layer (one `ST_SRID` query each).
_SRIDS: dict[str, int] = {}


def _layer_srid(layer_id: str, conn, schema: str, target: str) -> int:
    if layer_id not in _SRIDS:
        df = conn.read(f'SELECT ST_SRID("geometry") AS "srid" FROM "{schema}"."{target}" LIMIT 1')
        _SRIDS[layer_id] = int(df['srid'][0])
    return _SRIDS[layer_id]


def _build_bbox_geojson(
    layer_id: str,
    minlon: float,
    minlat: float,
    maxlon: float,
    maxlat: float,
    zoom: int,
) -> str:
    """Viewport query: bbox-filtered, zoom-simplified features WITH per-feature
    detail properties (the full-layer path dissolves those away). Pure PostGIS —
    no geopandas round-trip. All numeric inputs are coerced before
    interpolation; `layer_id` is validated against LAYERS by the caller.
    """
    import json
    import math

    layer = LAYERS[layer_id]
    pipe = _layer_pipe(layer)
    conn = pipe.instance_connector
    schema = pipe.parameters.get('schema') or 'public'
    target = pipe.target

    minlon, minlat, maxlon, maxlat = (
        float(minlon), float(minlat), float(maxlon), float(maxlat),
    )
    zoom = max(1, min(int(zoom), 22))

    props = {**layer.get('props', {}), **layer.get('detail_props', {})}
    prop_cols = ''.join(
        f', "{db_col}" AS "{out_name}"'
        for out_name, db_col in props.items()
    )

    # ~1 px worth of simplification at this zoom (Greenville latitude), in the
    # layer's native units (US-ft state plane, or degrees for 4326 layers).
    m_per_px = 156543.03 * math.cos(math.radians(34.85)) / (2 ** zoom)
    srid = _layer_srid(layer_id, conn, schema, target)
    if srid == 4326:
        tolerance = m_per_px / 111320
    else:
        tolerance = max(m_per_px * FT_PER_M, 1.0)

    envelope = (
        f"ST_Transform(ST_MakeEnvelope({minlon}, {minlat}, {maxlon}, {maxlat}, 4326), {srid})"
    )
    bbox_limit = int(layer.get('bbox_limit', 4000))
    query = f'''
    SELECT
        ST_AsGeoJSON(
            ST_Force2D(
                ST_Transform(ST_SimplifyPreserveTopology("geometry", {tolerance}), 4326)
            ),
            5
        ) AS "gj"
        {prop_cols}
    FROM "{schema}"."{target}"
    WHERE ST_Intersects("geometry", {envelope})
    ORDER BY ST_Length("geometry") DESC
    LIMIT {bbox_limit}
    '''
    def _clean(v):
        if hasattr(v, 'item'):
            v = v.item()
        if isinstance(v, float) and v != v:  # NaN -> null
            return None
        return v

    df = conn.read(query)
    features = []
    for row in df.to_dict(orient='records'):
        gj = row.pop('gj', None)
        if not gj:
            continue
        features.append({
            'type': 'Feature',
            'geometry': json.loads(gj),
            'properties': {k: _clean(v) for k, v in row.items()},
        })
    return json.dumps({'type': 'FeatureCollection', 'features': features})


#: Greenville County-ish bounds for search (minlon, minlat, maxlon, maxlat).
SEARCH_BOUNDS = (-82.65, 34.58, -82.10, 35.10)

#: Nominatim fallback cache: query -> (epoch, results).
_NOMINATIM_CACHE: dict[str, tuple[float, list]] = {}
_NOMINATIM_TTL_SECONDS = 15 * 60
#: Nominatim asks for at most 1 request/second. Search now queries it on most
#: keystrokes (it fills the POI/business slots, not just a fallback), so an
#: outbound call inside this window returns empty instead of firing -- the
#: next keystroke, or the cache, picks it up.
_NOMINATIM_MIN_INTERVAL_S = 1.1
_NOMINATIM_LAST_CALL = [0.0]


def _escape_like(q: str) -> str:
    return q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _search_local(q: str, limit: int) -> list[dict[str, Any]]:
    """One UNION ALL round trip over bike parking, bus stops, city addresses,
    and PCC street names. ILIKE is fine at these row counts (<= 50k)."""
    import re
    conn = mrsm.get_connector('sql:bwg')
    # Riders search the street's REAL name; the GIS still holds the old one.
    # Alias the query back to what the tables know, then rename the labels on
    # the way out so what the rider typed is what they see.
    for gis, real in STREET_RENAMES.items():
        real_base = re.sub(r'\s+(St|Street)\s*$', '', real, flags=re.IGNORECASE)
        gis_base = re.sub(r'\s+(St|Street)\s*$', '', gis, flags=re.IGNORECASE)
        q = re.sub(rf'\b{re.escape(real_base)}\b', gis_base.title(), q,
                   flags=re.IGNORECASE)
    params = {'q': f'%{_escape_like(q)}%', 'qp': f'{_escape_like(q)}%'}

    arms = []
    parking_pipe = mrsm.Pipe('app', 'locations', 'BikeParking', instance='sql:bwg')
    if parking_pipe.exists():
        arms.append(r'''
        (SELECT
            "name" AS "label",
            COALESCE(NULLIF("address", ''), 'Bike parking') AS "sublabel",
            "lat", "lon",
            'bike-parking' AS "kind",
            ("name" ILIKE :qp ESCAPE '\') AS "prefix"
        FROM "BikeParking"."parking_locations"
        WHERE "name" ILIKE :q ESCAPE '\' OR "address" ILIKE :q ESCAPE '\'
        LIMIT 5)
        ''')
    arms.append(r'''
    (SELECT
        "name" AS "label",
        COALESCE(NULLIF('Bus stop — routes ' || NULLIF("routes", ''), 'Bus stop — routes '), 'Bus stop') AS "sublabel",
        "lat", "lon",
        'bus-stops' AS "kind",
        ("name" ILIKE :qp ESCAPE '\') AS "prefix"
    FROM "transit"."stops"
    WHERE "name" ILIKE :q ESCAPE '\'
    LIMIT 5)
    ''')
    arms.append(r'''
    (SELECT DISTINCT ON ("FULLADDRES")
        SMART_CAPITALIZE("FULLADDRES") AS "label",
        COALESCE("ZIPCODE"::text, 'Greenville') AS "sublabel",
        ST_Y(ST_Transform("geometry", 4326)) AS "lat",
        ST_X(ST_Transform("geometry", 4326)) AS "lon",
        'address' AS "kind",
        ("FULLADDRES" ILIKE :qp ESCAPE '\') AS "prefix"
    FROM "city"."Addresses"
    WHERE "FULLADDRES" ILIKE :q ESCAPE '\'
    ORDER BY "FULLADDRES"
    LIMIT 5)
    ''')
    arms.append(r'''
    (SELECT
        SMART_CAPITALIZE("street_name") AS "label",
        'Street' AS "sublabel",
        ST_Y(ST_Transform(ST_Centroid(ST_Collect("geometry")), 4326)) AS "lat",
        ST_X(ST_Transform(ST_Centroid(ST_Collect("geometry")), 4326)) AS "lon",
        'street' AS "kind",
        BOOL_OR("street_name" ILIKE :qp ESCAPE '\') AS "prefix"
    FROM "pcc"."stress_levels"
    WHERE "street_name" ILIKE :q ESCAPE '\'
    GROUP BY "street_name"
    LIMIT 5)
    ''')

    query = f'''
    SELECT "label", "sublabel", "lat", "lon", "kind"
    FROM ({' UNION ALL '.join(arms)}) AS "matches"
    ORDER BY "prefix" DESC, LENGTH("label")
    LIMIT {int(limit)}
    '''
    df = conn.read(query, params=params)
    results = []
    for row in df.to_dict(orient='records'):
        lat, lon = row.get('lat'), row.get('lon')
        if lat is None or lon is None or lat != lat or lon != lon:
            continue
        results.append({
            'label': _rename_label(row.get('label')) or '',
            'sublabel': row.get('sublabel') or '',
            'lat': float(lat),
            'lon': float(lon),
            'kind': row.get('kind') or '',
        })
    return results


def _nominatim_label(display_name: str) -> tuple[str, str]:
    """(label, sublabel) from a Nominatim `display_name`.

    Nominatim gives the house number its own comma part ("4, McHan Street,
    Downtown, Greenville, ..."), so a naive parts[0] label shows a bare "4"
    with the street exiled to the subtitle. A leading all-numeric part is
    glued back onto the street it numbers."""
    import re
    parts = [p.strip() for p in (display_name or '').split(',') if p.strip()]
    if not parts:
        return display_name or '', ''
    if len(parts) > 1 and re.fullmatch(r'[\d/-]+\w?', parts[0]):
        return f'{parts[0]} {parts[1]}', ', '.join(parts[2:4])
    return parts[0], ', '.join(parts[1:3])


def _search_nominatim(q: str) -> list[dict[str, Any]]:
    """Geocoder fallback for addresses outside the city datasets. Server-side
    so the User-Agent/caching requirements of the Nominatim usage policy live
    in one place."""
    import requests

    cached = _NOMINATIM_CACHE.get(q.lower())
    if cached and (time.time() - cached[0]) < _NOMINATIM_TTL_SECONDS:
        return cached[1]
    if time.time() - _NOMINATIM_LAST_CALL[0] < _NOMINATIM_MIN_INTERVAL_S:
        return []
    _NOMINATIM_LAST_CALL[0] = time.time()

    minlon, minlat, maxlon, maxlat = SEARCH_BOUNDS
    resp = requests.get(
        'https://nominatim.openstreetmap.org/search',
        params={
            'q': q,
            'format': 'jsonv2',
            'limit': 6,
            'viewbox': f'{minlon},{maxlat},{maxlon},{minlat}',
            'bounded': 1,
        },
        headers={'User-Agent': f'bwg-map-layers/{__version__} (data@bikewalkgreenville.org)'},
        timeout=5,
    )
    resp.raise_for_status()
    results = []
    for item in resp.json():
        label, sublabel = _nominatim_label(item.get('display_name') or '')
        results.append({
            'label': label,
            'sublabel': sublabel,
            'lat': float(item['lat']),
            'lon': float(item['lon']),
            'kind': 'osm',
        })
    _NOMINATIM_CACHE[q.lower()] = (time.time(), results)
    return results


# =========================================================================
# Routing (bike / walk / transit)
#
# Feasible-path finder over the Swamp Rabbit Trail, bike lanes, and PCC
# streets -- shared graph, per-mode edge weights. Transit rides Greenlink
# route shapes between stops, with walking legs on either end. The graph is
# built lazily from PostGIS, cached in-process, and traversed with A*.
# =========================================================================

#: Edge weight multiplier per category and mode. Bike leans hard on stress
#: (trail < bike lane < LTS levels); walking mostly cares about distance with
#: a mild preference for the trail and calmer streets. `roll` is walking in a
#: wheelchair: the distance math is the same, but a street with no sidewalk is
#: close to unusable rather than merely unpleasant (see NO_SIDEWALK_FACTOR).
MODE_FACTORS = {
    'bike': {
        # The SRT is deliberately the cheapest surface for every human-powered
        # mode: BWG wants trips steered onto the trail network wherever it is
        # remotely competitive (v0.10.0 deepened the discount again — a bike
        # now detours up to ~5x the direct distance to ride the trail).
        # 'path' covers curated CUSTOM_PATHS + OSM paths/tunnels — car-free
        # like the trail, priced like a bike lane (they cross parking lots
        # and tunnels, not landscaped greenway).
        'srt': 0.18,
        'path': 0.4,
        # A bare OSM footway (apartment paths, plazas): car-free, so barely
        # cheaper than a calm street — but it must never beat the real
        # street beside it by much, or routes wiggle through complexes
        # (the Southernside lesson: Briar St, not the breezeways).
        'footway': 0.9,
        'bike-lane': 0.4,
        'L': 1.0,
        'ML': 1.3,
        'M': 2.5,
        'MH': 6.0,
        'H': 12.0,
    },
    'walk': {
        'srt': 0.45,
        'path': 0.9,
        'footway': 0.8,
        'bike-lane': 1.0,
        'L': 1.0,
        'ML': 1.0,
        'M': 1.1,
        'MH': 1.25,
        'H': 1.5,
    },
    'roll': {
        # 'path' stays neutral for wheelchairs: a curated shortcut's surface
        # and curb cuts are unverified, so don't steer rolls into it — just
        # stop pretending it doesn't exist.
        'srt': 0.45,
        'path': 1.0,
        'footway': 1.0,
        'bike-lane': 1.0,
        'L': 1.0,
        'ML': 1.0,
        'M': 1.05,
        'MH': 1.15,
        'H': 1.3,
    },
    # Last-resort weights: distance only. Used when the mode's own preferences
    # can't produce a route and we fall back to "just use the streets".
    'street': {},
}
MODE_DEFAULT_FACTOR = {'bike': 2.5, 'walk': 1.1, 'roll': 1.1, 'street': 1.0}
#: Casual cycling / walking / wheelchair pace.
MODE_SPEED_M_S = {'bike': 4.2, 'walk': 1.35, 'roll': 1.0, 'street': 1.35}
MODE_NETWORK_NOUN = {
    'bike': 'bikeable',
    'walk': 'walkable',
    'roll': 'wheelchair-accessible',
    'street': 'routable',
}
#: Extra cost for a street segment with no sidewalk beside it. Walking one is
#: unpleasant; rolling one means riding in the traffic lane, so the penalty is
#: severe enough that a much longer sidewalked detour wins.
NO_SIDEWALK_FACTOR = {'walk': 1.6, 'roll': 8.0}
#: Categories that ARE the walking/rolling surface, sidewalk data or not.
OWN_SURFACE_CATEGORIES = ('srt', 'path', 'footway')
#: Bike-stress levels that need a bike facility to feel safe.
STRESSFUL_CATEGORIES = ('M', 'MH', 'H')
#: Categories that ARE a bike facility (car-free surfaces included: riding
#: one never warns "no bike lane").
BIKE_FACILITY_CATEGORIES = ('srt', 'bike-lane', 'path', 'footway')

#: How much traffic the rider is willing to put up with. A tolerance only
#: RE-WEIGHTS; it never removes an edge, so a route always exists and the
#: stretches above the rider's tolerance stay disclosed through `warnings[]`.
#: `balanced` keeps the historical traffic weights; the `srt` discount has
#: deepened twice (0.4 -> 0.28 in v0.6.0, -> 0.18 in v0.10.0) to bias trips
#: hard onto the trail network.
STRESS_LEVELS = ('quiet', 'balanced', 'direct')
DEFAULT_STRESS = 'balanced'
#: The `srt` discounts sit well below the bike-lane ones on purpose (see
#: MODE_FACTORS): the trail network is the backbone BWG wants trips on.
BIKE_STRESS_FACTORS = {
    'quiet': {
        'srt': 0.12, 'path': 0.35, 'footway': 0.8, 'bike-lane': 0.35,
        'L': 1.0, 'ML': 2.0, 'M': 8.0, 'MH': 20.0, 'H': 40.0,
    },
    'balanced': {
        'srt': 0.18, 'path': 0.4, 'footway': 0.9, 'bike-lane': 0.4,
        'L': 1.0, 'ML': 1.3, 'M': 2.5, 'MH': 6.0, 'H': 12.0,
    },
    # "Shortest ride": the facility discount shrinks too, or a direct route
    # would still detour half a mile to pick up a bike lane. The srt discount
    # stays meaningful even here — direct riders still get steered onto the
    # trail when it is anywhere near competitive.
    'direct': {
        'srt': 0.3, 'path': 0.6, 'footway': 0.95, 'bike-lane': 0.6,
        'L': 1.0, 'ML': 1.1, 'M': 1.4, 'MH': 2.0, 'H': 3.0,
    },
}
BIKE_STRESS_DEFAULT_FACTOR = {'quiet': 8.0, 'balanced': 2.5, 'direct': 1.4}
#: Which stress levels earn a "no bike lane here" warning. A rider who asked
#: for the direct route does not want every medium block flagged.
STRESS_WARN_CATEGORIES = {
    'quiet': ('ML', 'M', 'MH', 'H'),
    'balanced': STRESSFUL_CATEGORIES,
    'direct': ('MH', 'H'),
}
#: Realistic trip average for a Class 1/2 e-bike (capped at 20 mph): 15 mph.
EBIKE_SPEED_M_S = 6.7

#: Cost of climbing, as a multiple of the metres climbed: 1 m of rise costs
#: this many metres of flat travel. Descents are free but never bonused -- a
#: downhill bonus invites routes that seek out hills to fall down.
CLIMB_FACTOR = {'bike': 8.0, 'ebike': 2.0, 'walk': 4.0, 'roll': 20.0, 'street': 0.0}
#: Seconds added per metre climbed. Bike ~500 m/h VAM; walking is Naismith's
#: rule (+1 min per 10 m of ascent); a manual chair pays dearly for every rise.
CLIMB_SEC_PER_M = {'bike': 7.0, 'ebike': 2.5, 'walk': 6.0, 'roll': 15.0, 'street': 6.0}
#: ADA's maximum running slope (1:12). Steeper than this gets disclosed to
#: people on foot and on wheels.
STEEP_GRADE = 0.083
#: Noise gates on that disclosure. Nearest-contour sampling quantizes each node
#: to +/-2 ft, so two nodes on genuinely flat ground can differ by a whole 4 ft
#: interval -- over a 10 m leg that reads as a 12% grade. Requiring a rise of
#: more than TWO contour intervals puts the signal outside the error bound, and
#: the minimum run keeps a short leg from turning rounding into a warning.
#: Crying wolf here is worse than staying quiet: someone plans a wheelchair
#: route around a hill that isn't there.
STEEP_MIN_RISE_FT = 8.0
STEEP_MIN_RUN_M = 25.0
#: Contour source for per-node elevation. Already ingested by
#: `projects/county.yaml`; 4 ft interval, so a nearest-contour lookup is good
#: to +/-2 ft. Coverage stops at the county line -- outside it, legs are
#: treated as flat rather than guessed at.
CONTOUR_SOURCE = ('county', 'TOP_CONTOUR')
CONTOUR_BATCH = 4000
#: How far from a node a contour may be and still describe it, in METRES. Two
#: jobs: a node outside the county's coverage gets no elevation instead of one
#: borrowed from miles away, and the nearest-neighbour scan stays bounded by
#: the spatial index (unbounded, a point outside the data walks the whole
#: index -- a single such lookup was measured at over two minutes).
CONTOUR_MAX_M = 2000.0

#: Bike profiles are composite mode keys -- `bike:quiet` ... `ebike:direct` --
#: so every existing `MODE_FACTORS[mode]` lookup keeps working unchanged. Plain
#: `bike` remains valid and means `bike:balanced`.
for _family in ('bike', 'ebike'):
    for _level in STRESS_LEVELS:
        _key = f'{_family}:{_level}'
        MODE_FACTORS[_key] = BIKE_STRESS_FACTORS[_level]
        MODE_DEFAULT_FACTOR[_key] = BIKE_STRESS_DEFAULT_FACTOR[_level]
        MODE_SPEED_M_S[_key] = (
            EBIKE_SPEED_M_S if _family == 'ebike' else MODE_SPEED_M_S['bike']
        )
        MODE_NETWORK_NOUN[_key] = MODE_NETWORK_NOUN['bike']
del _family, _level, _key


def _mode_family(mode: str) -> str:
    """`bike:quiet` -> `bike`, `ebike:direct` -> `ebike`. Picks the climb
    constants and the speed, which differ between bike and e-bike."""
    return mode.split(':', 1)[0]


def _base_mode(mode: str) -> str:
    """The mode as the rider thinks of it: an e-bike is a bike. Drives the
    network nouns, access verbs, icons and plan labels."""
    family = _mode_family(mode)
    return 'bike' if family == 'ebike' else family


def _stress_level(mode: str) -> str:
    """The tolerance baked into a composite bike key."""
    _, _, level = mode.partition(':')
    return level if level in STRESS_LEVELS else DEFAULT_STRESS


def _bike_mode(ebike: bool = False, stress: str = DEFAULT_STRESS) -> str:
    """Compose the mode key for a bike request."""
    level = stress if stress in STRESS_LEVELS else DEFAULT_STRESS
    return f"{'ebike' if ebike else 'bike'}:{level}"


ROUTE_SNAP_MAX_M = 400      # reject termini farther than this from the network
ROUTE_SNAP_RELAXED_M = 2500  # street-fallback snap: warn, don't refuse
ROUTE_SUBDIVIDE_M = 120.0   # split long lines so they're enterable mid-way
ROUTE_GRID_M = 12.0         # endpoint snap cell (stitches segment breaks)
ROUTE_STITCH_M = 60.0       # max connector length between components
ROUTE_CONNECT_M = 120.0     # max trail/lane -> street junction connector

#: How close a sidewalk has to run to a street centerline to count as that
#: street's sidewalk. Both sidewalk CRSes are `+units=ft` (3361 and 6570 alike
#: -- verified against spatial_ref_sys, DON'T assume 3361 is meters), so this
#: is feet: ~24 m covers a wide right-of-way plus digitizing slop.
SIDEWALK_NEAR_FT = 80.0
#: (schema, table) of every sidewalk source, with the SRID of its geometry so
#: the street side of ST_DWithin can be transformed to match (keeps the GiST
#: index usable -- the sidewalk geometry must stay untouched).
SIDEWALK_SOURCES = (
    ('county', 'sidewalks', 6570),
    ('city', 'Sidewalks', 3361),
)

#: Posted speed limits corroborate the PCC stress ratings: a 45 mph road rated
#: L is a data gap, not a pleasant ride. Each stress segment picks up the
#: SPEED of the nearest county centerline (nearest to the segment's MIDPOINT,
#: so a quiet side street doesn't inherit the arterial it dead-ends into) and
#: its stress level is floored by speed band. Escalation only — the posted
#: limit can make a street read worse, never better. Measured blast radius:
#: ~3.3k of 28.4k segments escalate (Pete Hollis Blvd's 40 mph blocks -> MH).
SPEED_SOURCE = ('county', 'TRA_STREETCL', 6570)
SPEED_NEAR_FT = 80.0
STRESS_ORDER = ('L', 'ML', 'M', 'MH', 'H')
SPEED_STRESS_FLOOR = ((45, 'H'), (40, 'MH'), (35, 'M'))

#: Vulnerable-road-user crash history (Ped.crashes_vulnerable, SCDPS
#: 2014-2024) prices real danger into every street and bike-lane edge.
#: Fatality-heavy weights on purpose: downtown logs many low-severity
#: incidents because that is where the people are, while the roads that kill
#: (White Horse Rd, S Academy St, Pete Hollis Blvd) log fewer but deadlier
#: crashes. Score = per-crash weight summed within CRASH_NEAR_FT of the
#: segment, normalized per 100 m; the edge weight multiplier is
#: 1 + min(score, DANGER_CAP) * DANGER_RATE (so ~x3.25 at the cap).
#: Applies to street AND bike-lane categories — paint does not stop a driver
#: (Church St has a bike lane; nobody recommends riding Church St) — never to
#: the car-free srt/path surfaces.
CRASH_SOURCE = ('Ped', 'crashes_vulnerable')
CRASH_NEAR_FT = 100.0
CRASH_W_FATAL = 10.0
CRASH_W_INJURY = 1.0
CRASH_W_OTHER = 0.25
DANGER_RATE = 0.75
DANGER_CAP = 3.0

#: A painted lane on a fast road is not the facility a protected lane is:
#: bike-lane edges additionally multiply by the posted speed of the street
#: they are painted on. Calibrated so a lane on a 35 mph arterial prices
#: like a CALM street (0.4 x 2.5 = 1.0), never better — the Church St
#: lesson: a quiet parallel residential street must win over painted
#: arterial paint. (Sharrows are excluded from the graph entirely — paint
#: saying "share" is not infrastructure.)
LANE_SPEED_PENALTY = ((45, 4.0), (40, 3.0), (35, 2.5))
#: ...and by the PCC stress of the street under the paint (nearest stress
#: segment to the lane's midpoint). Speed alone proved fragile — short
#: 30 mph centerline pieces near junctions let Church St's lane price at
#: the full 0.4 discount. Paint keeps LANE_STRESS_RELIEF of the underlying
#: street's cost — about a third — so a lane on an H street prices at H/3
#: whatever the rider's tolerance. This is applied at QUERY time against the
#: rider's own stress factors (see _lane_stress_multiplier), because a fixed
#: penalty calibrated for `balanced` was trivially cheap next to `quiet`'s
#: 8x/20x/40x street factors: quiet riders — the ones most averse to Church
#: St — were the only ones still routed onto its lane. Calibration for
#: `balanced` is unchanged (x the 0.4 lane base: M nets ~0.8, H nets 4.0 —
#: on the streets that kill, paint barely helps); under `quiet` an H-street
#: lane now prices at 40/3 ≈ 13x, repellent as it should be.
LANE_STRESS_RELIEF = 1.0 / 3.0


def _lane_stress_multiplier(factors: dict, lane_stress: str | None) -> float:
    """How much of the paint discount the street under it takes back, for
    THIS rider's stress factors. Never below 1.0 (a lane is never worse than
    the penalty-free lane price), and 1.0 for modes whose factors don't fear
    the street (walking a bike-lane street is just walking a street)."""
    if not lane_stress:
        return 1.0
    lane = factors.get('bike-lane') or 1.0
    street = factors.get(lane_stress)
    if not street:
        return 1.0
    return max(1.0, street * LANE_STRESS_RELIEF / lane)

#: Duke Energy streetlight poles (Ped.lighting, ~40k points). A street with
#: fewer than one pole per LIT_SPACING_M of length counts as unlit, and after
#: dark unlit street/bike-lane edges pay NIGHT_UNLIT_FACTOR. The trail and
#: paths are untouched — no lighting data covers them either way.
LIGHT_SOURCE = ('Ped', 'lighting')
LIGHT_NEAR_FT = 100.0
LIT_SPACING_M = 100.0
NIGHT_UNLIT_FACTOR = {'bike': 1.35, 'walk': 1.6, 'roll': 1.6, 'street': 1.0}

#: Approximate Greenville sunrise/sunset by month, LOCAL (America/New_York,
#: DST already reflected), decimal hours. Civil-purpose accuracy (~+/-30 min);
#: the point is "penalize unlit streets after dark", not astronomy.
NIGHT_HOURS = {
    1: (7.6, 17.7), 2: (7.4, 18.1), 3: (7.7, 19.6), 4: (7.0, 20.0),
    5: (6.5, 20.4), 6: (6.3, 20.7), 7: (6.4, 20.7), 8: (6.8, 20.3),
    9: (7.1, 19.6), 10: (7.4, 18.9), 11: (7.0, 17.4), 12: (7.4, 17.2),
}

#: Per-request night override (`?night=0/1`); None means "ask the clock".
import contextvars as _contextvars
_NIGHT_OVERRIDE: _contextvars.ContextVar = _contextvars.ContextVar(
    'bwg_night', default=None,
)

#: Per-request trail preference (`?trail=0` turns the SRT bias off — most
#: riders want the trail, some days you want the streets). Request-scoped
#: like the night flag; the plan cache key includes it.
_TRAIL_PREF: _contextvars.ContextVar = _contextvars.ContextVar(
    'bwg_trail', default=True,
)


def _is_night(now=None) -> bool:
    """Is it dark in Greenville right now (or per the request override)?"""
    override = _NIGHT_OVERRIDE.get()
    if override is not None:
        return bool(override)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo('America/New_York'))
    sunrise, sunset = NIGHT_HOURS[now.month]
    hour = now.hour + now.minute / 60.0
    return hour < sunrise or hour >= sunset


def _danger_factor(score_per_100m: float | None) -> float:
    """Crash-history multiplier for an edge's weight."""
    if not score_per_100m or score_per_100m <= 0:
        return 1.0
    return 1.0 + min(score_per_100m, DANGER_CAP) * DANGER_RATE


def _lane_speed_factor(speed_mph: float | None) -> float:
    """How much a painted bike lane's discount erodes with the posted speed
    of the road it is painted on."""
    if speed_mph is None:
        return 1.0
    for mph, factor in LANE_SPEED_PENALTY:
        if speed_mph >= mph:
            return factor
    return 1.0

#: OSM paths — cycleways, foot/bike paths, plazas, and street tunnels (the
#: Springer St tunnel is `highway=residential` + `tunnel=yes`) — fill the
#: shortcut gaps no county GIS layer maps, without hand-curating every one
#: into CUSTOM_PATHS. OSM sidewalk/crossing fragments are excluded: they
#: duplicate the street grid.
#:
#: The data lives in the OSM_PATHS_PIPE (`MapLayers.osm_paths`, synced daily
#: from Overpass via this plugin's `fetch()` — registered by
#: projects/osm-paths.yaml), so it is auditable in SQL and its cadence is a
#: schedule, not a TTL constant. The graph build reads the pipe; a direct
#: Overpass fetch (disk-cached) remains the fallback for environments where
#: the pipe has never synced.
OSM_PATHS_URL = 'https://overpass-api.de/api/interpreter'
OSM_PATHS_TTL_SECONDS = 7 * 24 * 60 * 60
OSM_PATHS_TIMEOUT_S = 120
#: highway= values that are their own car-free surface (category 'path').
OSM_PATH_HIGHWAYS = ('cycleway', 'path', 'pedestrian', 'footway')

OSM_PATHS_PIPE: mrsm.Pipe = mrsm.Pipe(
    'plugin:map-layers', 'osm_paths', 'greenville',
    instance='sql:bwg',
)


def fetch(pipe: mrsm.Pipe, debug: bool = False, **kwargs):
    """Sync connector for OSM_PATHS_PIPE: Overpass path/tunnel ways as rows
    keyed by OSM way id, geometry as LINESTRING 4326. Registered + scheduled
    by projects/osm-paths.yaml."""
    if pipe.metric_key != 'osm_paths':
        return []
    shapely_geometry = mrsm.attempt_import('shapely.geometry', lazy=False)
    return [
        {
            'way_id': e['way_id'],
            'name': e['name'],
            'highway': e['highway'],
            'street': e['street'],
            'geometry': shapely_geometry.LineString(e['coords']),
        }
        for e in _fetch_osm_paths()
    ]


def _stress_floor(category: str | None, speed_mph: float | None) -> str | None:
    """Escalate a stress level to the floor its posted speed demands."""
    if speed_mph is None or category not in STRESS_ORDER:
        return category
    for mph, floor in SPEED_STRESS_FLOOR:
        if speed_mph >= mph:
            if STRESS_ORDER.index(floor) > STRESS_ORDER.index(category):
                return floor
            return category
    return category


def _osm_paths_cache_file():
    return _output_dir() / 'osm-paths.json'


def _osm_category(highway: str | None, street: bool) -> str:
    """Graph category for an OSM way: street tunnels ride like a quiet
    street; cycleways/paths/plazas are trail-grade ('path'); a bare footway
    is car-free but NOT a bike facility-grade shortcut ('footway')."""
    if street:
        return 'L'
    return 'footway' if highway == 'footway' else 'path'


def _parse_overpass_paths(data: dict) -> list[dict[str, Any]]:
    """Overpass `out geom` ways -> [{'name', 'coords', 'street'}]. The SRT is
    skipped by name — it is already its own (cheaper) category, and a slightly
    offset OSM copy would just shadow it."""
    entries = []
    for el in (data.get('elements') or []):
        coords = [
            [p['lon'], p['lat']]
            for p in (el.get('geometry') or [])
            if 'lon' in p and 'lat' in p
        ]
        if len(coords) < 2:
            continue
        tags = el.get('tags') or {}
        name = tags.get('name')
        if name and 'swamp rabbit' in name.lower():
            continue
        entries.append({
            'way_id': str(el.get('id') or ''),
            'name': name,
            'highway': tags.get('highway'),
            'coords': coords,
            # A street tunnel (Springer St) is still a street; only true
            # paths get the car-free 'path' pricing.
            'street': tags.get('highway') not in OSM_PATH_HIGHWAYS,
        })
    return entries


def _fetch_osm_paths() -> list[dict[str, Any]]:
    import requests
    minlon, minlat, maxlon, maxlat = SEARCH_BOUNDS
    bbox = f'{minlat},{minlon},{maxlat},{maxlon}'
    query = f'''[out:json][timeout:90];
(
  way["highway"~"^(cycleway|path|pedestrian)$"]({bbox});
  way["highway"="footway"]["footway"!~"^(sidewalk|crossing)$"]({bbox});
  way["highway"~"^(residential|service|living_street|unclassified|track)$"]["tunnel"="yes"]({bbox});
);
out geom;'''
    resp = requests.post(
        OSM_PATHS_URL,
        data={'data': query},
        headers={'User-Agent': f'bwg-map-layers/{__version__} (data@bikewalkgreenville.org)'},
        timeout=OSM_PATHS_TIMEOUT_S,
    )
    resp.raise_for_status()
    return _parse_overpass_paths(resp.json())


def _osm_path_rows_from_pipe(debug: bool = False) -> list | None:
    """(coords, category, name, has_sidewalk) rows from the synced
    OSM_PATHS_PIPE, or None when the pipe is missing/empty (fresh checkout,
    first boot before the daily job has run)."""
    import json
    try:
        if not OSM_PATHS_PIPE.exists(debug=debug):
            return None
        schema = OSM_PATHS_PIPE.parameters.get('schema') or 'public'
        target = OSM_PATHS_PIPE.target
        df = OSM_PATHS_PIPE.instance_connector.read(
            f'SELECT ST_AsGeoJSON("geometry") AS "gj", "name", "highway", "street" '
            f'FROM "{schema}"."{target}"',
            debug=debug,
        )
    except Exception as e:
        warn(f"Could not read the OSM paths pipe ({e}); falling back to Overpass.")
        return None
    if df is None or not len(df):
        return None
    rows = []
    for rec in df.to_dict(orient='records'):
        gj = rec.get('gj')
        if not gj:
            continue
        coords = (json.loads(gj).get('coordinates') or [])
        if len(coords) < 2:
            continue
        name = rec.get('name')
        name = None if name is None or name != name or not str(name).strip() else str(name)
        highway = rec.get('highway')
        highway = None if highway is None or highway != highway else str(highway)
        street = bool(rec.get('street'))
        # Street rows from the OSM paths pipe are exactly the tunnel'd
        # streets (the Overpass query fetches nothing else street-shaped).
        # The tunnel flag (index 7) keeps their nodes out of the surface
        # grid — see _build_route_graph.
        rows.append((
            coords,
            _osm_category(highway, street),
            name,
            True,
            1.0, True, None,
            street,
        ))
    return rows or None


def _osm_path_rows(debug: bool = False) -> list[tuple[list, str, str | None, bool]]:
    """(coords, category, name, has_sidewalk) rows from OSM: the synced pipe
    first, else a direct disk-cached Overpass fetch. Any failure degrades to
    the stale cache, then to nothing — the graph builds either way."""
    import json
    from_pipe = _osm_path_rows_from_pipe(debug=debug)
    if from_pipe is not None:
        return from_pipe
    cache = _osm_paths_cache_file()
    entries = None
    try:
        if cache.exists() and (time.time() - cache.stat().st_mtime) < OSM_PATHS_TTL_SECONDS:
            entries = json.loads(cache.read_text())
    except Exception:
        entries = None
    if entries is None:
        try:
            entries = _fetch_osm_paths()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(entries))
            info(f"Fetched {len(entries)} OSM path ways from Overpass.")
        except Exception as e:
            warn(f"Overpass unavailable ({e}); using the cached OSM paths if any.")
            try:
                entries = json.loads(cache.read_text())
            except Exception:
                entries = []
    return [
        (
            p['coords'],
            # has_sidewalk=True: these are their own surface (or too short
            # to warn about), not a shoulder-less arterial.
            _osm_category(p.get('highway'), bool(p.get('street'))),
            p.get('name'),
            True,
            1.0, True, None,
            # Street rows here are exactly the tunnel'd streets.
            bool(p.get('street')),
        )
        for p in entries
        if len(p.get('coords') or []) >= 2
    ]

# Transit tuning: how far someone will walk to/from a stop, how close a stop
# must sit to its route shape to count as "on" it, minimum useful ride, an
# average in-service bus speed, and a flat wait estimate (no stop_times yet).
TRANSIT_WALK_MAX_M = 1500.0
TRANSIT_STOP_SNAP_M = 100.0
TRANSIT_MIN_RIDE_M = 250.0
TRANSIT_BUS_SPEED_M_S = 6.5
TRANSIT_WAIT_MIN = 8.0
#: Greenlink buses carry front-load racks, so a bike widens the catchment
#: around a stop a long way past walking distance.
TRANSIT_BIKE_MAX_M = 5000.0

#: With several modes selected, the combined (transit) itinerary is preferred
#: over the fastest single-mode plan as long as it isn't slower than this
#: factor — picking bike + bus means "use them together", not "race them".
MULTIMODAL_MAX_SLOWDOWN = 1.6

#: Alternate routes (`?alt=N`): edges the previous route used cost this much
#: extra on the next pass. High enough to prefer a real parallel option,
#: low enough that when none exists the same route comes back (which the
#: response discloses via `alt_distinct`) instead of an absurd detour.
ALT_AVOID_FACTOR = 1.5
ALT_MAX = 3

# Bike share (Greenville BCycle): how far someone will walk to a dock, how
# long a ride has to be to justify the trip to one, and the unlock overhead.
BIKESHARE_WALK_MAX_M = 1200.0
BIKESHARE_MIN_RIDE_M = 500.0
BIKESHARE_UNLOCK_MIN = 3.0

M_PER_DEG_LAT = 111320.0
_COSLAT = math.cos(math.radians(34.85))  # Greenville-latitude lon scale

_GRAPH: dict[str, Any] = {'epoch': 0.0, 'nodes': None, 'adj': None}
_GRAPH_LOCK = threading.Lock()
_GRAPH_TTL_SECONDS = 24 * 60 * 60


def _equirect_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dx = (lon2 - lon1) * M_PER_DEG_LAT * _COSLAT
    dy = (lat2 - lat1) * M_PER_DEG_LAT
    return (dx * dx + dy * dy) ** 0.5


def _sidewalk_exists_sql(alias: str, srid: int) -> str:
    """SQL boolean: does a sidewalk run alongside this segment?

    One indexed `ST_DWithin` per sidewalk source. The street geometry is what
    gets transformed (never the sidewalk column) so each source's GiST index
    still applies -- without that this is a 28k x 24k nested loop.
    """
    tests = []
    for i, (sw_schema, sw_table, sw_srid) in enumerate(SIDEWALK_SOURCES):
        street_geom = (
            f'{alias}."geometry"'
            if srid == sw_srid
            else f'ST_Transform({alias}."geometry", {sw_srid})'
        )
        tests.append(
            f'EXISTS (SELECT 1 FROM "{sw_schema}"."{sw_table}" AS sw{i} '
            f'WHERE ST_DWithin(sw{i}."geometry", {street_geom}, {SIDEWALK_NEAR_FT}))'
        )
    return '(' + ' OR '.join(tests) + ')'


def _route_source_rows(debug: bool = False) -> list[tuple[list, str, str, bool]]:
    """Pull (coords, category, street name, has_sidewalk) rows for every
    routable segment. Geography-cast lengths sidestep per-CRS units;
    simplification preserves endpoints, so connectivity is unaffected. The
    name feeds turn-by-turn instructions ("Turn right onto Main St"); the
    sidewalk flag feeds walk/roll weighting and the "this stretch may have no
    sidewalk" disclosure.
    """
    import json

    sources = [
        # (layer, category, SQL name expression, check sidewalks?, WHERE)
        # category = stress_level per row for bike-stress:
        ('bike-stress', None, '"street_name"', True, None),
        # PROPOSED lanes are paint that does not exist yet — pricing them as
        # real bike lanes routed riders onto bare arterials (133 rows). And a
        # SHARROW is paint saying "share the road", not infrastructure (322
        # rows priced like real lanes made scary streets read cheap); the
        # street itself is already in the stress data.
        ('bike-lanes', 'bike-lane', '"STREET_NAM"', True,
         'COALESCE(src."STATUS", \'\') != \'PROPOSED\' '
         'AND COALESCE(src."BIKE_TYPE", \'\') != \'SHARROW\''),
        # The trail IS the walking surface; asking whether it has a sidewalk
        # is a category error.
        ('srt', 'srt', "'Prisma Health Swamp Rabbit Trail'", False, None),
    ]
    rows = []
    for layer_id, category, name_expr, check_sidewalks, where in sources:
        layer = LAYERS[layer_id]
        pipe = _layer_pipe(layer)
        conn = pipe.instance_connector
        schema = pipe.parameters.get('schema') or 'public'
        target = pipe.target
        srid = _layer_srid(layer_id, conn, schema, target)
        tolerance = (5 / M_PER_DEG_LAT) if srid == 4326 else (5 * FT_PER_M)
        cat_col = (
            '"stress_level" AS "category"'
            if layer_id == 'bike-stress'
            else f"'{category}' AS \"category\""
        )
        sidewalk_col = (
            f'{_sidewalk_exists_sql("src", srid)} AS "has_sidewalk"'
            if check_sidewalks
            else 'TRUE AS "has_sidewalk"'
        )
        # Posted speed of the nearest centerline to the segment's midpoint
        # (stress rows only). Midpoint-nearest, not ST_DWithin against the
        # whole segment: every side street touches an arterial at the
        # intersection, and MAX-within-80ft would escalate them all.
        sp_schema, sp_table, sp_srid = SPEED_SOURCE
        # ST_PointOnSurface: a point ON the line for any geometry type
        # (MultiLineString included, where interpolation would throw).
        mid = f'ST_Transform(ST_PointOnSurface(src."geometry"), {sp_srid})'
        # Streets and painted lanes carry the safety context columns; the
        # trail is its own car-free surface and skips all of them.
        is_street = layer_id in ('bike-stress', 'bike-lanes')
        speed_col = (
            f'''(
                SELECT NULLIF(cl."SPEED", 0)
                FROM "{sp_schema}"."{sp_table}" AS cl
                WHERE cl."SPEED" IS NOT NULL
                  AND ST_DWithin(cl."geometry", {mid}, {SPEED_NEAR_FT})
                ORDER BY cl."geometry" <-> {mid}
                LIMIT 1
            ) AS "speed_mph"'''
            if is_street
            else 'NULL AS "speed_mph"'
        )
        cr_schema, cr_table = CRASH_SOURCE
        li_schema, li_table = LIGHT_SOURCE
        # Both point sources are 4326: transform the STREET side (never the
        # indexed point column — same lesson as the sidewalk join) and use a
        # degree radius. Slightly anisotropic at this latitude; fine for
        # scoring.
        street_4326 = 'ST_Transform(src."geometry", 4326)'
        crash_deg = CRASH_NEAR_FT / FT_PER_M / M_PER_DEG_LAT
        light_deg = LIGHT_NEAR_FT / FT_PER_M / M_PER_DEG_LAT
        crash_col = (
            f'''(
                SELECT COALESCE(SUM(CASE
                    WHEN c."persons_killed" > 0 THEN {CRASH_W_FATAL}
                    WHEN c."persons_injured" > 0 THEN {CRASH_W_INJURY}
                    ELSE {CRASH_W_OTHER}
                END), 0)
                FROM "{cr_schema}"."{cr_table}" AS c
                WHERE c."geometry" IS NOT NULL
                  AND ST_DWithin(c."geometry", {street_4326}, {crash_deg})
            ) AS "crash_score"'''
            if is_street
            else '0 AS "crash_score"'
        )
        light_col = (
            f'''(
                SELECT COUNT(*)
                FROM "{li_schema}"."{li_table}" AS sl
                WHERE ST_DWithin(sl."geometry", {street_4326}, {light_deg})
            ) AS "lights"'''
            if is_street
            else 'NULL AS "lights"'
        )
        # The stress rating of the street a bike lane is painted on. Prefer
        # the nearest stress segment with the SAME street name — a short
        # lane piece at a junction sits closer to the cross-street's rating
        # (Church St's lane matched Springer's L at the tunnel portal) —
        # then fall back to plain nearest.
        lane_stress_col = (
            f'''COALESCE(
                (
                    SELECT s."stress_level"
                    FROM "pcc"."stress_levels" AS s
                    WHERE s."street_name" = src."STREET_NAM"
                      AND ST_DWithin(s."geometry", {mid}, 200)
                    ORDER BY s."geometry" <-> {mid}
                    LIMIT 1
                ),
                (
                    SELECT s."stress_level"
                    FROM "pcc"."stress_levels" AS s
                    WHERE ST_DWithin(s."geometry", {mid}, {SPEED_NEAR_FT})
                    ORDER BY s."geometry" <-> {mid}
                    LIMIT 1
                )
            ) AS "lane_stress"'''
            if layer_id == 'bike-lanes'
            else 'NULL AS "lane_stress"'
        )
        len_col = 'ST_Length(ST_Transform(src."geometry", 4326)::geography) AS "len_m"'
        query = f'''
        SELECT
            ST_AsGeoJSON(
                ST_Force2D(
                    ST_Transform(
                        ST_SimplifyPreserveTopology(src."geometry", {tolerance}),
                        4326
                    )
                ),
                5
            ) AS "gj",
            {cat_col},
            {name_expr} AS "name",
            {sidewalk_col},
            {speed_col},
            {crash_col},
            {light_col},
            {lane_stress_col},
            {len_col}
        FROM "{schema}"."{target}" AS src
        {f'WHERE {where}' if where else ''}
        '''
        df = conn.read(query, debug=debug)

        def _num(rec, key):
            value = rec.get(key)
            return None if value is None or value != value else float(value)

        for rec in df.to_dict(orient='records'):
            gj = rec.get('gj')
            if not gj:
                continue
            geom = json.loads(gj)
            parts = (
                geom['coordinates']
                if geom['type'] == 'MultiLineString'
                else [geom['coordinates']]
            )
            name = rec.get('name')
            name = None if not name or str(name).strip() in ('', 'N/A', 'None') else _gis_rename(str(name).strip())
            has_sidewalk = bool(rec.get('has_sidewalk'))
            speed = _num(rec, 'speed_mph')
            category = _stress_floor(rec.get('category'), speed)
            len_m = _num(rec, 'len_m') or 0.0
            crash_score = _num(rec, 'crash_score') or 0.0
            danger = (
                _danger_factor(crash_score / len_m * 100.0)
                if is_street and len_m > 0
                else 1.0
            )
            lane_stress = None
            if category == 'bike-lane':
                # Speed penalty is mode-agnostic and bakes into the edge;
                # the stress penalty depends on the rider's tolerance, so
                # the raw rating rides along and _astar applies it.
                danger *= _lane_speed_factor(speed)
                ls = rec.get('lane_stress')
                lane_stress = (
                    str(ls) if ls is not None and ls == ls and str(ls).strip()
                    else None
                )
            lights = _num(rec, 'lights')
            # Lit = at least one pole per LIT_SPACING_M of length (a short
            # block needs one light; a long one needs a series).
            lit = (
                True if not is_street or lights is None or len_m <= 0
                else lights * LIT_SPACING_M >= len_m
            )
            for part in parts:
                if len(part) < 2 or _tunnel_roof(name, part):
                    continue
                for sub in _clip_grade_separated(name, part):
                    rows.append(
                        (sub, category, name, has_sidewalk, danger, lit,
                         lane_stress)
                    )
    # County centerlines PCC never rated (~3.2k of 35k): without them the
    # graph has holes exactly where quiet residential streets live — the
    # Springer St bug: PCC carries only an 8 m stub east of the Church St
    # tunnel, so the tunnel dead-ended and routes escaped over Church St.
    # Base rating 'L' (PCC rates most residentials L), floored by posted
    # speed like every other street. Interstates/US highways excluded: PCC
    # skipping those was a decision, not a gap.
    sp_schema, sp_table, sp_srid = SPEED_SOURCE
    street_4326 = 'ST_Transform(src."geometry", 4326)'
    crash_deg = CRASH_NEAR_FT / FT_PER_M / M_PER_DEG_LAT
    light_deg = LIGHT_NEAR_FT / FT_PER_M / M_PER_DEG_LAT
    cr_schema, cr_table = CRASH_SOURCE
    li_schema, li_table = LIGHT_SOURCE
    gap_query = f'''
    SELECT
        ST_AsGeoJSON(
            ST_Force2D(
                ST_Transform(
                    ST_SimplifyPreserveTopology(src."geometry", {5 * FT_PER_M}),
                    4326
                )
            ),
            5
        ) AS "gj",
        src."LABEL" AS "name",
        NULLIF(src."SPEED", 0) AS "speed_mph",
        {_sidewalk_exists_sql("src", sp_srid)} AS "has_sidewalk",
        (
            SELECT COALESCE(SUM(CASE
                WHEN c."persons_killed" > 0 THEN {CRASH_W_FATAL}
                WHEN c."persons_injured" > 0 THEN {CRASH_W_INJURY}
                ELSE {CRASH_W_OTHER}
            END), 0)
            FROM "{cr_schema}"."{cr_table}" AS c
            WHERE c."geometry" IS NOT NULL
              AND ST_DWithin(c."geometry", {street_4326}, {crash_deg})
        ) AS "crash_score",
        (
            SELECT COUNT(*)
            FROM "{li_schema}"."{li_table}" AS sl
            WHERE ST_DWithin(sl."geometry", {street_4326}, {light_deg})
        ) AS "lights",
        ST_Length(ST_Transform(src."geometry", 4326)::geography) AS "len_m"
    FROM "{sp_schema}"."{sp_table}" AS src
    WHERE COALESCE(src."FEAT_CODE", 0) NOT IN (1370, 1371)
      AND NOT EXISTS (
        SELECT 1 FROM "pcc"."stress_levels" AS s
        -- Midpoint test, not whole-line: a street whose ENDPOINT touches a
        -- rated junction is still unrated along its length (Springer St
        -- east of the tunnel was excluded by its own 8 m PCC stub).
        WHERE ST_DWithin(s."geometry", ST_PointOnSurface(src."geometry"), 80)
      )
    '''
    conn = _layer_pipe(LAYERS['bike-stress']).instance_connector
    gap_df = conn.read(gap_query, debug=debug)
    for rec in gap_df.to_dict(orient='records'):
        gj = rec.get('gj')
        if not gj:
            continue
        geom = json.loads(gj)
        parts = (
            geom['coordinates']
            if geom['type'] == 'MultiLineString'
            else [geom['coordinates']]
        )
        name = rec.get('name')
        name = None if not name or str(name).strip() in ('', 'N/A', 'None') else _gis_rename(str(name).strip())
        speed = rec.get('speed_mph')
        speed = None if speed is None or speed != speed else float(speed)
        category = _stress_floor('L', speed)
        len_m = rec.get('len_m') or 0.0
        crash_score = rec.get('crash_score') or 0.0
        danger = _danger_factor(crash_score / len_m * 100.0) if len_m > 0 else 1.0
        lights = rec.get('lights')
        lit = (
            True if lights is None or len_m <= 0
            else float(lights) * LIT_SPACING_M >= len_m
        )
        for part in parts:
            if len(part) < 2 or _tunnel_roof(name, part):
                continue
            for sub in _clip_grade_separated(name, part):
                rows.append((sub, category, name, bool(rec.get('has_sidewalk')), danger, lit))
    # OSM cycleways, paths and tunnels: the shortcuts no county layer maps.
    rows.extend(_osm_path_rows(debug=debug))
    # Curated off-grid connectors (tunnels, cut-throughs): straight from the
    # CUSTOM_PATHS constant, no database involved. Category 'path' is its own
    # surface, so no sidewalk check applies.
    for p in CUSTOM_PATHS:
        coords = p.get('route_coords') or p.get('coords') or []
        if len(coords) >= 2:
            rows.append((coords, 'path', p.get('name'), True))
    return rows


def _grid_node(lat: float, lon: float) -> tuple[int, int]:
    cell_lat = ROUTE_GRID_M / M_PER_DEG_LAT
    cell_lon = cell_lat / _COSLAT
    return (round(lat / cell_lat), round(lon / cell_lon))


def _subdivide(coords: list) -> list[list]:
    """Split a [lon, lat] coord list into chunks of ~ROUTE_SUBDIVIDE_M so long
    features (the SRT especially) can be entered/exited mid-way."""
    chunks = []
    chunk = [coords[0]]
    acc = 0.0
    for a, b in zip(coords, coords[1:]):
        acc += _equirect_m(a[1], a[0], b[1], b[0])
        chunk.append(b)
        if acc >= ROUTE_SUBDIVIDE_M:
            chunks.append(chunk)
            chunk = [b]
            acc = 0.0
    if len(chunk) >= 2:
        chunks.append(chunk)
    return chunks


def _build_route_graph(debug: bool = False) -> dict[str, Any]:
    """nodes: cell -> (lat, lon); adj: cell -> list of
    (nbr, weight, length_m, category, coords, reversed?, name, has_sidewalk).
    `has_sidewalk` is None for synthetic connectors (unknown, and too short to
    be worth warning about). Largest connected component only, so off-island
    termini snap to routable nodes."""
    nodes: dict = {}
    adj: dict = {}
    street_nodes: set = set()
    path_nodes: set = set()
    # Street name (suffix-stripped) -> chunk-endpoint nodes, for wiring
    # tunnel portals back to their own street below.
    name_nodes: dict = {}
    # (portal node, street name) for every tunnel row's two ends.
    tunnel_portals: list = []

    def _node(lon: float, lat: float, tunnel: bool = False):
        cell = _grid_node(lat, lon)
        # The graph is 2D and the 12 m grid snap FUSES grade-separated
        # geometry: the Springer St tunnel's alignment passes within a cell
        # of the S Church St edges crossing ABOVE it, which minted a node
        # where riders could "turn left onto Church St" out of the tunnel.
        # Tunnel ways therefore key into their own namespace — they can
        # never share a node with surface geometry — and rejoin the network
        # only through explicit portal connectors to their own street.
        if tunnel:
            cell = ('T', *cell)
        if cell not in nodes:
            nodes[cell] = (lat, lon)
            adj[cell] = []
        return cell

    bike_factors = MODE_FACTORS['bike']
    for row in _route_source_rows(debug=debug):
        # Rows may be legacy 4-tuples (tests, custom paths) or carry the
        # (danger, lit, lane_stress, tunnel) context as elements 5-8.
        coords, category, name, has_sidewalk = row[:4]
        extras = (
            (float(row[4]) if len(row) > 4 else 1.0),
            (bool(row[5]) if len(row) > 5 else True),
            (row[6] if len(row) > 6 else None),
        )
        tunnel = bool(row[7]) if len(row) > 7 else False
        # Car-free trail-access streets ride at trail price (Furman College
        # Way) — every mode and stress level, since _astar prices by category.
        if category != 'srt' and _street_base(name) in TRAIL_TIER_STREETS:
            category = 'srt'
        factor = bike_factors.get(category, MODE_DEFAULT_FACTOR['bike'])
        is_path = category in ('srt', 'bike-lane', 'path', 'footway')
        if tunnel and len(coords) >= 2:
            tunnel_portals.append((_node(*coords[0], tunnel=True), name))
            tunnel_portals.append((_node(*coords[-1], tunnel=True), name))
        for chunk in _subdivide(coords):
            length_m = sum(
                _equirect_m(a[1], a[0], b[1], b[0])
                for a, b in zip(chunk, chunk[1:])
            )
            if length_m <= 0:
                continue
            u = _node(chunk[0][0], chunk[0][1], tunnel=tunnel)
            v = _node(chunk[-1][0], chunk[-1][1], tunnel=tunnel)
            (path_nodes if is_path else street_nodes).update((u, v))
            if not tunnel:
                base = _street_base(name)
                if base:
                    name_nodes.setdefault(base, set()).update((u, v))
            if u == v:
                continue  # self-loop after snapping
            weight = length_m * factor * extras[0]
            # Keep the cheapest edge per node pair (bike lane painted on a
            # stressful street should win).
            existing = next(
                (e for e in adj[u] if e[0] == v), None,
            )
            if existing is not None and existing[1] <= weight:
                continue
            if existing is not None:
                adj[u] = [e for e in adj[u] if e[0] != v]
                adj[v] = [e for e in adj[v] if e[0] != u]
            adj[u].append(
                (v, weight, length_m, category, chunk, False, name, has_sidewalk, extras)
            )
            adj[v].append(
                (u, weight, length_m, category, chunk, True, name, has_sidewalk, extras)
            )

    def _add_connector(u, v):
        # A connector between nodes a real edge already joins is worse than
        # useless: it weighs 1.0x its length, so the straight line undercuts
        # the road it duplicates and the router cuts the corner.
        if u == v or any(e[0] == v for e in adj[u]):
            return
        # Never bridge a grade-separation window (fake ramp). Tunnel portals
        # are the one legitimate hop near those boxes — the boxes sit
        # strictly between the portals, but a portal connector's endpoint
        # can graze an edge, so T-nodes are exempt.
        if (
            u[0] != 'T' and v[0] != 'T'
            and _crosses_grade_separation(*nodes[u], *nodes[v])
        ):
            return
        d = _equirect_m(*nodes[u], *nodes[v])
        coords = [
            [nodes[u][1], nodes[u][0]],
            [nodes[v][1], nodes[v][0]],
        ]
        length = max(d, 1.0)
        adj[u].append((v, length, length, 'connector', coords, False, None, None, (1.0, True, None)))
        adj[v].append((u, length, length, 'connector', coords, True, None, None, (1.0, True, None)))

    # Tunnel portals rejoin the surface network HERE and only here: each
    # portal connects to the nearest chunk endpoint of a street with the
    # SAME (suffix-stripped) name — Springer Street's tunnel may meet
    # SPRINGER ST and the Springer St custom path, never the S Church St
    # edges crossing above its bore.
    for portal, portal_name in tunnel_portals:
        base = _street_base(portal_name)
        candidates = name_nodes.get(base) if base else None
        if not candidates:
            continue
        plat, plon = nodes[portal]
        best = min(candidates, key=lambda n: _equirect_m(plat, plon, *nodes[n]))
        if _equirect_m(plat, plon, *nodes[best]) <= ROUTE_CONNECT_M:
            _add_connector(portal, best)

    def _spatial_hash(cells, cell_m):
        cell_lat = cell_m / M_PER_DEG_LAT
        cell_lon = cell_lat / _COSLAT
        spatial: dict = {}
        for cell in cells:
            lat, lon = nodes[cell]
            key = (int(lat / cell_lat), int(lon / cell_lon))
            spatial.setdefault(key, []).append(cell)
        return spatial, cell_lat, cell_lon

    def _nearest_in_hash(spatial, cell_lat, cell_lon, lat, lon, want=None):
        key = (int(lat / cell_lat), int(lon / cell_lon))
        best, best_d = None, None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for v in spatial.get((key[0] + dy, key[1] + dx), []):
                    if want is not None and not want(v):
                        continue
                    d = _equirect_m(lat, lon, *nodes[v])
                    if best_d is None or d < best_d:
                        best, best_d = v, d
        return best, best_d

    # Junctions: the trail (and off-street greenway lanes) crosses dozens of
    # streets without ever sharing an endpoint with one -- left alone it's a
    # long tube with one door, and routes can't get on or off. So every
    # trail/lane-only node gets a connector to the street network
    # (component-agnostic).
    #
    # The connector lands on the nearest point ON a street chunk, splitting
    # that chunk there, NOT on the nearest street *node*. Nodes only exist
    # where `_subdivide` happened to end a ~120 m chunk, so a node-targeted
    # connector routinely points backwards: the Cleveland St bike lane's south
    # end connected 48 m south down Jones Ave rather than to the junction 12 m
    # away, and a bike route arriving at that junction had to ride south and
    # U-turn to get on the lane. Splitting puts the door where the path
    # actually meets the street. Same idea as `_snap_terminus`, applied at
    # build time instead of per request.
    street_chunks = [
        (u, e)
        for u, lst in adj.items()
        for e in lst
        if not e[5] and e[3] not in ('srt', 'bike-lane', 'connector')
        # Never target a tunnel chunk: a surface path near the bore would
        # otherwise get a connector diving underground.
        and u[0] != 'T'
    ]
    orphan_nodes = path_nodes - street_nodes
    if street_chunks and orphan_nodes:
        cell_lat = ROUTE_CONNECT_M / M_PER_DEG_LAT
        cell_lon = cell_lat / _COSLAT
        buckets: dict = {}
        for ci, (_u, e) in enumerate(street_chunks):
            cells: set = set()
            for a, b in zip(e[4], e[4][1:]):
                r0 = int(min(a[1], b[1]) / cell_lat)
                r1 = int(max(a[1], b[1]) / cell_lat)
                c0 = int(min(a[0], b[0]) / cell_lon)
                c1 = int(max(a[0], b[0]) / cell_lon)
                for r in range(r0, r1 + 1):
                    for c in range(c0, c1 + 1):
                        cells.add((r, c))
            for cell in cells:
                buckets.setdefault(cell, []).append(ci)

        # chunk index -> [(position along chunk, seg_i, t, point, path node)]
        splits: dict = {}
        for u in orphan_nodes:
            lat, lon = nodes[u]
            home = (int(lat / cell_lat), int(lon / cell_lon))
            best = None
            seen: set = set()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    for ci in buckets.get((home[0] + dy, home[1] + dx), ()):
                        if ci in seen:
                            continue
                        seen.add(ci)
                        coords = street_chunks[ci][1][4]
                        for si, (a, b) in enumerate(zip(coords, coords[1:])):
                            d, t, pt = _project_seg(lat, lon, a, b)
                            if best is None or d < best[0]:
                                best = (d, ci, si, t, pt)
            if best is None or best[0] > ROUTE_CONNECT_M:
                continue
            _d, ci, si, t, pt = best
            coords = street_chunks[ci][1][4]
            splits.setdefault(ci, []).append(
                (_chunk_pos_m(coords, si, t), si, t, pt, u)
            )

        for ci, requests in splits.items():
            src, edge = street_chunks[ci]
            dst = edge[0]
            coords = edge[4]
            category, name, has_sidewalk = edge[3], edge[6], edge[7]
            extras = edge[8] if len(edge) > 8 else (1.0, True)
            factor = bike_factors.get(category, MODE_DEFAULT_FACTOR['bike'])
            requests.sort()
            # Cut the chunk at every projection, in order along it.
            pieces = []
            prev_si, prev_pt = 0, coords[0]
            for _pos, si, _t, pt, _u in requests:
                pieces.append([prev_pt] + coords[prev_si + 1:si + 1] + [pt])
                prev_si, prev_pt = si, pt
            pieces.append([prev_pt] + coords[prev_si + 1:])
            # Drop the original chunk (identity on its coords, so a parallel
            # edge between the same nodes is left alone) and re-add the parts.
            adj[src] = [
                x for x in adj[src] if not (x[0] == dst and x[4] is coords)
            ]
            adj[dst] = [
                x for x in adj[dst] if not (x[0] == src and x[4] is coords)
            ]
            for piece in pieces:
                length_m = _poly_len_m(piece)
                if length_m <= 0:
                    continue
                pu = _node(piece[0][0], piece[0][1])
                pv = _node(piece[-1][0], piece[-1][1])
                street_nodes.update((pu, pv))
                if pu == pv:
                    continue
                weight = length_m * factor * extras[0]
                adj[pu].append(
                    (pv, weight, length_m, category, piece, False, name, has_sidewalk, extras)
                )
                adj[pv].append(
                    (pu, weight, length_m, category, piece, True, name, has_sidewalk, extras)
                )
            for _pos, _si, _t, pt, u in requests:
                _add_connector(u, _node(pt[0], pt[1]))

    def _components() -> list[set]:
        seen: set = set()
        comps = []
        for start in adj:
            if start in seen:
                continue
            comp = {start}
            queue = [start]
            seen.add(start)
            while queue:
                cur = queue.pop()
                for e in adj[cur]:
                    if e[0] not in seen:
                        seen.add(e[0])
                        comp.add(e[0])
                        queue.append(e[0])
            comps.append(comp)
        return sorted(comps, key=len, reverse=True)

    # The SRT (and some bike lanes) never share endpoints with the street
    # grid: the trail breaks at every road crossing, so it arrives as many
    # disconnected fragments. For THROUGH-travel the fragments must bridge to
    # each other (and to the streets) at every close approach -- one
    # connector per component is a dead end. So: every node gets a straight
    # connector to its nearest node in a DIFFERENT component, if within
    # ROUTE_STITCH_M.
    comps = _components()
    if comps and len(comps) > 1:
        comp_of: dict = {}
        for i, comp in enumerate(comps):
            for c in comp:
                comp_of[c] = i
        spatial, cl, cn = _spatial_hash(
            list(adj), max(ROUTE_STITCH_M, ROUTE_GRID_M),
        )
        for u in list(adj):
            best, best_d = _nearest_in_hash(
                spatial, cl, cn, *nodes[u],
                want=lambda v, _u=u: comp_of[v] != comp_of[_u],
            )
            if best is not None and best_d <= ROUTE_STITCH_M:
                _add_connector(u, best)

    # Keep the largest component (recomputed after stitching).
    comps = _components()
    best_comp = comps[0] if comps else set()
    nodes = {c: nodes[c] for c in best_comp}
    adj = {c: adj[c] for c in best_comp}
    return {
        'epoch': time.time(),
        'nodes': nodes,
        'adj': adj,
        'elev': _node_elevations(nodes, debug=debug),
    }


def _srid_units_per_m(conn, srid: int, debug: bool = False) -> float:
    """How many of `srid`'s linear units make a metre, MEASURED rather than
    assumed.

    The county's layers are a mix of feet (3361, 6570) and degrees (4326), and
    getting this backwards silently scales every threshold by 3.28. So: take
    two points a known distance apart on the ground, transform both into the
    target CRS, and see how long the line came out.
    """
    probe = conn.read(
        f'''
        SELECT
            ST_Distance(
                ST_Transform(ST_SetSRID(ST_MakePoint(-82.4, 34.85), 4326), {srid}),
                ST_Transform(ST_SetSRID(ST_MakePoint(-82.4, 34.86), 4326), {srid})
            ) AS "ns",
            ST_Distance(
                ST_Transform(ST_SetSRID(ST_MakePoint(-82.4, 34.85), 4326), {srid}),
                ST_Transform(ST_SetSRID(ST_MakePoint(-82.39, 34.85), 4326), {srid})
            ) AS "ew"
        ''',
        debug=debug,
    )
    #: 0.01 degrees of latitude on the ground, and 0.01 degrees of longitude at
    #: this latitude -- the same ground distance in each direction only for a
    #: projected CRS. In a geographic one a degree of longitude is the shorter
    #: of the two, so the axes disagree and the LARGER units-per-metre is what
    #: guarantees the radius still covers CONTOUR_MAX_M the narrow way.
    ns_ground_m = 0.01 * M_PER_DEG_LAT
    ew_ground_m = 0.01 * M_PER_DEG_LAT * _COSLAT
    ns, ew = float(probe['ns'].iloc[0]), float(probe['ew'].iloc[0])
    if ns <= 0 or ew <= 0:
        raise ValueError(f"Could not measure the units of SRID {srid}.")
    return max(ns / ns_ground_m, ew / ew_ground_m)


def _node_elevations(nodes: dict, debug: bool = False) -> dict:
    """cell -> elevation in FEET, from the county's 4 ft contours.

    One indexed nearest-neighbour lookup per node (`<->` against the GiST index
    on the contour geometry), batched so no single statement carries the whole
    graph. Contours cover Greenville County only; a node outside it simply gets
    no entry and every leg touching it is treated as flat.

    Elevation is stored per NODE rather than per edge on purpose: `_astar`
    already holds both endpoints of the edge it is relaxing, so climb costs
    need no change to the edge tuple and nothing downstream is re-indexed.
    """
    if not nodes:
        return {}
    schema, table = CONTOUR_SOURCE
    try:
        conn = _layer_pipe(LAYERS['bike-stress']).instance_connector
        srid_df = conn.read(
            f'SELECT ST_SRID("geometry") AS "srid" '
            f'FROM "{schema}"."{table}" LIMIT 1',
            debug=debug,
        )
        srid = int(srid_df['srid'].iloc[0])
        radius = CONTOUR_MAX_M * _srid_units_per_m(conn, srid, debug=debug)
    except Exception as e:
        warn(f"No contour data ({schema}.{table}): routing will ignore hills ({e})")
        return {}

    cells = list(nodes)
    elev: dict = {}
    try:
        for start in range(0, len(cells), CONTOUR_BATCH):
            batch = cells[start:start + CONTOUR_BATCH]
            lons = ','.join(f'{nodes[c][1]!r}' for c in batch)
            lats = ','.join(f'{nodes[c][0]!r}' for c in batch)
            query = f'''
            WITH pts AS (
                SELECT
                    ordinality AS "i",
                    ST_Transform(
                        ST_SetSRID(ST_MakePoint(lon, lat), 4326), {srid}
                    ) AS "geom"
                FROM unnest(ARRAY[{lons}]::float8[], ARRAY[{lats}]::float8[])
                     WITH ORDINALITY AS t(lon, lat)
            )
            SELECT p."i", c."ELEVATION"
            FROM pts p
            CROSS JOIN LATERAL (
                SELECT "ELEVATION"
                FROM "{schema}"."{table}" src
                WHERE ST_DWithin(src."geometry", p."geom", {radius})
                ORDER BY src."geometry" <-> p."geom"
                LIMIT 1
            ) c
            '''
            df = conn.read(query, debug=debug)
            for rec in df.to_dict(orient='records'):
                value = rec.get('ELEVATION')
                if value is None:
                    continue
                elev[batch[int(rec['i']) - 1]] = float(value)
    except Exception as e:
        warn(f"Contour lookup failed: routing will ignore hills ({e})")
        return {}
    return elev


def _get_route_graph(debug: bool = False) -> dict[str, Any]:
    with _GRAPH_LOCK:
        if (
            _GRAPH['nodes'] is None
            or (time.time() - _GRAPH['epoch']) > _GRAPH_TTL_SECONDS
        ):
            info("Building routing graph...")
            built = _build_route_graph(debug=debug)
            _GRAPH.pop('nearest_index', None)  # stale after a rebuild
            _GRAPH.pop('edge_index', None)
            _GRAPH.update(built)
            info(
                f"Routing graph: {len(built['nodes'])} nodes, "
                f"{sum(len(v) for v in built['adj'].values()) // 2} edges."
            )
    return _GRAPH


#: Node lookup grid, ~250 m cells. A linear scan over 37k nodes is 20 ms and
#: the multi-modal planner does a dozen lookups per request.
_NEAREST_GRID_M = 250.0


def _nearest_index(graph: dict) -> dict:
    """Bucket every node into a coarse grid, memoized on the graph dict."""
    index = graph.get('nearest_index')
    if index is not None:
        return index
    cell_lat = _NEAREST_GRID_M / M_PER_DEG_LAT
    cell_lon = cell_lat / _COSLAT
    buckets: dict = {}
    for cell, (nlat, nlon) in graph['nodes'].items():
        buckets.setdefault(
            (int(nlat / cell_lat), int(nlon / cell_lon)), [],
        ).append(cell)
    index = {'buckets': buckets, 'cell_lat': cell_lat, 'cell_lon': cell_lon}
    graph['nearest_index'] = index
    return index


def _nearest_node(graph: dict, lat: float, lon: float):
    """(node, meters) nearest to (lat, lon). Searches outward in grid rings and
    stops as soon as the next ring can't beat the best hit."""
    if not graph.get('nodes'):
        return None, None
    index = _nearest_index(graph)
    buckets = index['buckets']
    cell_lat, cell_lon = index['cell_lat'], index['cell_lon']
    home = (int(lat / cell_lat), int(lon / cell_lon))
    best, best_d = None, None
    for ring in range(0, 40):
        # Everything outside this ring is at least (ring-1) cells away.
        if best_d is not None and best_d <= (ring - 1) * _NEAREST_GRID_M:
            break
        for dy in range(-ring, ring + 1):
            for dx in range(-ring, ring + 1):
                if ring and max(abs(dy), abs(dx)) != ring:
                    continue  # interior already searched
                for cell in buckets.get((home[0] + dy, home[1] + dx), []):
                    d = _equirect_m(lat, lon, *graph['nodes'][cell])
                    if best_d is None or d < best_d:
                        best, best_d = cell, d
    return best, best_d


def _edge_index(graph: dict) -> dict:
    """Spatial index of real (non-connector) edges, memoized on the graph.

    `edges[i]` is `(u, forward_edge_tuple)` — the canonical copy stored under
    `u` with `is_reversed=False`. Every ~250 m cell an edge segment's bbox
    overlaps maps to that edge's index, so a nearest-edge query only has to
    project onto a handful of candidates.
    """
    index = graph.get('edge_index')
    if index is not None:
        return index
    cell_lat = _NEAREST_GRID_M / M_PER_DEG_LAT
    cell_lon = cell_lat / _COSLAT
    edges: list = []
    buckets: dict = {}
    for u, lst in graph['adj'].items():
        for e in lst:
            if e[5] or e[3] == 'connector':
                continue
            ei = len(edges)
            edges.append((u, e))
            cells: set = set()
            coords = e[4]
            for a, b in zip(coords, coords[1:]):
                r0 = int(min(a[1], b[1]) / cell_lat)
                r1 = int(max(a[1], b[1]) / cell_lat)
                c0 = int(min(a[0], b[0]) / cell_lon)
                c1 = int(max(a[0], b[0]) / cell_lon)
                for r in range(r0, r1 + 1):
                    for c in range(c0, c1 + 1):
                        cells.add((r, c))
            for cell in cells:
                buckets.setdefault(cell, []).append(ei)
    index = {
        'buckets': buckets, 'edges': edges,
        'cell_lat': cell_lat, 'cell_lon': cell_lon,
    }
    graph['edge_index'] = index
    return index


def _project_seg(lat: float, lon: float, a: list, b: list):
    """Project (lat, lon) onto segment [lon,lat] a->b.
    Returns (meters, t in [0,1], [lon, lat] of the projection)."""
    mx = M_PER_DEG_LAT * _COSLAT
    my = M_PER_DEG_LAT
    ax, ay = (a[0] - lon) * mx, (a[1] - lat) * my
    bx, by = (b[0] - lon) * mx, (b[1] - lat) * my
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 <= 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    px, py = ax + t * dx, ay + t * dy
    d = (px * px + py * py) ** 0.5
    return d, t, [lon + px / mx, lat + py / my]


def _poly_len_m(coords: list) -> float:
    return sum(
        _equirect_m(a[1], a[0], b[1], b[0])
        for a, b in zip(coords, coords[1:])
    )


def _snap_edge(graph: dict, lat: float, lon: float):
    """Nearest point on any real edge to (lat, lon), or None.
    Returns (edge_i, seg_i, t, [lon, lat], meters)."""
    index = _edge_index(graph)
    if not index['edges']:
        return None
    buckets = index['buckets']
    edges = index['edges']
    cell_lat, cell_lon = index['cell_lat'], index['cell_lon']
    home = (int(lat / cell_lat), int(lon / cell_lon))
    best = None
    seen: set = set()
    for ring in range(0, 40):
        if best is not None and best[4] <= (ring - 1) * _NEAREST_GRID_M:
            break
        for dy in range(-ring, ring + 1):
            for dx in range(-ring, ring + 1):
                if ring and max(abs(dy), abs(dx)) != ring:
                    continue
                for ei in buckets.get((home[0] + dy, home[1] + dx), []):
                    if ei in seen:
                        continue
                    seen.add(ei)
                    coords = edges[ei][1][4]
                    for si, (a, b) in enumerate(zip(coords, coords[1:])):
                        d, t, pt = _project_seg(lat, lon, a, b)
                        if best is None or d < best[4]:
                            best = (ei, si, t, pt, d)
    return best


def _snap_terminus(graph: dict, lat: float, lon: float, tag: str) -> dict:
    """Snap a route terminus to the nearest point ON the network, not just the
    nearest node. Termini that land mid-edge get a virtual node (the edge is
    split for this request only), which is what stops the router from walking
    to a node behind you and doubling back — the McHan St / Haynie St U-turn.
    """
    node, node_d = _nearest_node(graph, lat, lon)
    hit = _snap_edge(graph, lat, lon)
    if hit is None or (node_d is not None and node_d <= hit[4] + 0.01):
        return {'node': node, 'dist': node_d, 'virtual': None}
    ei, seg_i, t, pt, d = hit
    u, edge = _edge_index(graph)['edges'][ei]
    coords = edge[4]
    # Landing on (or a hair from) a chunk endpoint IS that node.
    if _equirect_m(pt[1], pt[0], coords[0][1], coords[0][0]) < 0.5:
        return {'node': u, 'dist': d, 'virtual': None}
    if _equirect_m(pt[1], pt[0], coords[-1][1], coords[-1][0]) < 0.5:
        return {'node': edge[0], 'dist': d, 'virtual': None}
    return {
        'node': ('virt', tag),
        'dist': d,
        'virtual': {'ei': ei, 'seg_i': seg_i, 't': t, 'pt': pt},
    }


def _chunk_pos_m(coords: list, seg_i: int, t: float) -> float:
    """Distance along `coords` of the point (seg_i, t)."""
    pos = sum(
        _equirect_m(coords[k][1], coords[k][0], coords[k + 1][1], coords[k + 1][0])
        for k in range(seg_i)
    )
    return pos + t * _equirect_m(
        coords[seg_i][1], coords[seg_i][0],
        coords[seg_i + 1][1], coords[seg_i + 1][0],
    )


def _register_virtual(
    graph: dict,
    snap: dict,
    extra_adj: dict,
    extra_nodes: dict,
    extra_elev: dict | None = None,
):
    """Split the snapped edge at the projection point: partial edges from the
    virtual node to both real endpoints (and back), overlaid per-request."""
    if snap['virtual'] is None:
        return
    v_info = snap['virtual']
    vid = snap['node']
    u, edge = _edge_index(graph)['edges'][v_info['ei']]
    v, w, total, category, coords, _rev, name, hs = (
        edge[0], edge[1], edge[2], edge[3], edge[4], edge[5], edge[6], edge[7],
    )
    extras = edge[8] if len(edge) > 8 else (1.0, True)
    seg_i, pt = v_info['seg_i'], v_info['pt']
    part_a = coords[:seg_i + 1] + [pt]      # u -> virtual, forward
    part_b = [pt] + coords[seg_i + 1:]      # virtual -> v, forward
    len_a = max(_poly_len_m(part_a), 0.1)
    len_b = max(_poly_len_m(part_b), 0.1)
    fa = len_a / total if total > 0 else 0.5
    fb = len_b / total if total > 0 else 0.5
    extra_nodes[vid] = (pt[1], pt[0])
    # Give the virtual node an elevation interpolated along the edge it splits,
    # or a terminus would read as a cliff against its own street.
    if extra_elev is not None:
        elev = graph.get('elev') or {}
        eu, ev = elev.get(u), elev.get(v)
        if eu is not None and ev is not None:
            extra_elev[vid] = eu + (ev - eu) * min(max(fa, 0.0), 1.0)
    extra_adj.setdefault(vid, []).extend((
        (u, w * fa, len_a, category, part_a, True, name, hs, extras),
        (v, w * fb, len_b, category, part_b, False, name, hs, extras),
    ))
    extra_adj.setdefault(u, []).append(
        (vid, w * fa, len_a, category, part_a, False, name, hs, extras))
    extra_adj.setdefault(v, []).append(
        (vid, w * fb, len_b, category, part_b, True, name, hs, extras))


def _bridge_same_edge(
    graph: dict,
    snap_a: dict,
    snap_b: dict,
    extra_adj: dict,
):
    """Both termini split the SAME edge: connect them directly along it, or the
    only path between them would detour to an endpoint and double back."""
    va, vb = snap_a.get('virtual'), snap_b.get('virtual')
    if not va or not vb or va['ei'] != vb['ei']:
        return
    _u, edge = _edge_index(graph)['edges'][va['ei']]
    w, total, category, coords, name, hs = (
        edge[1], edge[2], edge[3], edge[4], edge[6], edge[7],
    )
    extras = edge[8] if len(edge) > 8 else (1.0, True)
    pos_a = _chunk_pos_m(coords, va['seg_i'], va['t'])
    pos_b = _chunk_pos_m(coords, vb['seg_i'], vb['t'])
    lo, hi = (snap_a, snap_b) if pos_a <= pos_b else (snap_b, snap_a)
    lo_v, hi_v = lo['virtual'], hi['virtual']
    mid = coords[lo_v['seg_i'] + 1:hi_v['seg_i'] + 1]
    bridge = [lo_v['pt']] + mid + [hi_v['pt']]
    blen = max(abs(pos_b - pos_a), 0.1)
    bw = w * (blen / total) if total > 0 else blen
    extra_adj.setdefault(lo['node'], []).append(
        (hi['node'], bw, blen, category, bridge, False, name, hs, extras))
    extra_adj.setdefault(hi['node'], []).append(
        (lo['node'], bw, blen, category, bridge, True, name, hs, extras))


def _edge_deficiency(edge, mode: str) -> str | None:
    """The infrastructure this edge is missing for `mode`, or None.

    `no_sidewalk` -- a street with no sidewalk mapped beside it (walk/roll).
    `no_bike_lane` -- a medium-or-worse stress street with no bike facility.
    """
    category = edge[3]
    if category == 'connector':
        return None
    base = _base_mode(mode)
    if base in ('walk', 'roll'):
        if category in OWN_SURFACE_CATEGORIES:
            return None
        return 'no_sidewalk' if edge[7] is False else None
    if base in ('bike', 'street'):
        if category in BIKE_FACILITY_CATEGORIES:
            return None
        # What counts as "stressful enough to mention" follows the rider's
        # chosen tolerance.
        stressful = STRESS_WARN_CATEGORIES.get(
            _stress_level(mode), STRESSFUL_CATEGORIES,
        )
        return 'no_bike_lane' if category in stressful else None
    return None


def _climb_m(from_elev: float | None, to_elev: float | None) -> float:
    """Metres of RISE between two node elevations (feet), 0 for a descent or
    when either end is off the contour coverage."""
    if from_elev is None or to_elev is None or to_elev <= from_elev:
        return 0.0
    return (to_elev - from_elev) / FT_PER_M


def _astar(
    graph: dict,
    start,
    goal,
    mode: str = 'bike',
    extra_adj: dict | None = None,
    extra_nodes: dict | None = None,
    extra_elev: dict | None = None,
    climb_mode: str | None = None,
    avoid_pairs: set | None = None,
):
    """Returns list of (node, edge) from start to goal, or None. edge is the
    adjacency tuple taken to arrive at node (None for start). Edge weights are
    recomputed per mode from length x category factor x missing-sidewalk
    penalty (the stored weight is the bike one, kept for stats/dedup), plus the
    cost of any climbing between the edge's two nodes.

    `extra_adj` / `extra_nodes` / `extra_elev` overlay per-request virtual nodes
    (termini snapped mid-edge) without mutating the shared graph."""
    import heapq
    import itertools

    nodes = graph['nodes']
    adj = graph['adj']
    extra_adj = extra_adj or {}
    extra_nodes = extra_nodes or {}
    elev = graph.get('elev') or {}
    extra_elev = extra_elev or {}

    def _pos(cell):
        return extra_nodes.get(cell) or nodes[cell]

    def _elev(cell):
        value = extra_elev.get(cell)
        return elev.get(cell) if value is None else value

    glat, glon = _pos(goal)
    base = _base_mode(mode)
    factors = MODE_FACTORS[mode]
    # "Prefer the trail" off (`?trail=0`): the SRT prices like a plain calm
    # street — still routable, just no longer magnetic.
    if not _TRAIL_PREF.get() and factors.get('srt'):
        factors = {**factors, 'srt': max(factors['srt'], 1.0)}
    default_factor = MODE_DEFAULT_FACTOR[mode]
    no_sidewalk_factor = NO_SIDEWALK_FACTOR.get(base, 1.0)
    # Hills are priced for whoever is actually travelling, which is not always
    # the mode being routed: the street fallback routes with flat `street`
    # weights, but the rider is still in a wheelchair and the hill is still
    # there. Traffic tolerance is a preference; a grade is physics.
    climb_factor = CLIMB_FACTOR.get(_mode_family(climb_mode or mode), 0.0)
    night = _is_night()
    night_factor = NIGHT_UNLIT_FACTOR.get(base, 1.0) if night else 1.0
    # Connectors weigh 1.0, so the heuristic can never assume better than that.
    # The climb, danger and night terms only ADD cost (all multipliers >= 1),
    # so the heuristic stays admissible.
    min_factor = min(list(factors.values()) + [1.0])

    def h(cell):
        lat, lon = _pos(cell)
        return _equirect_m(lat, lon, glat, glon) * min_factor

    def edge_weight(edge) -> float:
        category = edge[3]
        if category == 'connector':
            return edge[2]
        weight = edge[2] * factors.get(category, default_factor)
        # (danger multiplier, lit?, lane_stress) — crash history, streetlight
        # coverage and the stress rating under a painted lane, baked in at
        # graph build. Danger is 1.0 on car-free surfaces. The lane-stress
        # penalty is priced HERE, against this rider's own factors: a fixed
        # build-time penalty read as cheap precisely to the quiet riders who
        # most wanted away from streets like Church St.
        extras = edge[8] if len(edge) > 8 else None
        if extras is not None:
            if extras[0] != 1.0:
                weight *= extras[0]
            if night_factor != 1.0 and not extras[1]:
                weight *= night_factor
            if category == 'bike-lane' and len(extras) > 2 and extras[2]:
                weight *= _lane_stress_multiplier(factors, extras[2])
        if no_sidewalk_factor != 1.0 and _edge_deficiency(edge, mode) == 'no_sidewalk':
            weight *= no_sidewalk_factor
        return weight

    # Tiebreaker: virtual-node ids aren't comparable with grid cells, so the
    # heap must never fall through to comparing nodes.
    seq = itertools.count()
    dist = {start: 0.0}
    prev: dict = {start: (None, None)}
    heap = [(h(start), 0.0, next(seq), start)]
    visited: set = set()
    while heap:
        _f, g, _seq, cur = heapq.heappop(heap)
        if cur in visited:
            continue
        visited.add(cur)
        if cur == goal:
            path = []
            node = goal
            while node is not None:
                parent, edge = prev[node]
                path.append((node, edge))
                node = parent
            return list(reversed(path))
        for edge in itertools.chain(adj.get(cur, ()), extra_adj.get(cur, ())):
            nbr, weight = edge[0], edge_weight(edge)
            if climb_factor:
                weight += _climb_m(_elev(cur), _elev(nbr)) * climb_factor
            # Alternate routes: edges the previous route used cost extra, so
            # the search prefers a genuinely different way when one exists.
            # The penalty only ever ADDS cost, so `h` stays admissible.
            if avoid_pairs and (cur, nbr) in avoid_pairs:
                weight *= ALT_AVOID_FACTOR
            ng = g + weight
            if nbr not in dist or ng < dist[nbr]:
                dist[nbr] = ng
                prev[nbr] = (cur, edge)
                heapq.heappush(heap, (ng + h(nbr), ng, next(seq), nbr))
    return None


#: Bearing change (degrees) at a junction -> maneuver type. Ordered by the
#: upper bound of |delta|; the sign picks left vs. right.
TURN_BANDS = (
    (20, 'straight'),
    (50, 'slight'),
    (125, 'turn'),
    (165, 'sharp'),
)
COMPASS = ('north', 'northeast', 'east', 'southeast',
           'south', 'southwest', 'west', 'northwest')
#: Merge a leg into the current step below this bearing change (unless the
#: street name changes) so gentle curves don't generate maneuvers.
STEP_TURN_MIN_DEG = 22.0
#: Same street (or an unnamed/connector leg): only a real corner splits it.
STEP_SAME_STREET_TURN_DEG = 60.0
#: Steps shorter than this (meters) are folded into the previous one.
STEP_MIN_M = 25.0
#: Bearings for maneuver decisions are measured over this much geometry, not a
#: single (possibly centimetres-long) segment. Junction geometry often jogs a
#: few metres the WRONG way before the real turn -- measured segment-by-segment
#: that announced "turn right" at a left turn, which is the one mistake
#: turn-by-turn must never make.
STEP_BEARING_LOOKAHEAD_M = 15.0


def _bearing(a: list, b: list) -> float:
    """Compass bearing in degrees from [lon, lat] `a` to `b`."""
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlon = math.radians(b[0] - a[0])
    y = math.sin(dlon) * math.cos(lat2)
    x = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    )
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _bearing_into(coords: list, dist_m: float = STEP_BEARING_LOOKAHEAD_M) -> float:
    """Bearing from a line's start to the point ~`dist_m` along it."""
    acc = 0.0
    for i in range(1, len(coords)):
        acc += _equirect_m(
            coords[i - 1][1], coords[i - 1][0], coords[i][1], coords[i][0],
        )
        if acc >= dist_m:
            return _bearing(coords[0], coords[i])
    return _bearing(coords[0], coords[-1])


def _bearing_out_of(coords: list, dist_m: float = STEP_BEARING_LOOKAHEAD_M) -> float:
    """Bearing from the point ~`dist_m` before a line's end to its end."""
    acc = 0.0
    for i in range(len(coords) - 2, -1, -1):
        acc += _equirect_m(
            coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0],
        )
        if acc >= dist_m:
            return _bearing(coords[i], coords[-1])
    return _bearing(coords[0], coords[-1])


def _maneuver(delta: float) -> str:
    """Signed bearing change (-180..180) -> maneuver key."""
    side = 'left' if delta < 0 else 'right'
    mag = abs(delta)
    for bound, kind in TURN_BANDS:
        if mag < bound:
            if kind == 'straight':
                return 'straight'
            return f'{kind}-{side}' if kind != 'turn' else side
    return 'uturn'


def _titleize(name: str | None) -> str | None:
    """GIS street names are SHOUTED; instructions shouldn't be. Mirrors the
    DB's SMART_CAPITALIZE: plain .title() mangles Mc names ("MCHAN ST" ->
    "Mchan St", and it's McHan)."""
    import re
    if not name:
        return None
    if not name.isupper():
        return name
    return re.sub(
        r'\bMc([a-z])',
        lambda m: 'Mc' + m.group(1).upper(),
        name.title(),
    )


def _instruction(
    maneuver: str,
    name: str | None,
    bearing: float,
    prev_name: str | None = None,
) -> str:
    name = _titleize(name)
    onto = f' onto {name}' if name else ''
    if maneuver == 'depart':
        heading = COMPASS[int((bearing + 22.5) % 360 // 45)]
        return f'Head {heading}' + (f' on {name}' if name else '')
    if maneuver == 'arrive':
        return 'Arrive at your destination'
    if maneuver == 'straight':
        if not name:
            return 'Continue straight'
        return f'Continue onto {name}' if name != _titleize(prev_name) else f'Continue on {name}'
    if maneuver == 'uturn':
        return f'Make a U-turn{onto}'
    words = {
        'left': 'Turn left',
        'right': 'Turn right',
        'slight-left': 'Bear left',
        'slight-right': 'Bear right',
        'sharp-left': 'Sharp left',
        'sharp-right': 'Sharp right',
    }
    return words.get(maneuver, 'Continue') + onto


def _build_steps(
    legs: list[dict],
    coordinates: list,
    speed_m_s: float = 4.2,
    climb_sec_per_m: float = 0.0,
) -> list[dict]:
    """Collapse routed legs into turn-by-turn steps.

    A new step starts when the street name changes or the bearing swings more
    than [STEP_TURN_MIN_DEG]; everything else accumulates into the step in
    progress, the way Google/Komoot report "continue for 0.4 mi".
    """
    steps: list[dict] = []
    prev_out_bearing: float | None = None

    for leg in legs:
        coords = leg['coords']
        if len(coords) < 2:
            continue
        # Measured over ~15 m, not one segment: junction geometry that jogs a
        # couple of metres right before a left turn must still read "left".
        in_bearing = _bearing_into(coords)
        out_bearing = _bearing_out_of(coords)
        if prev_out_bearing is None:
            maneuver, delta = 'depart', 0.0
        else:
            delta = ((in_bearing - prev_out_bearing + 540.0) % 360.0) - 180.0
            maneuver = _maneuver(delta)

        # Connector edges are synthetic gap-crossers with no street name; they
        # belong to whatever step they interrupt, not to a maneuver of their own.
        # Unnamed legs likewise continue whatever the rider is already on.
        continues = bool(steps) and (
            steps[-1]['name'] == _titleize(leg['name'])
            or leg['category'] == 'connector'
            or not leg['name']
        )
        # A street that curves stays one step; only a real corner splits it.
        limit = STEP_SAME_STREET_TURN_DEG if continues else STEP_TURN_MIN_DEG
        warn = leg.get('warn')
        if steps and continues and abs(delta) < limit:
            steps[-1]['distance_m'] += leg['length_m']
            steps[-1]['climb_m'] += leg.get('climb_m') or 0.0
            if warn:
                steps[-1]['warn'] = steps[-1].get('warn') or warn
                steps[-1]['warn_m'] += leg['length_m']
        else:
            steps.append({
                'maneuver': maneuver,
                'name': _titleize(leg['name']),
                'category': leg['category'],
                'instruction': _instruction(
                    maneuver, leg['name'], in_bearing,
                    prev_name=steps[-1]['name'] if steps else None,
                ),
                'distance_m': leg['length_m'],
                'climb_m': leg.get('climb_m') or 0.0,
                'start_index': leg['start_index'],
                'location': list(coords[0]),
                'bearing': round(in_bearing, 1),
                'warn': warn,
                'warn_m': leg['length_m'] if warn else 0.0,
            })
        prev_out_bearing = out_bearing

    # Fold away hops too short to announce ("in 40 ft, turn left, then turn
    # right" is worse than one instruction).
    merged: list[dict] = []
    for step in steps:
        if merged and step['distance_m'] < STEP_MIN_M and step['maneuver'] != 'depart':
            merged[-1]['distance_m'] += step['distance_m']
            merged[-1]['climb_m'] = (
                merged[-1].get('climb_m', 0.0) + step.get('climb_m', 0.0)
            )
            merged[-1]['warn_m'] = merged[-1].get('warn_m', 0.0) + step.get('warn_m', 0.0)
            merged[-1]['warn'] = merged[-1].get('warn') or step.get('warn')
            continue
        merged.append(step)
    steps = merged

    # A hair-length depart leg (origin -> snapped node) is noise; promote the
    # first real step to the departure instead.
    if (
        len(steps) > 1
        and steps[0]['maneuver'] == 'depart'
        and steps[0]['distance_m'] < STEP_MIN_M
    ):
        head, nxt = steps.pop(0), steps[0]
        nxt['distance_m'] += head['distance_m']
        nxt['climb_m'] += head.get('climb_m') or 0.0
        nxt['start_index'] = head['start_index']
        nxt['location'] = head['location']
        nxt['maneuver'] = 'depart'
        nxt['instruction'] = _instruction('depart', nxt['name'], nxt['bearing'])

    if coordinates:
        steps.append({
            'maneuver': 'arrive',
            'name': None,
            'category': None,
            'instruction': _instruction('arrive', None, 0.0),
            'distance_m': 0.0,
            'climb_m': 0.0,
            'start_index': len(coordinates) - 1,
            'location': list(coordinates[-1]),
        })
    for step in steps:
        step['distance_m'] = round(step['distance_m'], 1)
        climb = step.pop('climb_m', 0.0) or 0.0
        step['climb_ft'] = round(climb * FT_PER_M)
        step['duration_min'] = round(
            (step['distance_m'] / speed_m_s + climb * climb_sec_per_m) / 60, 2
        )
        step['warn_m'] = round(step.get('warn_m') or 0.0, 1)
        step.setdefault('warn', None)
    return steps


#: kind -> (short label template, spoken/banner sentence template). `{d}` is
#: the formatted distance.
WARNING_LABELS = {
    'no_sidewalk': (
        '{d} with no sidewalk',
        'About {d} of this route runs along streets with no sidewalk mapped — '
        'you may be walking on the shoulder.',
    ),
    'no_bike_lane': (
        '{d} with no bike lane',
        'About {d} of this route is on streets with no bike lane — '
        'expect to share the lane with traffic.',
    ),
    'steep': (
        '{d} of steep grade',
        'About {d} of this route climbs more steeply than a wheelchair ramp '
        'is allowed to (1:12) — expect a hard push.',
    ),
}


def _format_mi(meters: float) -> str:
    """Imperial, because Greenville. Feet under a quarter mile."""
    if meters < 400:
        return f"{int(round(meters * FT_PER_M / 50.0) * 50)} ft"
    return f"{meters / 1609.344:.1f} mi"


def _summarize_warnings(ranges: list[dict]) -> list[dict]:
    """Collapse per-segment gaps into one entry per kind, for the banner."""
    totals: dict[str, float] = {}
    for r in ranges:
        totals[r['kind']] = totals.get(r['kind'], 0.0) + r['distance_m']
    out = []
    for kind, meters in sorted(totals.items(), key=lambda kv: -kv[1]):
        short, sentence = WARNING_LABELS.get(kind, ('{d}', '{d}'))
        pretty = _format_mi(meters)
        out.append({
            'kind': kind,
            'distance_m': round(meters, 1),
            'label': short.format(d=pretty),
            'message': sentence.format(d=pretty),
        })
    return out


def _warn_ranges(legs: list[dict]) -> list[dict]:
    """Merge consecutive deficient legs into coordinate index ranges so the app
    can redraw exactly those stretches in the "watch out" style."""
    ranges: list[dict] = []
    for leg in legs:
        warn = leg.get('warn')
        if not warn:
            continue
        start = leg['start_index']
        end = start + max(len(leg['coords']) - 1, 1)
        if ranges and ranges[-1]['kind'] == warn and ranges[-1]['end'] >= start:
            ranges[-1]['end'] = max(ranges[-1]['end'], end)
            ranges[-1]['distance_m'] += leg['length_m']
            continue
        ranges.append({
            'kind': warn,
            'start': start,
            'end': end,
            'distance_m': leg['length_m'],
        })
    for r in ranges:
        r['distance_m'] = round(r['distance_m'], 1)
    return ranges


def _route_core(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    mode: str = 'bike',
    snap_max_m: float = ROUTE_SNAP_MAX_M,
    warn_mode: str | None = None,
    avoid_pairs: set | None = None,
    pairs_out: set | None = None,
) -> dict[str, Any]:
    """One A* pass with `mode`'s weights; raises ValueError with a user-facing
    message when no route is possible.

    `warn_mode` is the mode the DISCLOSURE is written for. It differs from
    `mode` on the street fallback: routed with flat street weights, but still
    told "you're walking, and this bit has no sidewalk".

    `avoid_pairs` penalizes directed node pairs the previous route used (the
    alternate-route mechanism); `pairs_out`, when given, collects this route's
    own pairs (both directions, real nodes only) for the next pass.
    """
    graph = _get_route_graph()
    if not graph['nodes']:
        raise ValueError("Routing network is unavailable.")
    warn_mode = warn_mode or mode

    snap_s = _snap_terminus(graph, from_lat, from_lon, 'start')
    snap_g = _snap_terminus(graph, to_lat, to_lon, 'goal')
    start, start_d = snap_s['node'], snap_s['dist']
    goal, goal_d = snap_g['node'], snap_g['dist']
    if start is None or goal is None:
        raise ValueError("Routing network is unavailable.")
    if start_d > snap_max_m or goal_d > snap_max_m:
        noun = MODE_NETWORK_NOUN.get(warn_mode, 'routable')
        raise ValueError(f"No {noun} network near that point.")

    # Mid-edge termini become per-request virtual nodes so the route leaves
    # from the snapped point itself instead of the nearest endpoint.
    extra_adj: dict = {}
    extra_nodes: dict = {}
    extra_elev: dict = {}
    _register_virtual(graph, snap_s, extra_adj, extra_nodes, extra_elev)
    _register_virtual(graph, snap_g, extra_adj, extra_nodes, extra_elev)
    _bridge_same_edge(graph, snap_s, snap_g, extra_adj)

    elev = graph.get('elev') or {}

    def _node_elev(cell):
        value = extra_elev.get(cell)
        return elev.get(cell) if value is None else value

    legs: list[dict] = []
    climb_m = 0.0
    #: [distance_from_start_m, elevation_ft] at each leg boundary with a known
    #: elevation — the app's elevation-preview sparkline.
    profile: list[list[float]] = []
    profile_dist = 0.0
    if start == goal:
        nlat, nlon = graph['nodes'][start]
        coordinates = [[from_lon, from_lat], [nlon, nlat], [to_lon, to_lat]]
        distance_m = start_d + goal_d
        breakdown: dict[str, float] = {}
        legs.append({
            'coords': coordinates,
            'name': None,
            'category': None,
            'length_m': distance_m,
            'start_index': 0,
            'warn': None,
            'climb_m': 0.0,
        })
    else:
        path = _astar(
            graph, start, goal, mode=mode,
            extra_adj=extra_adj, extra_nodes=extra_nodes,
            extra_elev=extra_elev, climb_mode=warn_mode,
            avoid_pairs=avoid_pairs,
        )
        if path is None:
            raise ValueError("Couldn't find a connected route.")
        coordinates = [[from_lon, from_lat]]
        distance_m = start_d + goal_d
        breakdown = {}
        prev_node = start
        profile_dist = start_d
        for _node, edge in path:
            if edge is None:
                continue
            _nbr, _w, length_m, category, coords, is_reversed, name = edge[:7]
            seg = list(reversed(coords)) if is_reversed else list(coords)
            start_index = max(len(coordinates) - 1, 0)
            leg_coords = [coordinates[-1]] + seg if coordinates else list(seg)
            if coordinates and coordinates[-1] == seg[0]:
                seg = seg[1:]
                leg_coords = [coordinates[-1]] + seg
            coordinates.extend(seg)
            distance_m += length_m
            key = str(category or 'unknown')
            breakdown[key] = breakdown.get(key, 0.0) + length_m
            from_elev, to_elev = _node_elev(prev_node), _node_elev(_node)
            rise_m = _climb_m(from_elev, to_elev)
            climb_m += rise_m
            if from_elev is not None and not profile:
                profile.append([profile_dist, from_elev])
            profile_dist += length_m
            if to_elev is not None:
                profile.append([profile_dist, to_elev])
            # For the disclosure, what matters is how steep the ground is, not
            # which way the rider is pointed: rolling DOWN a 12% grade is its
            # own hazard.
            grade_m = (
                abs(to_elev - from_elev) / FT_PER_M
                if from_elev is not None and to_elev is not None
                else 0.0
            )
            # Virtual (per-request) nodes are excluded: penalizing the only
            # edges that reach a terminus would just shove the next pass onto
            # a worse snap, not a different street.
            if (
                pairs_out is not None
                and prev_node in graph['nodes']
                and _node in graph['nodes']
            ):
                pairs_out.add((prev_node, _node))
                pairs_out.add((_node, prev_node))
            prev_node = _node
            leg_warn = _edge_deficiency(edge, warn_mode)
            # A hill only gets the callout when nothing worse is wrong with the
            # leg: the schema carries one warning, and a missing sidewalk
            # outranks a grade.
            if (
                leg_warn is None
                and _base_mode(warn_mode) in ('walk', 'roll')
                # A connector is a synthetic straight line between two nodes,
                # not a surface anyone walks; its "grade" means nothing.
                and category != 'connector'
                and length_m >= STEEP_MIN_RUN_M
                and grade_m * FT_PER_M >= STEEP_MIN_RISE_FT
                and grade_m / length_m > STEEP_GRADE
            ):
                leg_warn = 'steep'
            legs.append({
                'coords': leg_coords,
                'name': name,
                'category': category,
                'length_m': length_m,
                'start_index': start_index,
                'warn': leg_warn,
                'climb_m': rise_m,
            })
        coordinates.append([to_lon, to_lat])

    speed = MODE_SPEED_M_S[warn_mode]
    climb_seconds = climb_m * CLIMB_SEC_PER_M.get(_mode_family(warn_mode), 0.0)
    ranges = _warn_ranges(legs)
    # Downsample the elevation profile: a long route touches hundreds of
    # nodes and the sparkline can't show more than ~a hundred anyway.
    if len(profile) > 120:
        stride = len(profile) / 119.0
        profile = [profile[int(i * stride)] for i in range(119)] + [profile[-1]]
    elevation_profile = (
        [[round(d, 1), round(e)] for d, e in profile] if len(profile) >= 2 else None
    )
    return {
        'type': 'Feature',
        'geometry': {'type': 'LineString', 'coordinates': coordinates},
        'properties': {
            'mode': _base_mode(warn_mode),
            'ebike': _mode_family(warn_mode) == 'ebike',
            'stress': _stress_level(warn_mode),
            'distance_m': round(distance_m, 1),
            'distance_mi': round(distance_m / 1609.344, 2),
            'duration_min': round((distance_m / speed + climb_seconds) / 60, 1),
            'climb_ft': round(climb_m * FT_PER_M),
            'elevation_profile': elevation_profile,
            'stress_breakdown': {k: round(v, 1) for k, v in breakdown.items()},
            'from_snap_m': round(start_d, 1),
            'to_snap_m': round(goal_d, 1),
            'warn_ranges': ranges,
            'warnings': _summarize_warnings(ranges),
            'steps': _build_steps(
                legs, coordinates, speed_m_s=speed,
                climb_sec_per_m=CLIMB_SEC_PER_M.get(_mode_family(warn_mode), 0.0),
            ),
        },
    }


def _route(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    mode: str = 'bike',
    street_fallback: bool = True,
    avoid_pairs: set | None = None,
    pairs_out: set | None = None,
) -> dict[str, Any]:
    """A bike / walk / roll route, falling back to plain streets when the
    mode's own network can't get there.

    The graph is the street grid plus the trail and bike lanes, so the fallback
    isn't a different network -- it's the same one with the mode's preferences
    switched off and the snap radius widened. What makes it honest is that the
    disclosure stays in the requested mode: the response still marks every
    stretch missing a sidewalk (walk/roll) or a bike facility (bike), and says
    `fallback: 'street'` so the app can lead with the caveat.
    """
    try:
        return _route_core(
            from_lat, from_lon, to_lat, to_lon, mode=mode,
            avoid_pairs=avoid_pairs, pairs_out=pairs_out,
        )
    except ValueError as first_error:
        if not street_fallback or mode == 'street':
            raise
        feature = _route_core(
            from_lat, from_lon, to_lat, to_lon,
            mode='street',
            snap_max_m=ROUTE_SNAP_RELAXED_M,
            warn_mode=mode,
            avoid_pairs=avoid_pairs,
            pairs_out=pairs_out,
        )
        props = feature['properties']
        props['fallback'] = 'street'
        props['fallback_reason'] = str(first_error)
        noun = MODE_NETWORK_NOUN.get(mode, 'routable')
        props['fallback_note'] = (
            f"No {noun} route was available, so this follows regular streets. "
            "Check the highlighted stretches before you go."
        )
        return feature


# ------------------------------------------------------------------ transit

_TRANSIT: dict[str, Any] = {'epoch': 0.0, 'stops': None, 'shapes': None}
_TRANSIT_LOCK = threading.Lock()
_TRANSIT_TTL_SECONDS = 24 * 60 * 60


def _get_transit_data() -> dict[str, Any]:
    """Greenlink stops + route shapes, cached in-process.

    stops: list of {name, lat, lon, routes: set of short names}
    shapes: list of {route, long_name, color, coords: [[lon,lat],...],
                     cumulative: [m from start]}
    """
    with _TRANSIT_LOCK:
        if (
            _TRANSIT['stops'] is not None
            and (time.time() - _TRANSIT['epoch']) < _TRANSIT_TTL_SECONDS
        ):
            return _TRANSIT
        import json

        conn = mrsm.get_connector('sql:bwg')
        stops = []
        df = conn.read('SELECT "name", "lat", "lon", "routes" FROM "transit"."stops"')
        for rec in df.to_dict(orient='records'):
            routes = {
                r.strip()
                for r in str(rec.get('routes') or '').split(',')
                if r.strip()
            }
            if not routes:
                continue
            stops.append({
                'name': rec['name'],
                'lat': float(rec['lat']),
                'lon': float(rec['lon']),
                'routes': routes,
            })

        shapes = []
        df = conn.read('''
            SELECT "short_name", "long_name", "color",
                   ST_AsGeoJSON("geometry", 5) AS "gj"
            FROM "transit"."route_shapes"
        ''')
        for rec in df.to_dict(orient='records'):
            gj = rec.get('gj')
            if not gj:
                continue
            coords = json.loads(gj).get('coordinates') or []
            if len(coords) < 2:
                continue
            cumulative = [0.0]
            for a, b in zip(coords, coords[1:]):
                cumulative.append(
                    cumulative[-1] + _equirect_m(a[1], a[0], b[1], b[0])
                )
            shapes.append({
                'route': str(rec.get('short_name') or ''),
                'long_name': str(rec.get('long_name') or ''),
                'color': rec.get('color'),
                'coords': coords,
                'cumulative': cumulative,
            })
        _TRANSIT.update({'epoch': time.time(), 'stops': stops, 'shapes': shapes})
        return _TRANSIT


def _project_on_shape(shape: dict, lat: float, lon: float) -> tuple[float, float, int, float]:
    """(meters along shape, offset meters, segment index, t) of the closest
    point on the shape to (lat, lon)."""
    coords = shape['coords']
    best = (0.0, float('inf'), 0, 0.0)
    for i in range(len(coords) - 1):
        ax, ay = coords[i][0] * _COSLAT, coords[i][1]
        bx, by = coords[i + 1][0] * _COSLAT, coords[i + 1][1]
        px, py = lon * _COSLAT, lat
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        t = 0.0 if denom == 0 else ((px - ax) * dx + (py - ay) * dy) / denom
        t = max(0.0, min(1.0, t))
        cx, cy = ax + dx * t, ay + dy * t
        off = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 * M_PER_DEG_LAT
        if off < best[1]:
            seg_len = shape['cumulative'][i + 1] - shape['cumulative'][i]
            best = (shape['cumulative'][i] + seg_len * t, off, i, t)
    return best


def _shape_slice(shape: dict, a: tuple, b: tuple) -> list[list]:
    """Shape coords between projections a and b (from _project_on_shape)."""
    coords = shape['coords']

    def _point(proj):
        _dist, _off, i, t = proj
        ax, ay = coords[i]
        bx, by = coords[i + 1]
        return [ax + (bx - ax) * t, ay + (by - ay) * t]

    out = [_point(a)]
    out.extend(coords[a[2] + 1:b[2] + 1])
    out.append(_point(b))
    return [c for i, c in enumerate(out) if i == 0 or c != out[i - 1]]


#: Verb used in the synthetic "just head that way" step, per access mode.
_ACCESS_VERB = {'bike': 'Ride', 'walk': 'Walk', 'roll': 'Roll', 'street': 'Head'}


def _walk_or_direct(
    from_lat, from_lon, to_lat, to_lon,
    toward: str = 'your stop',
    mode: str = 'walk',
) -> dict[str, Any]:
    """Access/egress leg for a multi-leg trip (transit, bike share), falling
    back to a straight line when the leg is too short or too far off-network to
    route. `mode` is the rider's own mode for that leg."""
    try:
        return _route(from_lat, from_lon, to_lat, to_lon, mode=mode)
    except ValueError:
        coords = [[from_lon, from_lat], [to_lon, to_lat]]
        dist = _equirect_m(from_lat, from_lon, to_lat, to_lon)
        speed = MODE_SPEED_M_S.get(mode, MODE_SPEED_M_S['walk'])
        verb = _ACCESS_VERB.get(_base_mode(mode), 'Head')
        return {
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': coords},
            'properties': {
                'distance_m': round(dist, 1),
                'duration_min': round(dist / speed / 60, 1),
                'warn_ranges': [],
                'warnings': [],
                'steps': [{
                    'maneuver': 'depart',
                    'name': None,
                    'category': None,
                    'instruction': f'{verb} toward {toward}',
                    'distance_m': round(dist, 1),
                    'duration_min': round(dist / speed / 60, 2),
                    'start_index': 0,
                    'location': coords[0],
                    'bearing': 0.0,
                    'warn': None,
                    'warn_m': 0.0,
                }],
            },
        }


def _route_transit(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    access_mode: str = 'walk',
) -> dict[str, Any]:
    """Access leg -> ride a Greenlink route -> egress leg. No schedules yet
    (stop_times isn't ingested), so the wait is a flat estimate and the ride
    follows the route shape between the boarding and alighting stops.

    `access_mode` is how the rider gets to and from the stops. Greenlink buses
    carry front-load racks, so `bike` both widens the catchment around a stop
    (TRANSIT_BIKE_MAX_M) and adds a "load your bike" cue at boarding.
    """
    data = _get_transit_data()
    stops, shapes = data['stops'], data['shapes']
    if not stops or not shapes:
        raise ValueError("Transit network is unavailable.")
    access_max_m = (
        TRANSIT_BIKE_MAX_M if _base_mode(access_mode) == 'bike'
        else TRANSIT_WALK_MAX_M
    )
    access_speed = MODE_SPEED_M_S.get(access_mode, MODE_SPEED_M_S['walk'])

    def _near(lat, lon):
        """Candidate stops within reach — the nearest few PER ROUTE, not
        overall (downtown the 25 nearest stops are all trolley stops and the
        transit center never makes the cut)."""
        ranked = []
        for s in stops:
            d = _equirect_m(lat, lon, s['lat'], s['lon'])
            if d <= access_max_m:
                ranked.append((d, s))
        ranked.sort(key=lambda x: x[0])
        out = []
        per_route: dict[str, int] = {}
        for d, s in ranked:
            if any(per_route.get(r, 0) < 3 for r in s['routes']):
                out.append((d, s))
                for r in s['routes']:
                    per_route[r] = per_route.get(r, 0) + 1
        return out

    near_from = _near(from_lat, from_lon)
    near_to = _near(to_lat, to_lon)
    if not near_from or not near_to:
        reach = 'biking' if _base_mode(access_mode) == 'bike' else 'walking'
        raise ValueError(f"No bus stops within {reach} distance.")

    shapes_by_route: dict[str, list] = {}
    for sh in shapes:
        shapes_by_route.setdefault(sh['route'], []).append(sh)

    proj_cache: dict = {}

    def _proj(shape_i, s):
        key = (shape_i, id(s))
        if key not in proj_cache:
            proj_cache[key] = _project_on_shape(shapes[shape_i], s['lat'], s['lon'])
        return proj_cache[key]

    shape_index = {id(sh): i for i, sh in enumerate(shapes)}
    walk_speed = access_speed
    best = None  # (total_s, d1, s1, d2, s2, shape, p1, p2)
    for d1, s1 in near_from:
        for d2, s2 in near_to:
            shared = s1['routes'] & s2['routes']
            if not shared or s1 is s2:
                continue
            for route in shared:
                for sh in shapes_by_route.get(route, []):
                    i = shape_index[id(sh)]
                    p1 = _proj(i, s1)
                    p2 = _proj(i, s2)
                    if p1[1] > TRANSIT_STOP_SNAP_M or p2[1] > TRANSIT_STOP_SNAP_M:
                        continue
                    ride_m = p2[0] - p1[0]
                    if ride_m < TRANSIT_MIN_RIDE_M:
                        continue
                    total_s = (
                        (d1 + d2) / walk_speed
                        + ride_m / TRANSIT_BUS_SPEED_M_S
                        + TRANSIT_WAIT_MIN * 60
                    )
                    if best is None or total_s < best[0]:
                        best = (total_s, d1, s1, d2, s2, sh, p1, p2)

    if best is None:
        raise ValueError(
            "No direct bus route between those points — try walk or bike."
        )
    _total_s, _d1, s1, _d2, s2, shape, p1, p2 = best

    walk1 = _walk_or_direct(
        from_lat, from_lon, s1['lat'], s1['lon'], mode=access_mode,
    )
    walk2 = _walk_or_direct(
        s2['lat'], s2['lon'], to_lat, to_lon,
        toward='your destination', mode=access_mode,
    )
    ride_coords = _shape_slice(shape, p1, p2)
    ride_m = p2[0] - p1[0]

    coordinates: list = []
    steps: list[dict] = []
    ranges: list[dict] = []

    def _append_leg(feature, drop_arrive: bool):
        offset = max(len(coordinates) - 1, 0)
        coords = feature['geometry']['coordinates']
        if coordinates and coordinates[-1] == coords[0]:
            coords = coords[1:]
        else:
            offset = len(coordinates)
        coordinates.extend(coords)
        for step in feature['properties']['steps']:
            if drop_arrive and step['maneuver'] == 'arrive':
                continue
            step = dict(step)
            step['start_index'] = step['start_index'] + offset
            steps.append(step)
        # Access legs carry their own missing-sidewalk stretches; shift them
        # into the combined line's coordinate space.
        for r in feature['properties'].get('warn_ranges') or []:
            ranges.append({
                **r,
                'start': r['start'] + offset,
                'end': r['end'] + offset,
            })

    _append_leg(walk1, drop_arrive=True)

    route_name = f"Route {shape['route']}".strip()
    long_name = _titleize(shape['long_name'])
    board_index = max(len(coordinates) - 1, 0)
    steps.append({
        'maneuver': 'board',
        'name': route_name,
        'category': 'transit',
        'instruction': (
            f"Board Greenlink {route_name}"
            + (f' ({long_name})' if long_name else '')
            + f" at {s1['name']}"
            + (' — load your bike on the front rack'
               if _base_mode(access_mode) == 'bike' else '')
        ),
        'distance_m': 0.0,
        'duration_min': round(TRANSIT_WAIT_MIN, 1),
        'start_index': board_index,
        'location': [s1['lon'], s1['lat']],
        'bearing': 0.0,
        'warn': None,
        'warn_m': 0.0,
    })
    ride_start_index = len(coordinates) - 1 if coordinates else 0
    if coordinates and coordinates[-1] == ride_coords[0]:
        coordinates.extend(ride_coords[1:])
    else:
        ride_start_index = len(coordinates)
        coordinates.extend(ride_coords)
    steps.append({
        'maneuver': 'ride',
        'name': route_name,
        'category': 'transit',
        'instruction': f"Ride {route_name} to {s2['name']}",
        'distance_m': round(ride_m, 1),
        'duration_min': round(ride_m / TRANSIT_BUS_SPEED_M_S / 60, 1),
        'start_index': ride_start_index,
        'location': [s1['lon'], s1['lat']],
        'bearing': 0.0,
        'warn': None,
        'warn_m': 0.0,
    })
    steps.append({
        'maneuver': 'alight',
        'name': s2['name'],
        'category': 'transit',
        'instruction': f"Get off at {s2['name']}",
        'distance_m': 0.0,
        'duration_min': 0.0,
        'start_index': max(len(coordinates) - 1, 0),
        'location': [s2['lon'], s2['lat']],
        'bearing': 0.0,
        'warn': None,
        'warn_m': 0.0,
    })
    _append_leg(walk2, drop_arrive=False)
    if not steps or steps[-1]['maneuver'] != 'arrive':
        steps.append({
            'maneuver': 'arrive',
            'name': None,
            'category': None,
            'instruction': 'Arrive at your destination',
            'distance_m': 0.0,
            'duration_min': 0.0,
            'start_index': max(len(coordinates) - 1, 0),
            'location': list(coordinates[-1]),
            'warn': None,
            'warn_m': 0.0,
        })

    walk_m = (
        walk1['properties']['distance_m'] + walk2['properties']['distance_m']
    )
    duration_min = (
        walk1['properties']['duration_min']
        + walk2['properties']['duration_min']
        + ride_m / TRANSIT_BUS_SPEED_M_S / 60
        + TRANSIT_WAIT_MIN
    )
    distance_m = walk_m + ride_m
    return {
        'type': 'Feature',
        'geometry': {'type': 'LineString', 'coordinates': coordinates},
        'properties': {
            'mode': 'transit',
            'access_mode': _base_mode(access_mode),
            'distance_m': round(distance_m, 1),
            'distance_mi': round(distance_m / 1609.344, 2),
            'duration_min': round(duration_min, 1),
            # The bus does its own climbing; this is what the rider does.
            'climb_ft': (
                walk1['properties'].get('climb_ft', 0)
                + walk2['properties'].get('climb_ft', 0)
            ),
            'walk_m': round(walk_m, 1),
            'ride_m': round(ride_m, 1),
            'route': shape['route'],
            'route_long_name': shape['long_name'],
            'route_color': shape.get('color'),
            'board_stop': s1['name'],
            'alight_stop': s2['name'],
            'wait_min': TRANSIT_WAIT_MIN,
            'warn_ranges': ranges,
            'warnings': _summarize_warnings(ranges),
            'steps': steps,
        },
    }


# ---------------------------------------------------------------- bike share


def _bcycle_stations() -> list[dict[str, Any]]:
    """Greenville BCycle docks with live availability, via `plugins/bcycle.py`.
    Returns [] if the plugin or the GBFS feed is unavailable -- bike share is
    an option, never a dependency."""
    try:
        module = mrsm.Plugin('bcycle').module
        return module.get_stations()
    except Exception as e:
        warn(f"BCycle stations unavailable: {e}")
        return []


def _route_bikeshare(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    foot_mode: str = 'walk',
    bike_mode: str = 'bike',
) -> dict[str, Any]:
    """Walk to a BCycle dock -> ride a share bike -> dock it -> walk on.

    Same shape as the transit plan, with docks instead of stops. Stations with
    no bikes (or that aren't renting) are skipped, so the plan reflects what's
    actually available right now rather than where the kiosks are.

    `bike_mode` carries the rider's stress tolerance onto the rented leg -- but
    never their e-bike setting. You cannot count on the dock handing you an
    electric one, so the ride is costed at ordinary bike pace.
    """
    stations = _bcycle_stations()
    if not stations:
        raise ValueError("Bike share availability is unavailable right now.")

    def _reachable(lat, lon, want: str):
        out = []
        for st in stations:
            # A count of None means station_status didn't load; don't refuse
            # the plan over missing telemetry, only over a known-empty dock.
            if want == 'bikes':
                if st.get('is_renting') is False:
                    continue
                bikes = st.get('bikes')
                if bikes is not None and bikes < 1:
                    continue
            else:
                if st.get('is_returning') is False:
                    continue
                docks = st.get('docks')
                if docks is not None and docks < 1:
                    continue
            d = _equirect_m(lat, lon, st['lat'], st['lon'])
            if d <= BIKESHARE_WALK_MAX_M:
                out.append((d, st))
        out.sort(key=lambda x: x[0])
        return out[:4]

    from_docks = _reachable(from_lat, from_lon, 'bikes')
    to_docks = _reachable(to_lat, to_lon, 'docks')
    if not from_docks:
        raise ValueError("No BCycle bikes available within walking distance.")
    if not to_docks:
        raise ValueError("No open BCycle dock near your destination.")

    foot_speed = MODE_SPEED_M_S.get(foot_mode, MODE_SPEED_M_S['walk'])
    best = None
    for d1, rent in from_docks:
        for d2, dock in to_docks:
            if rent['id'] == dock['id']:
                continue
            ride_m = _equirect_m(rent['lat'], rent['lon'], dock['lat'], dock['lon'])
            if ride_m < BIKESHARE_MIN_RIDE_M:
                continue
            total_s = (
                (d1 + d2) / foot_speed
                + ride_m / MODE_SPEED_M_S['bike']
                + BIKESHARE_UNLOCK_MIN * 60
            )
            if best is None or total_s < best[0]:
                best = (total_s, rent, dock)
    if best is None:
        raise ValueError(
            "You're closer to your destination than to a BCycle dock — "
            "try walking or your own bike."
        )
    _total_s, rent, dock = best

    leg1 = _walk_or_direct(
        from_lat, from_lon, rent['lat'], rent['lon'],
        toward='the BCycle dock', mode=foot_mode,
    )
    ride = _route(
        rent['lat'], rent['lon'], dock['lat'], dock['lon'], mode=bike_mode,
    )
    leg3 = _walk_or_direct(
        dock['lat'], dock['lon'], to_lat, to_lon,
        toward='your destination', mode=foot_mode,
    )

    coordinates: list = []
    steps: list[dict] = []
    ranges: list[dict] = []

    def _append(feature, drop_arrive: bool, drop_depart: bool = False):
        offset = max(len(coordinates) - 1, 0)
        coords = feature['geometry']['coordinates']
        if coordinates and coordinates[-1] == coords[0]:
            coords = coords[1:]
        else:
            offset = len(coordinates)
        coordinates.extend(coords)
        for step in feature['properties']['steps']:
            if drop_arrive and step['maneuver'] == 'arrive':
                continue
            if drop_depart and step['maneuver'] == 'depart':
                drop_depart = False
                continue
            step = dict(step)
            step['start_index'] = step['start_index'] + offset
            steps.append(step)
        for r in feature['properties'].get('warn_ranges') or []:
            ranges.append({
                **r, 'start': r['start'] + offset, 'end': r['end'] + offset,
            })

    _append(leg1, drop_arrive=True)
    bikes = rent.get('bikes')
    steps.append({
        'maneuver': 'rent',
        'name': rent['name'],
        'category': 'bikeshare',
        'instruction': (
            f"Unlock a BCycle at {rent['name']}"
            + (
                f" ({bikes} available)"
                if bikes is not None else ''
            )
        ),
        'distance_m': 0.0,
        'duration_min': round(BIKESHARE_UNLOCK_MIN, 1),
        'start_index': max(len(coordinates) - 1, 0),
        'location': [rent['lon'], rent['lat']],
        'bearing': 0.0,
        'warn': None,
        'warn_m': 0.0,
    })
    _append(ride, drop_arrive=True)
    steps.append({
        'maneuver': 'dock',
        'name': dock['name'],
        'category': 'bikeshare',
        'instruction': f"Dock the bike at {dock['name']}",
        'distance_m': 0.0,
        'duration_min': 1.0,
        'start_index': max(len(coordinates) - 1, 0),
        'location': [dock['lon'], dock['lat']],
        'bearing': 0.0,
        'warn': None,
        'warn_m': 0.0,
    })
    _append(leg3, drop_arrive=False, drop_depart=True)
    if not steps or steps[-1]['maneuver'] != 'arrive':
        steps.append({
            'maneuver': 'arrive',
            'name': None,
            'category': None,
            'instruction': 'Arrive at your destination',
            'distance_m': 0.0,
            'duration_min': 0.0,
            'start_index': max(len(coordinates) - 1, 0),
            'location': list(coordinates[-1]),
            'warn': None,
            'warn_m': 0.0,
        })

    foot_m = leg1['properties']['distance_m'] + leg3['properties']['distance_m']
    ride_m = ride['properties']['distance_m']
    duration_min = (
        leg1['properties']['duration_min']
        + leg3['properties']['duration_min']
        + ride['properties']['duration_min']
        + BIKESHARE_UNLOCK_MIN
        + 1.0  # docking
    )
    distance_m = foot_m + ride_m
    return {
        'type': 'Feature',
        'geometry': {'type': 'LineString', 'coordinates': coordinates},
        'properties': {
            'mode': 'bcycle',
            'access_mode': _base_mode(foot_mode),
            'distance_m': round(distance_m, 1),
            'distance_mi': round(distance_m / 1609.344, 2),
            'duration_min': round(duration_min, 1),
            'climb_ft': (
                leg1['properties'].get('climb_ft', 0)
                + ride['properties'].get('climb_ft', 0)
                + leg3['properties'].get('climb_ft', 0)
            ),
            'walk_m': round(foot_m, 1),
            'ride_m': round(ride_m, 1),
            'rent_station': rent['name'],
            'rent_station_bikes': rent.get('bikes'),
            'rent_station_uri': rent.get('rental_uri'),
            'dock_station': dock['name'],
            'dock_station_docks': dock.get('docks'),
            'dock_station_uri': dock.get('rental_uri'),
            'warn_ranges': ranges,
            'warnings': _summarize_warnings(ranges),
            'steps': steps,
        },
    }


# ------------------------------------------------------------- multi-modal


#: plan key -> (human label, the mode whose icon/color the app should use).
PLAN_LABELS = {
    'bike': ('Bike', 'bike'),
    'walk': ('Walk', 'walk'),
    'roll': ('Roll', 'roll'),
    'bcycle': ('BCycle', 'bcycle'),
    'walk-transit': ('Walk + bus', 'transit'),
    'roll-transit': ('Roll + bus', 'transit'),
    'bike-transit': ('Bike + bus', 'transit'),
}
#: A trip this short isn't worth waiting for a bus, whatever the math says.
TRANSIT_MIN_TRIP_M = 1200.0

#: Recently computed plans, so flipping between them in the app is instant.
_ROUTE_CACHE: dict[tuple, tuple[float, dict]] = {}
_ROUTE_CACHE_TTL_SECONDS = 120
_ROUTE_CACHE_MAX = 256


def _plan_keys(modes: set[str], roll: bool, bcycle: bool) -> list[str]:
    """Which itineraries the selected modes make possible.

    Transit access is by bike whenever the rider said they have a bike (racks
    on every Greenlink bus), which is both faster and a wider catchment than
    walking to the stop -- so `bike + transit` supersedes `walk + transit`
    rather than doubling the work.
    """
    foot = 'roll' if roll else 'walk'
    plans: list[str] = []
    if 'bike' in modes:
        plans.append('bike')
        if bcycle:
            plans.append('bcycle')
    if 'walk' in modes:
        plans.append(foot)
    if 'transit' in modes:
        plans.append('bike-transit' if 'bike' in modes else f'{foot}-transit')
    return plans or [foot]


def _compute_plan(
    plan: str,
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    bike_mode: str = 'bike',
    alt: int = 0,
) -> dict[str, Any]:
    """One itinerary, memoized briefly. Raises ValueError when impossible.

    `bike_mode` is the composite profile (`bike:quiet`, `ebike:direct`, ...)
    that every pedalling leg of this plan is costed with. The plan KEY stays
    plain (`bike`, `bike-transit`) because it names the itinerary, not the
    rider's preferences -- but the profile joins the cache key, or two riders
    with different tolerances would share a route.

    `alt=N` asks for the Nth alternate of a plain (bike/walk/roll) plan:
    each pass penalizes the edges every previous pass used, so a genuinely
    different way wins when one exists. `alt_distinct` in the response says
    whether it actually differs from the base route. Composite plans
    (transit, bike share) ignore `alt` — their shape is fixed by the stop
    and dock locations, not the street choice.
    """
    if plan.endswith('-transit') or plan == 'bcycle':
        alt = 0
    key = (
        plan, bike_mode, alt, _is_night(), _TRAIL_PREF.get(),
        round(from_lat, 5), round(from_lon, 5),
        round(to_lat, 5), round(to_lon, 5),
    )
    hit = _ROUTE_CACHE.get(key)
    if hit is not None and (time.time() - hit[0]) < _ROUTE_CACHE_TTL_SECONDS:
        return hit[1]

    if plan.endswith('-transit'):
        access = plan.split('-', 1)[0]
        if access == 'bike':
            access = bike_mode
        straight = _equirect_m(from_lat, from_lon, to_lat, to_lon)
        if straight < TRANSIT_MIN_TRIP_M:
            raise ValueError("That trip is too short to be worth the bus.")
        feature = _route_transit(
            from_lat, from_lon, to_lat, to_lon, access_mode=access,
        )
    elif plan == 'bcycle':
        feature = _route_bikeshare(
            from_lat, from_lon, to_lat, to_lon,
            # A rental is not the rider's own e-bike, but it IS ridden on the
            # streets they said they would accept.
            bike_mode=_bike_mode(False, _stress_level(bike_mode)),
        )
    else:
        route_mode = bike_mode if plan == 'bike' else plan
        if alt <= 0:
            feature = _route(from_lat, from_lon, to_lat, to_lon, mode=route_mode)
        else:
            avoid: set = set()
            base_coords = None
            for i in range(alt + 1):
                pairs: set = set()
                feature = _route(
                    from_lat, from_lon, to_lat, to_lon, mode=route_mode,
                    avoid_pairs=avoid or None, pairs_out=pairs,
                )
                if i == 0:
                    base_coords = feature['geometry']['coordinates']
                avoid |= pairs
            feature['properties']['alt'] = alt
            feature['properties']['alt_distinct'] = (
                feature['geometry']['coordinates'] != base_coords
            )

    label, icon_mode = PLAN_LABELS.get(plan, (plan.title(), plan))
    feature['properties']['plan'] = plan
    feature['properties']['plan_label'] = label
    feature['properties']['icon_mode'] = icon_mode
    if len(_ROUTE_CACHE) > _ROUTE_CACHE_MAX:
        _ROUTE_CACHE.clear()
    _ROUTE_CACHE[key] = (time.time(), feature)
    return feature


def _route_multimodal(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    modes: set[str],
    roll: bool = False,
    bcycle: bool = False,
    plan: str | None = None,
    ebike: bool = False,
    stress: str = DEFAULT_STRESS,
    alt: int = 0,
) -> dict[str, Any]:
    """Pick the best itinerary across everything the rider is willing to use.

    Every viable plan is computed (each is well under a second) and the
    fastest wins, unless the app asked for a specific `plan`. The rest ride
    along in `properties.alternatives` with real numbers, so switching is a
    labelled choice rather than a guess.

    `ebike` and `stress` describe the rider's own bike and how much traffic
    they will put up with; they apply to every pedalling leg, including the
    ride to the bus stop.

    `alt=N` (with a pinned `plan`) swaps in that plan's Nth alternate route;
    the other plans in `alternatives` stay their base selves.
    """
    bike_mode = _bike_mode(ebike, stress)
    keys = _plan_keys(modes, roll, bcycle)
    if plan and plan not in keys:
        keys = keys + [plan]

    computed: dict[str, dict] = {}
    failures: list[dict[str, str]] = []
    for key in keys:
        try:
            computed[key] = _compute_plan(
                key, from_lat, from_lon, to_lat, to_lon,
                bike_mode=bike_mode,
                alt=(alt if key == plan else 0),
            )
        except ValueError as e:
            failures.append({'plan': key, 'reason': str(e)})
        except Exception as e:
            warn(f"Plan {key} failed: {e}")
            failures.append({'plan': key, 'reason': 'Routing failed.'})

    if not computed:
        # Lead with a reason that isn't "the bus doesn't go there".
        reason = next(
            (f['reason'] for f in failures if not f['plan'].endswith('-transit')),
            failures[0]['reason'] if failures else 'No route found.',
        )
        raise ValueError(reason)

    def _duration(k: str) -> float:
        return computed[k]['properties']['duration_min']

    fastest_key = min(computed, key=_duration)
    chosen_key = fastest_key
    if plan in computed:
        chosen_key = plan
    elif len(modes) > 1:
        # Selecting several modes is a request to USE them together: a rider
        # who picked bike + bus wants the bike-to-the-bus itinerary, not to be
        # told the bike alone is faster (that stays one tap away in the
        # alternatives). Only fall back to the fastest single-mode plan when
        # the combined trip is a genuinely bad deal.
        combined = [k for k in computed if k.endswith('-transit')]
        if combined:
            best_combined = min(combined, key=_duration)
            if _duration(best_combined) <= _duration(fastest_key) * MULTIMODAL_MAX_SLOWDOWN:
                chosen_key = best_combined
    # The plans are cached and shared; annotate a copy so a later request
    # doesn't inherit this one's alternatives list.
    feature = dict(computed[chosen_key])
    props = dict(feature['properties'])
    feature['properties'] = props
    props['alternatives'] = [
        {
            'plan': k,
            'label': PLAN_LABELS.get(k, (k.title(), k))[0],
            'icon_mode': PLAN_LABELS.get(k, (k.title(), k))[1],
            'distance_m': v['properties']['distance_m'],
            'duration_min': v['properties']['duration_min'],
            'warnings': v['properties'].get('warnings') or [],
        }
        for k, v in computed.items()
        if k != chosen_key
    ]
    props['alternatives'].sort(key=lambda a: a['duration_min'])
    props['unavailable'] = failures
    return feature


# User-submitted missing points (bike parking, repair stations, fountains...).
# Deliberately anonymous — no usernames until the login question is settled;
# rows are moderated by hand before any OSM upstreaming.
SUBMISSION_PIPE: mrsm.Pipe = mrsm.Pipe(
    'app', 'point_submissions', 'MapLayers',
    instance='sql:bwg',
    parameters={
        'autotime': True,
        'schema': 'MapLayers',
        'target': 'point_submissions',
        'columns': {
            'datetime': 'ts',
            'id': 'id',
        },
        'dtypes': {
            'ts': 'datetime',
            'id': 'string',
            'category': 'string',
            'name': 'string',
            'comment': 'string',
            'lat': 'float',
            'lon': 'float',
            'photo_filename': 'string',
            'ip': 'string',
            'user_agent': 'string',
        },
    },
)

#: Allowed categories for point submissions (server-side guard: the endpoint
#: is public, so free-text categories invite junk).
SUBMISSION_CATEGORIES = (
    'bike-parking',
    'repair-station',
    'water-fountain',
    'bike-business',
    'other',
)

#: Cheap abuse guards for the anonymous submission endpoint: an in-process
#: per-IP sliding window and a hard cap on the streamed photo. Not real
#: DoS protection (that's the reverse proxy's job) but enough that one script
#: kiddie can't fill the disk or the table overnight.
SUBMIT_MAX_PER_HOUR = 10
SUBMIT_MAX_PHOTO_BYTES = 8 * 1024 * 1024
_SUBMIT_HITS: dict[str, list[float]] = {}
_SUBMIT_HITS_LOCK = threading.Lock()


def _submit_rate_limited(ip: str | None) -> bool:
    """True when this client has used up its hourly submissions."""
    key = ip or 'unknown'
    now = time.time()
    with _SUBMIT_HITS_LOCK:
        hits = [t for t in _SUBMIT_HITS.get(key, []) if now - t < 3600]
        if len(hits) >= SUBMIT_MAX_PER_HOUR:
            _SUBMIT_HITS[key] = hits
            return True
        hits.append(now)
        _SUBMIT_HITS[key] = hits
    return False

# User feedback on any map feature (bus stop, sidewalk segment, bike lane, ...).
FEEDBACK_PIPE: mrsm.Pipe = mrsm.Pipe(
    'app', 'feedback', 'MapLayers',
    instance='sql:bwg',
    parameters={
        'autotime': True,
        'schema': 'MapLayers',
        'target': 'layer_feedback',
        'columns': {
            'datetime': 'ts',
            'id': 'id',
        },
        'dtypes': {
            'ts': 'datetime',
            'id': 'string',
            'layer': 'string',
            'name': 'string',
            'lat': 'float',
            'lon': 'float',
            'props': 'string',
            'feedback': 'string',
            'photo_filename': 'string',
            'ip': 'string',
            'user_agent': 'string',
        },
    },
)


def _photos_dir():
    """Directory where uploaded photos are stored (created on demand)."""
    from pathlib import Path
    from meerschaum.config.paths import ROOT_DIR_PATH
    photos_dir = Path(ROOT_DIR_PATH) / 'uploads' / 'map-layers'
    photos_dir.mkdir(parents=True, exist_ok=True)
    return photos_dir


def _layer_index() -> list[dict[str, Any]]:
    return [
        {
            'id': layer_id,
            'label': layer['label'],
            'kind': layer['kind'],
            'url': f'/map-layers/{layer_id}.geojson',
            'props': list(layer.get('props', {})),
            **{
                key: layer[key]
                for key in ('color', 'color_by', 'icon')
                if key in layer
            },
        }
        for layer_id, layer in LAYERS.items()
    ]


@make_action
def export_map_layers(debug: bool = False, **kwargs) -> mrsm.SuccessTuple:
    """Run `mrsm export map_layers` to pregenerate the app layer files."""
    import json

    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    num_successes = 0
    for layer_id in LAYERS:
        info(f"Exporting layer '{layer_id}'...")
        try:
            json_str = _build_layer_geojson(layer_id, debug=debug)
        except Exception as e:
            return False, f"Failed to export layer '{layer_id}':\n{e}"
        if json_str is None:
            continue

        layer_path = output_dir / f'{layer_id}.geojson'
        with open(layer_path, 'w+') as f:
            f.write(json_str)
        mrsm.pprint((True, f"Wrote file '{layer_path}'."))
        num_successes += 1

    with open(output_dir / 'index.json', 'w+') as f:
        json.dump({'layers': _layer_index()}, f)

    if num_successes == 0:
        return False, "Did not export any layers."

    return True, f"Successfully exported {num_successes} of {len(LAYERS)} layers."


@api_plugin
def init_app(app):
    """Register the map-layers HTTP routes on the Meerschaum API app."""
    import uuid
    import shutil
    from pathlib import Path
    from fastapi import Form, File, UploadFile, Request, Query
    from fastapi.responses import JSONResponse, FileResponse, Response
    from starlette.middleware.gzip import GZipMiddleware

    # App-wide (all bwg.mrsm.io routes): geojson payloads gzip ~8-10x.
    if not any(m.cls is GZipMiddleware for m in app.user_middleware):
        app.add_middleware(GZipMiddleware, minimum_size=2048)

    # Warm the routing graph, its lookup indexes and the transit data in the
    # background so the first route request doesn't pay the ~6 s build.
    def _warm_routing():
        try:
            graph = _get_route_graph()
            _nearest_index(graph)
            _edge_index(graph)
            _get_transit_data()
        except Exception as e:
            warn(f"Routing warm-up failed: {e}")
    threading.Thread(target=_warm_routing, daemon=True).start()

    @app.get('/map-layers/index.json')
    def map_layers_index():
        return JSONResponse({'layers': _layer_index()})

    #: Live-ish garage occupancy cache (the pipe is synced by the parking job;
    #: this only avoids hammering Postgres from every map pan).
    _garages_cache: dict[str, Any] = {'epoch': 0.0, 'body': None}

    # NOTE: registered BEFORE the parameterized `/{layer_id}.geojson` route --
    # Starlette matches in declaration order, so the other way around this
    # endpoint is unreachable (the layer route 404s 'parking-garages').
    @app.get('/map-layers/parking-garages.geojson')
    def map_layers_parking_garages():
        """Downtown parking garages with the latest occupancy counts, from the
        cached `Parking.garages_counts_map` pipe (one row per garage)."""
        import json
        if (
            _garages_cache['body'] is not None
            and time.time() - _garages_cache['epoch'] < 60
        ):
            return Response(
                _garages_cache['body'],
                media_type='application/geo+json',
                headers={'Cache-Control': 'no-store'},
            )

        def _num(value):
            """float | None, treating pandas NaN as missing -- one bad garage
            row must not 500 the layer."""
            if value is None:
                return None
            try:
                f = float(value)
            except Exception:
                return None
            return None if f != f else f

        query = '''
        SELECT
            "label" AS "name",
            "capacity",
            "Spaces Occupied" AS "occupied",
            ROUND(("Percent Occupied" * 100)::numeric, 0) AS "percent_occupied",
            "ts",
            "longitude",
            "latitude"
        FROM "Parking".garages_counts_map
        '''
        try:
            conn = mrsm.get_connector('sql:bwg')
            df = conn.read(query)
        except Exception as e:
            return JSONResponse({'error': str(e)}, status_code=500)
        features = []
        for row in df.to_dict(orient='records'):
            lon = _num(row.pop('longitude', None))
            lat = _num(row.pop('latitude', None))
            if lon is None or lat is None:
                continue
            capacity = _num(row.get('capacity'))
            occupied = _num(row.get('occupied'))
            percent = _num(row.get('percent_occupied'))
            # City counters glitch (Church St. Garage reported -535 occupied
            # -> "1484 of 949 spaces open"): a count outside [0, capacity]
            # is a broken sensor, not data — drop the occupancy fields and
            # let the pin say only what's true (name + capacity).
            if (
                occupied is not None
                and (occupied < 0 or (capacity and occupied > capacity))
            ):
                occupied = None
                percent = None
            name = row.get('name')
            props = {
                # One garage row carries NaN for a name — never serve it.
                'name': str(name) if name is not None and name == name else None,
                'capacity': int(capacity) if capacity is not None else None,
                'occupied': int(occupied) if occupied is not None else None,
                'percent_occupied': int(percent) if percent is not None else None,
                'as_of': str(row['ts']) if row.get('ts') is not None else None,
            }
            if props['capacity'] and props['occupied'] is not None:
                open_spaces = max(props['capacity'] - props['occupied'], 0)
                props['availability'] = (
                    f"{open_spaces} of {props['capacity']} spaces open"
                )
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                'properties': props,
            })
        body = json.dumps({'type': 'FeatureCollection', 'features': features})
        _garages_cache['epoch'] = time.time()
        _garages_cache['body'] = body
        return Response(
            body,
            media_type='application/geo+json',
            headers={'Cache-Control': 'no-store'},
        )

    @app.get('/map-layers/{layer_id}.geojson')
    def map_layer_geojson(
        layer_id: str,
        bbox: str = None,
        zoom: int = 13,
    ):
        if layer_id not in LAYERS:
            return JSONResponse({'error': f"Unknown layer '{layer_id}'."}, status_code=404)

        # Viewport query: ?bbox=minlon,minlat,maxlon,maxlat&zoom=14
        # (builder layers have no single source table — serve the full layer)
        if bbox and not LAYERS[layer_id].get('builder'):
            try:
                minlon, minlat, maxlon, maxlat = (float(v) for v in bbox.split(','))
            except Exception:
                return JSONResponse({'error': 'Invalid bbox.'}, status_code=400)
            try:
                json_str = _build_bbox_geojson(
                    layer_id, minlon, minlat, maxlon, maxlat, zoom,
                )
            except Exception as e:
                return JSONResponse({'error': str(e)}, status_code=500)
            return Response(json_str, media_type='application/geo+json')

        pregenerated = _output_dir() / f'{layer_id}.geojson'
        if pregenerated.exists():
            return FileResponse(pregenerated, media_type='application/geo+json')

        cached = _CACHE.get(layer_id)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            return Response(cached[1], media_type='application/geo+json')

        try:
            json_str = _build_layer_geojson(layer_id)
        except Exception as e:
            return JSONResponse({'error': str(e)}, status_code=500)
        if json_str is None:
            return JSONResponse({'error': f"No data for layer '{layer_id}'."}, status_code=404)

        _CACHE[layer_id] = (time.time(), json_str)
        return Response(json_str, media_type='application/geo+json')

    @app.post('/map-layers/submit-point')
    async def submit_point(
        request: Request,
        category: str = Form(''),
        name: str = Form(''),
        comment: str = Form(''),
        lat: float = Form(None),
        lon: float = Form(None),
        photo: UploadFile = File(None),
    ):
        """A missing point on the map (bike rack, repair station...), submitted
        by a rider. Anonymous by design; moderated before anything downstream."""
        category = (category or '').strip().lower()
        if category not in SUBMISSION_CATEGORIES:
            return JSONResponse(
                {'error': f"Expected category from {', '.join(SUBMISSION_CATEGORIES)}."},
                status_code=400,
            )
        if (
            lat is None or lon is None
            or not (math.isfinite(lat) and math.isfinite(lon))
            or not (-90.0 <= lat <= 90.0)
            or not (-180.0 <= lon <= 180.0)
        ):
            return JSONResponse({'error': 'A valid location is required.'}, status_code=400)
        if not (name or '').strip() and not (comment or '').strip():
            return JSONResponse(
                {'error': 'Give the spot a name or a short description.'},
                status_code=400,
            )
        client = request.client
        if _submit_rate_limited(client.host if client else None):
            return JSONResponse(
                {'error': 'Too many submissions — please try again later.'},
                status_code=429,
            )
        rec_id = uuid.uuid4().hex
        photo_filename = None
        if photo is not None and photo.filename:
            ext = Path(photo.filename).suffix or '.jpg'
            photo_filename = f'{rec_id}{ext}'
            photo_path = _photos_dir() / photo_filename
            written = 0
            with open(photo_path, 'wb') as out:
                while chunk := photo.file.read(256 * 1024):
                    written += len(chunk)
                    if written > SUBMIT_MAX_PHOTO_BYTES:
                        break
                    out.write(chunk)
            if written > SUBMIT_MAX_PHOTO_BYTES:
                photo_path.unlink(missing_ok=True)
                return JSONResponse(
                    {'error': 'Photo is too large (8 MB max).'},
                    status_code=413,
                )
        SUBMISSION_PIPE.sync(
            [{
                'id': rec_id,
                'category': category,
                'name': (name or '').strip()[:200] or None,
                'comment': (comment or '').strip()[:2000] or None,
                'lat': lat,
                'lon': lon,
                'photo_filename': photo_filename,
                'ip': client.host if client else None,
                'user_agent': request.headers.get('user-agent'),
            }],
            blocking=False,
        )
        return JSONResponse({'ok': True, 'id': rec_id})

    @app.get('/map-layers/route')
    def map_layers_route(
        to: str = '',
        from_: str = Query('', alias='from'),
        mode: str = 'bike',
        modes: str = '',
        roll: bool = False,
        bcycle: bool = False,
        plan: str = '',
        ebike: bool = False,
        stress: str = '',
        alt: int = 0,
        night: int = -1,
        trail: int = 1,
    ):
        """Multi-modal directions.

        `?modes=bike,walk,transit` is the current form: every itinerary those
        modes allow gets costed and the fastest is returned, the rest listed in
        `properties.alternatives`. `roll=1` swaps walking for wheelchair
        weighting; `bcycle=1` adds a bike-share itinerary. `plan=<key>` pins a
        specific alternative. The older single `?mode=` is still honored.

        `ebike=1` rides at e-bike pace and shrugs off hills; `stress=` is how
        much traffic the rider will accept (`quiet`, `balanced`, `direct`).
        Both default to today's behaviour, so an old client sees no change.

        `alt=N` (1–3, with `plan` pinned to a plain bike/walk/roll plan)
        returns that plan's Nth alternate route; `alt_distinct: false` in the
        response means no genuinely different way exists.

        After dark (Greenville local time), unlit street stretches cost
        extra (Duke streetlight coverage, NIGHT_UNLIT_FACTOR). `night=0/1`
        overrides the clock; the response reports the flag used.

        `trail=0` turns off the Swamp Rabbit Trail bias: the trail prices
        like a plain calm street instead of the heavily-discounted backbone.
        """
        # Sync def on purpose: FastAPI runs it in the threadpool, so the
        # multi-second first-call graph build never blocks the event loop.
        def _parse_latlon(value: str):
            lat_s, lon_s = value.split(',', 1)
            return float(lat_s), float(lon_s)

        raw = modes or mode or 'bike'
        selected = {m.strip().lower() for m in raw.split(',') if m.strip()}
        # `roll` arrives either as its own flag or as a mode name.
        if 'roll' in selected:
            selected.discard('roll')
            selected.add('walk')
            roll = True
        unknown = selected - {'bike', 'walk', 'transit'}
        if unknown or not selected:
            return JSONResponse(
                {'error': "Expected modes from bike, walk (roll), transit."},
                status_code=400,
            )
        level = (stress or DEFAULT_STRESS).strip().lower()
        if level not in STRESS_LEVELS:
            return JSONResponse(
                {'error': f"Expected stress from {', '.join(STRESS_LEVELS)}."},
                status_code=400,
            )
        try:
            from_lat, from_lon = _parse_latlon(from_)
            to_lat, to_lon = _parse_latlon(to)
        except Exception:
            return JSONResponse(
                {'error': "Expected ?from=lat,lon&to=lat,lon."},
                status_code=400,
            )
        if (from_lat, from_lon) == (to_lat, to_lon):
            return JSONResponse(
                {'error': "Start and destination are the same point."},
                status_code=400,
            )
        # Request-scoped: _astar and the plan cache read the contextvar, so
        # no signature threading through the seven plan/transit/bcycle layers.
        night_token = _NIGHT_OVERRIDE.set(
            None if night not in (0, 1) else bool(night)
        )
        trail_token = _TRAIL_PREF.set(bool(trail))
        try:
            feature = _route_multimodal(
                from_lat, from_lon, to_lat, to_lon,
                modes=selected,
                roll=roll,
                bcycle=bcycle,
                plan=(plan or '').strip().lower() or None,
                ebike=ebike,
                stress=level,
                alt=max(0, min(alt, ALT_MAX)),
            )
            feature['properties']['night'] = _is_night()
            feature['properties']['trail'] = bool(trail)
        except ValueError as e:
            return JSONResponse({'error': str(e)}, status_code=422)
        except Exception as e:
            warn(f"Routing failed: {e}")
            return JSONResponse({'error': 'Routing failed.'}, status_code=500)
        finally:
            _NIGHT_OVERRIDE.reset(night_token)
            _TRAIL_PREF.reset(trail_token)
        return JSONResponse(feature)

    def _srt_gaps(graph, srt_adj):
        """Min pairwise distance (m) between srt-only components."""
        seen: set = set()
        comps = []
        for start in srt_adj:
            if start in seen:
                continue
            comp = [start]
            seen.add(start)
            queue = [start]
            while queue:
                cur = queue.pop()
                for nbr in srt_adj.get(cur, []):
                    if nbr not in seen:
                        seen.add(nbr)
                        comp.append(nbr)
                        queue.append(nbr)
            comps.append(comp)
        comps.sort(key=len, reverse=True)
        gaps = []
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                best = None
                for u in comps[i]:
                    for v in comps[j]:
                        d = _equirect_m(*graph['nodes'][u], *graph['nodes'][v])
                        if best is None or d < best:
                            best = d
                gaps.append({
                    'a': i, 'b': j,
                    'a_size': len(comps[i]), 'b_size': len(comps[j]),
                    'gap_m': round(best, 1),
                })
        gaps.sort(key=lambda g: g['gap_m'])
        return gaps[:15]

    @app.get('/map-layers/route-stats.json')
    def map_layers_route_stats():
        graph = _get_route_graph()
        counts: dict = {}
        length_m: dict = {}
        for edges in graph['adj'].values():
            for e in edges:
                if e[5]:  # count each undirected edge once
                    continue
                cat = str(e[3] or 'unknown')
                counts[cat] = counts.get(cat, 0) + 1
                length_m[cat] = length_m.get(cat, 0.0) + e[2]
        # Continuity check: components of the srt-only subgraph.
        srt_adj: dict = {}
        for u, edges in graph['adj'].items():
            for e in edges:
                if e[3] == 'srt':
                    srt_adj.setdefault(u, []).append(e[0])
        seen: set = set()
        srt_comps = []
        for start in srt_adj:
            if start in seen:
                continue
            comp = [start]
            seen.add(start)
            queue = [start]
            while queue:
                cur = queue.pop()
                for nbr in srt_adj.get(cur, []):
                    if nbr not in seen:
                        seen.add(nbr)
                        comp.append(nbr)
                        queue.append(nbr)
            srt_comps.append(len(comp))
        return JSONResponse({
            'nodes': len(graph['nodes']),
            'edges': sum(counts.values()),
            'edges_by_category': counts,
            'km_by_category': {k: round(v / 1000, 1) for k, v in length_m.items()},
            'srt_components': sorted(srt_comps, reverse=True)[:20],
            'srt_component_count': len(srt_comps),
            'srt_gaps_m': _srt_gaps(graph, srt_adj),
        })

    @app.get('/map-layers/search')
    def map_layers_search(q: str = '', limit: int = 12):
        q = (q or '').strip()
        limit = max(1, min(int(limit), 25))
        if len(q) < 2:
            return JSONResponse({'results': []})
        try:
            results = _search_local(q, limit)
        except Exception as e:
            warn(f"Local search failed for {q!r}: {e}")
            results = []
        # Curated bike-friendly businesses match by name, ahead of everything.
        ql = q.lower()
        results = [
            {
                'label': b['name'],
                'sublabel': b.get('note') or b.get('address') or 'Bike friendly business',
                'lat': b['lat'],
                'lon': b['lon'],
                'kind': 'business',
            }
            for b in BIKE_BUSINESSES if ql in b['name'].lower()
        ] + [
            {
                'label': p['name'],
                'sublabel': p.get('note') or 'Landmark',
                'lat': p['lat'],
                'lon': p['lon'],
                'kind': 'landmark',
            }
            for p in LANDMARKS if ql in p['name'].lower()
        ] + results
        # Nominatim used to be a fallback only, so ANY local hit (a street
        # prefix, one address) hid every business and POI. Now it fills the
        # remaining slots whenever the query is specific enough — that is
        # what makes "Willy Taco" work, not just streets and stops. Cached +
        # debounced client-side, so the usage policy is respected.
        if len(results) < limit and len(q) >= 3:
            try:
                seen = {r['label'].strip().lower() for r in results}
                results += [
                    r for r in _search_nominatim(q)
                    if r['label'].strip().lower() not in seen
                ]
            except Exception:
                pass
        return JSONResponse({'results': results[:limit]})

    @app.get('/map-layers/road-info')
    def map_layers_road_info(lat: float, lon: float):
        """Nearest road segment + its owner contact info ("Who Owns The Roads" on tap)."""
        query = """
        WITH pt AS (
            SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 6570) AS geom
        )
        SELECT
            r."Name", r."Type", r."Owner", r."Email", r."Phone", r."Online Form",
            ST_Distance(r.geometry, pt.geom) AS distance_ft
        FROM "Roads".roads AS r, pt
        ORDER BY r.geometry <-> pt.geom
        LIMIT 1
        """
        try:
            conn = mrsm.get_connector('sql:bwg')
            df = conn.read(query, params={'lat': lat, 'lon': lon})
        except Exception as e:
            return JSONResponse({'error': str(e)}, status_code=500)
        if df is None or not len(df):
            return JSONResponse({'error': 'No road found.'}, status_code=404)
        row = df.iloc[0]

        def _val(key):
            v = row.get(key)
            return None if v is None or (isinstance(v, float) and v != v) or v == 'N/A' else v

        return JSONResponse({
            'name': _val('Name'),
            'type': _val('Type'),
            'owner': _val('Owner'),
            'email': _val('Email'),
            'phone': _val('Phone'),
            'online_form': _val('Online Form'),
            'distance_ft': round(float(row['distance_ft']), 1),
        })

    @app.post('/map-layers/feedback')
    async def submit_layer_feedback(
        request: Request,
        layer: str = Form(''),
        name: str = Form(''),
        lat: float = Form(None),
        lon: float = Form(None),
        props: str = Form(''),
        feedback: str = Form(''),
        photo: UploadFile = File(None),
    ):
        rec_id = uuid.uuid4().hex
        photo_filename = None
        if photo is not None and photo.filename:
            ext = Path(photo.filename).suffix or '.jpg'
            photo_filename = f'{rec_id}{ext}'
            with open(_photos_dir() / photo_filename, 'wb') as out:
                shutil.copyfileobj(photo.file, out)

        client = request.client
        FEEDBACK_PIPE.sync(
            [{
                'id': rec_id,
                'layer': layer or None,
                'name': name or None,
                'lat': lat,
                'lon': lon,
                'props': props or None,
                'feedback': feedback or None,
                'photo_filename': photo_filename,
                'ip': client.host if client else None,
                'user_agent': request.headers.get('user-agent'),
            }],
            blocking=False,
        )
        return JSONResponse({'ok': True, 'id': rec_id})
