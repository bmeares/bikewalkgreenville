#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# vim:fenc=utf-8

"""
Property tax per acre for Greenville County, SC.

Each lot's current tax bill (Greenville County GIS parcel layer, TAX_YEAR) divided by its land
area, drawn as 3D extrusions whose height is tax per acre.

Lots: condo, garage and "building only" units are drawn by the county as small symbols (some just
30 sq ft), which would inflate their per-acre values, so they are stacked onto the land they sit on:
    1. contained: a unit drawn inside a land parcel's outline joins that parcel;
    2. pin: otherwise it joins its PIN base lot (lot 00800 <- units 00801-00899) within ~300 ft;
    3. cluster: otherwise neighboring units are grouped into a building that joins the land
       parcel it overlaps (or the nearest within ~100 ft).
  A unit is a parcel under 0.015 acres (650 sq ft, smaller than any buildable lot), or under
  0.05 acres with a unit-style description or a commercial, multi-family or industrial type.
  Townhome lots are real lots and stay as they are.

Pipes:
- `plugin:taxmap` / `parcels` / `greenville`: the parcels from the county's public parcel layer, in EPSG:3857.
- `sql:bwg` / `parcel_taxes` / `greenville`: each parcel's tax by year (one row per PIN and tax year).
- `plugin:taxmap` / `lots` / `greenville`: the stacked lots.

Routes:
- `/dash/tax-per-acre`: the page (map + comparisons).
- `/taxmap/map`: the standalone 3D map (embeddable).
- `/taxmap/tiles/lots/{z}/{x}/{y}.pbf`: vector tiles.
- `/taxmap/feature/lots/{id}`: details for a clicked lot.
- `/taxmap/equivalent/lots/{id}`: the nearest houses paying the same tax.
- `/taxmap/breakdown/{by}`, `/taxmap/srt.geojson`.
"""

import functools
import json
import re
import time
from typing import Any, Optional

import meerschaum as mrsm
from meerschaum.plugins import web_page, dash_plugin, api_plugin
from meerschaum.utils.warnings import info, warn

__version__ = '0.1.0'

PAGE_PATH: str = 'tax-per-acre'
DEFAULT_LOCATION: str = 'greenville'
PARCELS_PIPE_KEYS: tuple = ('plugin:taxmap', 'parcels')

### Parcels and their current tax bills: the county's public parcel layer (CAD_PARCEL_MP_VW).
### Its TOTTAX was checked against the Auditor's millage sheets: commercial parcels in DIST 500
### imply 339.7 mills, the 2025 rate (331.5) plus ~3.5% in fees (the same fees on the old 2024
### copy imply 371.3 for 358.8). Bump TAX_YEAR (or the pipe's taxmap:tax_year) when the county
### switches to new bills; the parcel_taxes pipe keeps one row per parcel and year.
TAX_YEAR: int = 2025
PARCELS_MAPSERVER: str = 'https://www.gcgis.org/arcgis3/rest/services/GreenvilleNJ/QueryLayers/MapServer'
PARCELS_LAYER_ID: int = 0
PARCEL_FIELDS: list[str] = [
    'PIN', 'SHEET', 'BLOCK', 'LOT', 'STRNUM', 'LOCATE', 'DESCR', 'SUBDIV', 'LANDUSE', 'PROPTYPE',
    'DIST', 'OWNAM1', 'TOTTAX', 'TAXMKTVAL', 'FAIRMKTVAL', 'LANDVAL', 'BLDGVAL',
]
### Only the stacked-lots view is served (the as-drawn parcels view was retired).
VIEWS: tuple = ('lots',)

UNIT_SQFT_ACRES: float = 0.015
SMALL_ACRES: float = 0.05
UNIT_DESCR_REGEX: str = '(UNIT|FLOOR|FLR|BLDG|BUILDING|SUITE|STE |GARAGE|PARKING|SPACE|STORAGE)'

MAPLIBRE_JS_URL: str = 'https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/5.24.0/maplibre-gl.min.js'
MAPLIBRE_CSS_URL: str = 'https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/5.24.0/maplibre-gl.min.css'
MAPLIBRE_STYLE_URL: str = 'https://tiles.openfreemap.org/styles/dark'
### Swamp Rabbit Trail overlay: a muted green, so the reference line doesn't outshine the data.
SRT_COLOR: str = '#4f8f74'

### Zoning: Greenville County GIS public MapServer, layer 3 'Zoning'. Countywide: each polygon's
### JCODE is the FIPS code of the government whose zoning it is (45045 = Greenville County,
### for unincorporated land; the rest match municipal JCODEs in BND_MUNICIPAL).
ZONING_MAPSERVER: str = 'https://www.gcgis.org/arcgis3/rest/services/GreenvilleNJ/Search_and_Select_Query/MapServer'
ZONING_LAYER_ID: int = 3
COUNTY_JCODE: str = '45045'
COUNTY_ZONING_NAME: str = 'GREENVILLE COUNTY'
MIN_ZONE_SHARE: float = 0.05
UNINCORPORATED: str = 'UNINCORPORATED'
NO_ZONE_LABEL: str = '(no zoning polygon)'

### Use class: the county's property type split by land-use code. Code names aren't in the layer;
### these were read from the data, and the county recoded some for 2025: apartment complexes were
### 120/122 in 2024 and are 1182/1183 (type MULTI-FAMILY, median 6-7 acres) in 2025. 1180/1181 are
### vacant lots (no building value on 86-93%); 110-113 are small multi-family buildings.
### `landuse` and `proptype` exist on both the lots and parcels tables.
USE_CLASS_SQL: str = """(CASE
    WHEN landuse IN ('120', '122', '1182', '1183') THEN 'Apartments'
    WHEN landuse IN ('1100', '1101') THEN 'Single-family'
    WHEN landuse IN ('1180', '1181') AND proptype IS DISTINCT FROM 'MULTI-FAMILY' THEN 'Vacant residential'
    WHEN landuse IN ('1170', '1171') OR proptype = 'MOBILE HOME' THEN 'Mobile home'
    WHEN proptype = 'MULTI-FAMILY' THEN 'Small multi-family'
    WHEN proptype = 'COMMERCIAL' THEN 'Commercial'
    WHEN proptype = 'INDUSTRIAL' THEN 'Industrial'
    WHEN proptype = 'AGRICULTURAL' THEN 'Agricultural'
    WHEN proptype = 'RESIDENTIAL' THEN 'Single-family'
    ELSE 'Other' END)"""

### Map colors for the categorical modes. Only three hues stay distinguishable from every other
### one on a map (dataviz validator, all pairs, dark surface), so each hue is a family and the
### rest share the neutral; the legend checkboxes separate classes that share a color.
BLUE, ORANGE, AQUA, NEUTRAL = '#3987e5', '#d95926', '#199e70', '#6b6a64'
USE_CLASSES: list[tuple[str, str]] = [
    ('Single-family', BLUE),
    ('Small multi-family', AQUA),
    ('Apartments', AQUA),
    ('Commercial', ORANGE),
    ('Industrial', NEUTRAL),
    ('Mobile home', NEUTRAL),
    ('Vacant residential', NEUTRAL),
    ('Agricultural', NEUTRAL),
    ('Other', NEUTRAL),
]
### Zoning families: projects/tax-per-acre/zoning_families.csv (scripts/taxmap_zoning_families.py), loaded into
### the `zone_families` pipe. Built from the county GIS zoning abbreviations list.
ZONE_FAMILIES: list[tuple[str, str]] = [
    ('Residential', BLUE),
    ('Mixed-use', AQUA),
    ('Commercial & office', ORANGE),
    ('Industrial & services', NEUTRAL),
    ('Planned development', NEUTRAL),
    ('Civic, parks & special', NEUTRAL),
    ('Agricultural', NEUTRAL),
    ('Unzoned', NEUTRAL),
    ('Unclassified', NEUTRAL),
]
ZONE_FAMILIES_CSV_DEFAULT: str = '../projects/tax-per-acre/zoning_families.csv'
### SQL for a lot's zoning family; needs `LEFT JOIN <zone families> zf` (see zone_family_join()).
ZONE_FAMILY_SQL: str = "COALESCE(zf.family, 'Unclassified')"

### "Typical house": median tax per acre of single-family lots (land use 1100) of 0.1-2 acres
### that aren't stacked. Per zone when the zone has at least this many such houses.
TYPICAL_HOUSE_SQL: str = "landuse = '1100' AND acres BETWEEN 0.1 AND 2 AND stacked = 0"
TYPICAL_HOUSE_MIN: int = 25

TILE_CACHE_SIZE: int = 256
DATA_VERSION_SECONDS: int = 600


def register(pipe: mrsm.Pipe) -> dict[str, Any]:
    """Default parameters for the `lots` and `grid` pipes."""
    if pipe.metric_key == 'zoning':
        return {
            'columns': {'primary': 'zone_id'},
            'indices': {'zone': 'zone', 'jcode': 'jcode'},
            'dtypes': {
                'zone_id': 'string', 'zone': 'string', 'jcode': 'string',
                'geometry': 'geometry[MULTIPOLYGON, 6570]',
            },
            'upsert': True,
            'taxmap': {'mapserver': ZONING_MAPSERVER, 'layer_id': ZONING_LAYER_ID},
        }
    if pipe.metric_key == 'parcels':
        return {
            'columns': {'primary': 'pin'},
            'indices': {'lot_key': 'lot_key'},
            'dtypes': {
                'pin': 'string', 'tax_year': 'int', 'lot_key': 'string', 'is_base': 'bool',
                'tax': 'float', 'acres': 'float', 'geometry': 'geometry[MULTIPOLYGON, 3857]',
            },
            'upsert': True,
            'taxmap': {'mapserver': PARCELS_MAPSERVER, 'layer_id': PARCELS_LAYER_ID, 'tax_year': TAX_YEAR},
        }
    if pipe.metric_key == 'millage':
        return {
            'columns': {'year': 'year', 'dist': 'dist'},
            'dtypes': {'year': 'int', 'dist': 'string', 'city_name': 'string'},
            'upsert': True,
        }
    if pipe.metric_key == 'zone_families':
        return {
            'columns': {'primary': 'zf_key'},
            'dtypes': {
                'zf_key': 'string', 'zf_jurisdiction': 'string', 'zf_zone': 'string',
                'family': 'string', 'family_name': 'string', 'family_source': 'string',
            },
            'upsert': True,
        }
    if pipe.metric_key == 'grid':
        return {
            'columns': {'level': 'level', 'i': 'i', 'j': 'j'},
            'dtypes': {
                'level': 'int', 'i': 'int', 'j': 'int', 'tax': 'float', 'acres': 'float',
                'tax_per_acre': 'float', 'geometry': 'geometry[POLYGON, 3857]',
            },
            'upsert': True,
            'taxmap': {'lots': {'location': pipe.location_key or DEFAULT_LOCATION}},
        }
    if pipe.metric_key != 'lots':
        return {}
    return {
        'columns': {'primary': 'lot_id'},
        'dtypes': {
            'lot_id': 'string',
            'parcels': 'int',
            'stacked': 'int',
            'member_pins': 'json',
            'tax': 'float',
            'acres': 'float',
            'tax_per_acre': 'float',
            'value_per_acre': 'float',
            'tax_district': 'string',
            'municipality': 'string',
            'muni_share': 'float',
            'zone': 'string',
            'zone_jcode': 'string',
            'zone_jurisdiction': 'string',
            'zone_share': 'float',
            'zones': 'int',
            'proptype': 'string',
            'landuse': 'string',
            'geometry': 'geometry[MULTIPOLYGON, 3857]',
        },
        'upsert': True,
        'taxmap': {
            'parcels': {
                'connector': PARCELS_PIPE_KEYS[0],
                'metric': PARCELS_PIPE_KEYS[1],
                'location': pipe.location_key or DEFAULT_LOCATION,
            },
        },
    }


def get_parcels_pipe(pipe: Optional[mrsm.Pipe] = None, instance: Optional[str] = None) -> mrsm.Pipe:
    """Return the parcels pipe (on the same instance as `pipe`)."""
    params = ((pipe.parameters.get('taxmap', {}) or {}).get('parcels', {}) if pipe else {}) or {}
    return mrsm.Pipe(
        params.get('connector', PARCELS_PIPE_KEYS[0]),
        params.get('metric', PARCELS_PIPE_KEYS[1]),
        params.get('location', (pipe.location_key if pipe else None) or DEFAULT_LOCATION),
        instance=(pipe.instance_keys if pipe else instance),
    )


def get_aux_pipe(pipe: mrsm.Pipe, metric: str) -> mrsm.Pipe:
    """
    The `zoning` (`plugin:taxmap`) or `municipalities` (`sql:bwg`) pipe,
    with the same location and instance as `pipe`.
    """
    connector = 'plugin:taxmap' if metric == 'zoning' else 'sql:bwg'
    return mrsm.Pipe(connector, metric, pipe.location_key or DEFAULT_LOCATION, instance=pipe.instance_keys)


def _page_mapserver(layer_url: str, out_fields: list[str], what: str) -> tuple['gpd.GeoDataFrame', str]:
    """
    Every feature of a public ArcGIS MapServer layer in EPSG:6570, paged with plain GETs
    (same approach as `fetch_paginated_mapserver()` in the BWG `greenville-county` plugin).
    """
    requests = mrsm.attempt_import('requests', lazy=False)
    gpd = mrsm.attempt_import('geopandas', lazy=False)
    pd = mrsm.attempt_import('pandas', lazy=False)
    meta = requests.get(layer_url, params={'f': 'json'}, timeout=60).json()
    page_size = int(meta.get('maxRecordCount') or 1000)
    oid_field = meta.get('objectIdField') or 'OBJECTID'
    query_url = f"{layer_url}/query"
    total = requests.get(
        query_url, params={'where': '1=1', 'returnCountOnly': 'true', 'f': 'json'}, timeout=60,
    ).json().get('count', 0)
    info(f"Fetching {total:,} {what} from '{meta.get('name')}' in pages of {page_size:,}.")
    fields = ','.join(dict.fromkeys([oid_field, *out_fields]))
    frames, offset, size, failures = [], 0, page_size, 0
    while offset < total:
        try:
            resp = requests.get(query_url, params={
                'where': '1=1', 'outFields': fields, 'outSR': 6570,
                'resultOffset': offset, 'resultRecordCount': size, 'orderByFields': oid_field,
                'f': 'geojson',
            }, timeout=180)
            resp.raise_for_status()
            features = resp.json().get('features', [])
        except Exception as e:
            ### The server sometimes 500s on heavy pages: retry with smaller pages.
            failures += 1
            if failures > 6:
                raise
            size = max(100, size // 2)
            warn(f"{what} page at offset {offset:,} failed ({e}); retrying with {size:,} per page.", stack=False)
            time.sleep(2 * failures)
            continue
        failures = 0
        if not features:
            break
        frames.append(gpd.GeoDataFrame.from_features(features, crs=6570))
        offset += len(features)
        if len(frames) % 20 == 0:
            info(f"  {offset:,} of {total:,} {what}")
    if offset < total:
        raise RuntimeError(f"Only fetched {offset:,} of {total:,} {what}.")
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=6570)
    return gdf, oid_field


def _multipolygons(gdf: 'gpd.GeoDataFrame') -> 'gpd.GeoDataFrame':
    """Drop empty geometries and make the rest valid MultiPolygons."""
    shapely_geometry = mrsm.attempt_import('shapely.geometry', lazy=False)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf['geometry'] = gdf.geometry.make_valid().apply(
        lambda g: shapely_geometry.MultiPolygon(
            [p for p in getattr(g, 'geoms', [g]) if p.geom_type == 'Polygon']
            + [q for p in getattr(g, 'geoms', [g]) if p.geom_type == 'MultiPolygon' for q in p.geoms]
        )
    )
    return gdf[~gdf.geometry.is_empty]


def fetch_county_parcels(pipe: mrsm.Pipe, debug: bool = False) -> 'gpd.GeoDataFrame':
    """
    Every parcel with its current tax bill, straight from the county's public parcel layer
    (GreenvilleNJ/QueryLayers layer 0, `CAD_PARCEL_MP_VW`), in EPSG:3857 with true acres.
    `tax_year` comes from the pipe's `taxmap:tax_year` parameter: the county layer doesn't say
    which year its TOTTAX is, so set it when the county rolls over to new bills (see README).
    Parcels the county no longer has are deleted, so the table matches the current layer.
    """
    pd = mrsm.attempt_import('pandas', lazy=False)
    gpd = mrsm.attempt_import('geopandas', lazy=False)
    params = (pipe.parameters.get('taxmap', {}) or {})
    tax_year = int(params.get('tax_year') or TAX_YEAR)
    layer_url = f"{params.get('mapserver', PARCELS_MAPSERVER).rstrip('/')}/{params.get('layer_id', PARCELS_LAYER_ID)}"
    gdf, _ = _page_mapserver(layer_url, PARCEL_FIELDS, 'parcels')
    gdf = _multipolygons(gdf)
    s = lambda col: gdf[col].astype('string').str.strip().replace('', pd.NA)
    num = lambda col: pd.to_numeric(gdf[col], errors='coerce').astype('float64')
    lot = s('LOT').fillna('')
    out = gpd.GeoDataFrame({
        'pin': s('PIN'),
        'tax_year': tax_year,
        'lot_key': s('SHEET').fillna('') + s('BLOCK').fillna('') + lot.str[:3],
        'is_base': lot.str[-2:] == '00',
        'site_address': (s('STRNUM').fillna('') + ' ' + s('LOCATE').fillna('')).str.strip().replace('', pd.NA),
        'descr': s('DESCR'),
        'subdivision': s('SUBDIV'),
        'landuse': s('LANDUSE'),
        'proptype': s('PROPTYPE'),
        'tax_district': s('DIST'),
        'owner': s('OWNAM1'),
        'tax': num('TOTTAX').fillna(0.0),
        'taxable_market_value': num('TAXMKTVAL'),
        'fair_market_value': num('FAIRMKTVAL'),
        'land_value': num('LANDVAL'),
        'building_value': num('BLDGVAL'),
        'acres': gdf.geometry.area / 43560.0,  # EPSG:6570 is in US feet: true area
    }, geometry=gdf.geometry.values, crs=6570)
    out = out[out['pin'].notna()].drop_duplicates('pin')
    out = out.to_crs(3857)
    info(f"Fetched {len(out):,} parcels, ${out['tax'].sum() / 1e6:,.1f}M in tax ({tax_year} bills).")
    if pipe.exists(debug=debug):
        sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
        with pipe.instance_connector.engine.begin() as connection:
            connection.execute(
                sqlalchemy.text(f'DELETE FROM "{pipe.target}" WHERE NOT (pin = ANY(:ids))'),
                {'ids': list(out['pin'])},
            )
    return out


def fetch_zoning(pipe: mrsm.Pipe, debug: bool = False) -> 'gpd.GeoDataFrame':
    """Zoning polygons from the county's public ArcGIS MapServer."""
    gpd = mrsm.attempt_import('geopandas', lazy=False)
    pd = mrsm.attempt_import('pandas', lazy=False)
    params = (pipe.parameters.get('taxmap', {}) or {})
    layer_url = f"{params.get('mapserver', ZONING_MAPSERVER).rstrip('/')}/{params.get('layer_id', ZONING_LAYER_ID)}"
    gdf, oid_field = _page_mapserver(layer_url, ['ZONING', 'JCODE'], 'zoning polygons')
    gdf = _multipolygons(gdf)
    out = gpd.GeoDataFrame({
        'zone_id': gdf[oid_field].astype(int).astype(str),
        'zone': gdf['ZONING'].astype('string').str.strip().replace('', pd.NA),
        'jcode': gdf['JCODE'].astype('string').str.strip().replace('', pd.NA),
    }, geometry=gdf.geometry.values, crs=6570)
    info(f"Fetched {len(out):,} zoning polygons.")
    if pipe.exists(debug=debug):
        sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
        with pipe.instance_connector.engine.begin() as connection:
            connection.execute(
                sqlalchemy.text(f'DELETE FROM "{pipe.target}" WHERE NOT (zone_id = ANY(:ids))'),
                {'ids': list(out['zone_id'])},
            )
    return out


def _data_path(config_key: str, default: str) -> 'pathlib.Path':
    """A data file path from `plugins:taxmap:<config_key>`, relative to this plugin."""
    import os
    import pathlib
    from meerschaum.config import get_plugin_config
    config = get_plugin_config('taxmap', warn=False) or {}
    path = pathlib.Path(config.get(config_key, None) or default)
    if not path.is_absolute():
        ### realpath: Meerschaum loads plugins through a symlink in root/.internal/plugins.
        path = pathlib.Path(os.path.dirname(os.path.realpath(__file__))) / path
    return path


def fetch_zone_families(pipe: mrsm.Pipe, debug: bool = False) -> 'pd.DataFrame':
    """The reviewed (jurisdiction, zone) -> family table, from projects/tax-per-acre/zoning_families.csv."""
    pd = mrsm.attempt_import('pandas', lazy=False)
    path = _data_path('zone_families_csv', ZONE_FAMILIES_CSV_DEFAULT)
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}; run scripts/taxmap_zoning_families.py.")
    df = pd.read_csv(path, dtype=str).fillna('')
    known = {family for family, _ in ZONE_FAMILIES}
    unknown = sorted(set(df['family']) - known)
    if unknown:
        raise ValueError(f"Unknown zoning families in {path}: {unknown}")
    return pd.DataFrame({
        'zf_key': df['jurisdiction'] + '|' + df['zone'],
        'zf_jurisdiction': df['jurisdiction'],
        'zf_zone': df['zone'],
        'family': df['family'],
        'family_name': df['name'],
        'family_source': df['source'],
    })


def ensure_spatial_index(conn: 'mrsm.connectors.SQLConnector', table: str) -> None:
    """Meerschaum doesn't build GiST indexes, so add one for tile queries."""
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    index_name = re.sub(r'[^a-z0-9_]', '_', f"{table}_geometry_gist".lower())[:63]
    with conn.engine.begin() as connection:
        connection.execute(sqlalchemy.text(
            f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table}" USING GIST (geometry)'
        ))


def fetch(pipe: mrsm.Pipe, debug: bool = False, **kwargs: Any) -> 'gpd.GeoDataFrame':
    """
    Build the stacked lots from the parcels pipe (in the instance database).
    """
    if pipe.metric_key == 'grid':
        return fetch_grid(pipe, debug=debug)
    if pipe.metric_key == 'zoning':
        return fetch_zoning(pipe, debug=debug)
    if pipe.metric_key == 'parcels':
        return fetch_county_parcels(pipe, debug=debug)
    if pipe.metric_key == 'zone_families':
        return fetch_zone_families(pipe, debug=debug)
    if pipe.metric_key == 'millage':
        return fetch_millage(pipe, debug=debug)
    if pipe.metric_key != 'lots':
        raise ValueError(f"Unknown metric '{pipe.metric_key}' (expected 'lots' or 'grid').")

    gpd = mrsm.attempt_import('geopandas', lazy=False)
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    parcels_pipe = get_parcels_pipe(pipe)
    conn = parcels_pipe.instance_connector
    if not parcels_pipe.exists(debug=debug):
        raise RuntimeError(f"Sync {parcels_pipe} first.")
    parcels = parcels_pipe.target
    ensure_spatial_index(conn, parcels)

    is_unit = (
        f"(acres < {UNIT_SQFT_ACRES} OR (acres < {SMALL_ACRES} AND ("
        f"descr ~* '{UNIT_DESCR_REGEX}' OR proptype IN ('COMMERCIAL', 'MULTI-FAMILY', 'INDUSTRIAL'))))"
    )
    statements = [
        f"""
        CREATE TEMP TABLE _tm_p ON COMMIT DROP AS
        SELECT pin, lot_key, is_base, acres, tax, geometry, {is_unit} AS is_unit
        FROM "{parcels}"
        """,
        "CREATE INDEX ON _tm_p (lot_key)",
        "CREATE INDEX ON _tm_p USING gist (geometry)",
        "ANALYZE _tm_p",
        ### Land parcels that can host units: >= 0.05 acres, and not a ring-shaped common area
        ### (holes under half of the filled outline), so neighborhoods aren't swallowed.
        f"""
        CREATE TEMP TABLE _tm_hosts ON COMMIT DROP AS
        SELECT pin, acres, geometry, outline FROM (
            SELECT pin, acres, geometry,
                   (SELECT ST_Union(ST_MakePolygon(ST_ExteriorRing(d.geom))) FROM ST_Dump(geometry) d) AS outline
            FROM _tm_p WHERE acres >= {SMALL_ACRES}
        ) h
        WHERE ST_Area(geometry) >= 0.5 * ST_Area(outline)
        """,
        "CREATE INDEX ON _tm_hosts USING gist (outline)",
        "CREATE INDEX ON _tm_hosts USING gist (geometry)",
        "ANALYZE _tm_hosts",
        ### 1. Units drawn inside a land parcel's outline (the smallest one).
        """
        CREATE TEMP TABLE _tm_a ON COMMIT DROP AS
        SELECT DISTINCT ON (s.pin) s.pin, h.pin AS host_pin, 'contained'::text AS stack_rule
        FROM _tm_p s
        JOIN _tm_hosts h ON h.outline && s.geometry AND ST_Within(s.geometry, ST_Buffer(h.outline, 1.0))
        WHERE s.is_unit AND h.pin <> s.pin
        ORDER BY s.pin, h.acres ASC
        """,
        "CREATE UNIQUE INDEX ON _tm_a (pin)",
        ### 2. Units onto their PIN base lot within ~300 ft (110 m in EPSG:3857 at 35N).
        f"""
        INSERT INTO _tm_a
        SELECT DISTINCT ON (m.pin) m.pin, b.pin, 'pin'
        FROM _tm_p m
        JOIN _tm_p b ON b.lot_key = m.lot_key AND b.is_base AND b.acres >= {SMALL_ACRES}
        WHERE m.is_unit AND NOT m.is_base
          AND NOT EXISTS (SELECT 1 FROM _tm_a x WHERE x.pin = m.pin)
          AND ST_DWithin(m.geometry, b.geometry, 110)
        ORDER BY m.pin, ST_Distance(m.geometry, b.geometry)
        """,
        ### 3. Remaining units grouped into buildings (~25 ft apart); the building joins the land
        ###    parcel it overlaps most, else the nearest within ~100 ft.
        """
        INSERT INTO _tm_a
        WITH cl AS (
            SELECT pin, geometry, ST_ClusterDBSCAN(geometry, eps := 9.3, minpoints := 1) OVER () AS cid
            FROM _tm_p WHERE is_unit AND NOT EXISTS (SELECT 1 FROM _tm_a x WHERE x.pin = _tm_p.pin)
        ), hull AS (
            SELECT cid, ST_Buffer(ST_ConvexHull(ST_Collect(geometry)), 1.0) AS g FROM cl GROUP BY cid
        ), best AS (
            SELECT DISTINCT ON (h.cid) h.cid, x.pin AS host_pin
            FROM hull h JOIN _tm_hosts x ON ST_DWithin(x.geometry, h.g, 37)
            ORDER BY h.cid, ST_Area(ST_Intersection(x.geometry, h.g)) DESC, ST_Distance(x.geometry, h.g)
        )
        SELECT cl.pin, best.host_pin, 'cluster' FROM cl JOIN best USING (cid)
        """,
    ]
    lots_sql = f"""
        WITH x AS (
            SELECT p.*, COALESCE(a.host_pin, p.pin) AS lot_id, a.stack_rule
            FROM "{parcels}" p LEFT JOIN _tm_a a USING (pin)
        ), sizes AS (
            SELECT lot_id, COUNT(*) AS n FROM x GROUP BY lot_id
        ), single AS (
            SELECT x.lot_id, 1 AS parcels, 0 AS stacked, NULL::text AS stack_rules, NULL::json AS member_pins,
                   x.tax, x.fair_market_value, x.taxable_market_value, x.acres, x.geometry
            FROM x JOIN sizes USING (lot_id) WHERE sizes.n = 1
        ), multi AS (
            SELECT x.lot_id, COUNT(*)::int AS parcels, (COUNT(*) - 1)::int AS stacked,
                   string_agg(DISTINCT x.stack_rule, ',') AS stack_rules,
                   json_agg(x.pin ORDER BY x.tax DESC) AS member_pins,
                   SUM(x.tax) AS tax, SUM(x.fair_market_value) AS fair_market_value,
                   SUM(x.taxable_market_value) AS taxable_market_value,
                   ST_Area(ST_Transform(ST_Union(x.geometry), 6570)) / 43560.0 AS acres,
                   ST_Multi(ST_CollectionExtract(ST_Union(x.geometry), 3)) AS geometry
            FROM x JOIN sizes USING (lot_id) WHERE sizes.n > 1
            GROUP BY x.lot_id
        ), lots AS (
            SELECT * FROM single UNION ALL SELECT * FROM multi
        )
        SELECT l.lot_id, l.parcels, l.stacked, l.stack_rules, l.member_pins,
               h.site_address, h.descr, h.subdivision, h.proptype, h.landuse, h.tax_district,
               l.tax, l.fair_market_value, l.taxable_market_value, l.acres,
               l.tax / NULLIF(l.acres, 0) AS tax_per_acre,
               l.fair_market_value / NULLIF(l.acres, 0) AS value_per_acre,
               l.geometry
        FROM lots l JOIN "{parcels}" h ON h.pin = l.lot_id
        WHERE l.geometry IS NOT NULL AND NOT ST_IsEmpty(l.geometry)
    """

    ### Zone and municipality for each lot, by largest overlap. Overlaps are measured in
    ### EPSG:6570 (US feet), never in the Web Mercator tiles' 3857 (which inflates area ~1.5x).
    zoning_pipe = get_aux_pipe(pipe, 'zoning')
    munis_pipe = get_aux_pipe(pipe, 'municipalities')
    has_zoning = zoning_pipe.exists(debug=debug)
    has_munis = munis_pipe.exists(debug=debug)
    if not has_zoning:
        warn(f"{zoning_pipe} is not synced; lots will have no zone.", stack=False)
    if not has_munis:
        warn(f"{munis_pipe} is not synced; lots will have no municipality.", stack=False)
    statements += [
        f"CREATE TEMP TABLE _tm_lots ON COMMIT DROP AS {lots_sql}",
        "CREATE UNIQUE INDEX ON _tm_lots (lot_id)",
        """
        CREATE TEMP TABLE _tm_lg ON COMMIT DROP AS
        SELECT lot_id, g, NULLIF(ST_Area(g), 0) AS area
        FROM (SELECT lot_id, ST_Transform(geometry, 6570) AS g FROM _tm_lots) t
        """,
        "CREATE INDEX ON _tm_lg USING gist (g)",
        "ANALYZE _tm_lg",
    ]
    overlap = "SUM(CASE WHEN ST_Within(l.g, z.g) THEN ST_Area(l.g) ELSE ST_Area(ST_Intersection(l.g, z.g)) END)"
    if has_zoning:
        statements += [
            f"""
            CREATE TEMP TABLE _tm_zs ON COMMIT DROP AS
            SELECT zone, jcode, ST_Subdivide(ST_Transform(geometry, 6570), 128) AS g
            FROM "{zoning_pipe.target}" WHERE zone IS NOT NULL
            """,
            "CREATE INDEX ON _tm_zs USING gist (g)",
            "ANALYZE _tm_zs",
            ### A lot takes the zone covering most of it, if that covers at least MIN_ZONE_SHARE.
            f"""
            CREATE TEMP TABLE _tm_zone ON COMMIT DROP AS
            SELECT DISTINCT ON (o.lot_id) o.lot_id,
                   CASE WHEN o.a / l.area >= {MIN_ZONE_SHARE} THEN o.zone END AS zone,
                   CASE WHEN o.a / l.area >= {MIN_ZONE_SHARE} THEN o.jcode END AS zone_jcode,
                   LEAST(o.a / l.area, 1.0) AS zone_share,
                   COUNT(*) OVER (PARTITION BY o.lot_id) AS zones
            FROM (
                SELECT l.lot_id, z.zone, z.jcode, {overlap} AS a
                FROM _tm_lg l JOIN _tm_zs z ON l.g && z.g AND ST_Intersects(l.g, z.g)
                GROUP BY 1, 2, 3
            ) o JOIN _tm_lg l USING (lot_id)
            ORDER BY o.lot_id, o.a DESC, o.zone
            """,
        ]
    else:
        statements.append(
            "CREATE TEMP TABLE _tm_zone ON COMMIT DROP AS "
            "SELECT NULL::text AS lot_id, NULL::text AS zone, NULL::text AS zone_jcode, "
            "NULL::float AS zone_share, NULL::int AS zones LIMIT 0"
        )
    if has_munis:
        statements += [
            f"""
            CREATE TEMP TABLE _tm_ms ON COMMIT DROP AS
            SELECT municipality, ST_Subdivide(ST_Transform(geometry, 6570), 128) AS g
            FROM "{munis_pipe.target}"
            """,
            "CREATE INDEX ON _tm_ms USING gist (g)",
            "ANALYZE _tm_ms",
            ### A lot is in a municipality if at least half of it is inside the limits.
            f"""
            CREATE TEMP TABLE _tm_muni ON COMMIT DROP AS
            SELECT DISTINCT ON (o.lot_id) o.lot_id, o.municipality, LEAST(o.a / l.area, 1.0) AS muni_share
            FROM (
                SELECT l.lot_id, z.municipality, {overlap} AS a
                FROM _tm_lg l JOIN _tm_ms z ON l.g && z.g AND ST_Intersects(l.g, z.g)
                GROUP BY 1, 2
            ) o JOIN _tm_lg l USING (lot_id)
            ORDER BY o.lot_id, o.a DESC
            """,
        ]
    else:
        statements.append(
            "CREATE TEMP TABLE _tm_muni ON COMMIT DROP AS "
            "SELECT NULL::text AS lot_id, NULL::text AS municipality, NULL::float AS muni_share LIMIT 0"
        )
    unincorporated = f"'{UNINCORPORATED}'" if has_munis else 'NULL'
    statements.append(
        f'CREATE TEMP TABLE _tm_jcodes ON COMMIT DROP AS SELECT DISTINCT jcode, municipality FROM "{munis_pipe.target}"'
        if has_munis else
        "CREATE TEMP TABLE _tm_jcodes ON COMMIT DROP AS SELECT NULL::text AS jcode, NULL::text AS municipality LIMIT 0"
    )
    final_sql = f"""
        SELECT l.*,
               CASE WHEN COALESCE(m.muni_share, 0) >= 0.5 THEN m.municipality ELSE {unincorporated} END AS municipality,
               COALESCE(m.muni_share, 0)::float AS muni_share,
               z.zone, z.zone_jcode,
               CASE WHEN z.zone_jcode = '{COUNTY_JCODE}' THEN '{COUNTY_ZONING_NAME}'
                    ELSE COALESCE((SELECT j.municipality FROM _tm_jcodes j WHERE j.jcode = z.zone_jcode), z.zone_jcode)
               END AS zone_jurisdiction,
               COALESCE(z.zone_share, 0)::float AS zone_share, COALESCE(z.zones, 0)::int AS zones
        FROM _tm_lots l
        LEFT JOIN _tm_zone z USING (lot_id)
        LEFT JOIN _tm_muni m USING (lot_id)
    """

    start = time.perf_counter()
    with conn.engine.begin() as connection:
        for statement in statements:
            connection.execute(sqlalchemy.text(statement))
        rules = connection.execute(sqlalchemy.text(
            "SELECT stack_rule, COUNT(*) FROM _tm_a GROUP BY 1 ORDER BY 1"
        )).fetchall()
        gdf = gpd.read_postgis(sqlalchemy.text(final_sql), connection, geom_col='geometry', crs=3857)
    info(
        f"Stacked {sum(n for _, n in rules):,} units ("
        + ', '.join(f"{rule}: {n:,}" for rule, n in rules)
        + f") into {len(gdf):,} lots in {time.perf_counter() - start:.1f}s."
    )
    if len(gdf):
        zoned = gdf['zone'].notna()
        info(
            f"Zoned {zoned.sum():,} of {len(gdf):,} lots ({100 * zoned.mean():.1f}%, "
            f"{100 * gdf.loc[zoned, 'tax'].sum() / max(gdf['tax'].sum(), 1):.1f}% of tax); "
            f"{(gdf['municipality'] != UNINCORPORATED).sum():,} lots inside a municipality."
        )

    gdf['member_pins'] = [
        (value if isinstance(value, list) else json.loads(value)) if value is not None else None
        for value in gdf['member_pins']
    ]

    ### Lot IDs can change when stacking changes, so remove lots that no longer exist.
    if pipe.exists(debug=debug):
        with conn.engine.begin() as connection:
            connection.execute(
                sqlalchemy.text(f'DELETE FROM "{pipe.target}" WHERE NOT (lot_id = ANY(:ids))'),
                {'ids': list(gdf['lot_id'])},
            )
    return gdf


### Overview grid cell sizes in EPSG:3857 units (~102, 205 and 410 m on the ground at 35N),
### and the zoom levels each one serves. Lots and parcels take over from GRID_MAX_ZOOM + 1.
GRID_LEVELS: dict[int, tuple[int, int]] = {125: (12, 12), 250: (11, 11), 500: (0, 10)}
GRID_MAX_ZOOM: int = 12


def grid_level_for_zoom(z: int) -> Optional[int]:
    for level, (min_z, max_z) in GRID_LEVELS.items():
        if min_z <= z <= max_z:
            return level
    return None


def fetch_grid(pipe: mrsm.Pipe, debug: bool = False) -> 'gpd.GeoDataFrame':
    """
    Area-weighted tax per acre on square grids for the zoomed-out view.
    Each lot's tax and acres are split among the cells it covers by area, so a cell's value
    is the land-area-weighted average of its lots' tax per acre (no inflation from slivers).
    """
    gpd = mrsm.attempt_import('geopandas', lazy=False)
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    location = ((pipe.parameters.get('taxmap', {}) or {}).get('lots', {}) or {}).get('location', None)
    lots_pipe = mrsm.Pipe('plugin:taxmap', 'lots', location or pipe.location_key, instance=pipe.instance_keys)
    if not lots_pipe.exists(debug=debug):
        raise RuntimeError(f"Sync {lots_pipe} first.")
    conn = lots_pipe.instance_connector
    ensure_spatial_index(conn, lots_pipe.target)
    base = min(GRID_LEVELS)
    levels = sorted(GRID_LEVELS)
    unions = ' UNION ALL '.join(
        f"""SELECT {level} AS level, floor(i * {base}.0 / {level})::int AS i, floor(j * {base}.0 / {level})::int AS j,
                   SUM(tax) AS tax, SUM(acres) AS acres FROM _tm_base GROUP BY 2, 3"""
        for level in levels
    )
    statements = [
        f"""
        CREATE TEMP TABLE _tm_base ON COMMIT DROP AS
        SELECT cell.i, cell.j,
               SUM(l.tax * w.share) AS tax,
               SUM(l.acres * w.share) AS acres
        FROM "{lots_pipe.target}" l
        CROSS JOIN LATERAL ST_SquareGrid({base}, l.geometry) AS cell
        CROSS JOIN LATERAL (
            SELECT ST_Area(ST_Intersection(l.geometry, cell.geom)) / NULLIF(ST_Area(l.geometry), 0) AS share
        ) w
        WHERE ST_Intersects(l.geometry, cell.geom) AND w.share > 0
        GROUP BY cell.i, cell.j
        """,
    ]
    grid_sql = f"""
        WITH g AS ({unions})
        SELECT level, i, j, tax, acres, tax / NULLIF(acres, 0) AS tax_per_acre,
               ST_MakeEnvelope(i * level, j * level, (i + 1) * level, (j + 1) * level, 3857) AS geometry
        FROM g WHERE acres > 0
    """
    start = time.perf_counter()
    with conn.engine.begin() as connection:
        for statement in statements:
            connection.execute(sqlalchemy.text(statement))
        gdf = gpd.read_postgis(sqlalchemy.text(grid_sql), connection, geom_col='geometry', crs=3857)
    info(f"Built {len(gdf):,} grid cells ({', '.join(str(l) for l in levels)}) in {time.perf_counter() - start:.1f}s.")
    return gdf


#########################
# Tiles and details     #
#########################

def get_settings() -> dict[str, str]:
    """The instance and location the routes read from (`plugins:taxmap` config)."""
    from meerschaum.config import get_plugin_config
    config = get_plugin_config('taxmap', warn=False) or {}
    instance = (
        config.get('instance', None)
        or mrsm.get_config('meerschaum', 'web_instance', warn=False)
        or mrsm.get_config('meerschaum', 'instance')
    )
    return {'instance': str(instance), 'location': str(config.get('location', DEFAULT_LOCATION))}


@functools.lru_cache(maxsize=1)
def _tables(settings_key: str) -> dict[str, Any]:
    settings = json.loads(settings_key)
    lots = mrsm.Pipe('plugin:taxmap', 'lots', settings['location'], instance=settings['instance'])
    grid = mrsm.Pipe('plugin:taxmap', 'grid', settings['location'], instance=settings['instance'])
    parcels = get_parcels_pipe(lots)
    zone_families = mrsm.Pipe('plugin:taxmap', 'zone_families', settings['location'], instance=settings['instance'])
    millage = mrsm.Pipe('plugin:taxmap', 'millage', settings['location'], instance=settings['instance'])
    parcel_taxes = mrsm.Pipe('sql:bwg', 'parcel_taxes', settings['location'], instance=settings['instance'])
    conn = lots.instance_connector
    for table in (lots.target, parcels.target, grid.target):
        try:
            ensure_spatial_index(conn, table)
        except Exception as e:
            warn(f"taxmap: could not index {table}: {e}", stack=False)
    return {
        'lots': lots.target, 'parcels': parcels.target, 'grid': grid.target,
        'zone_families': zone_families.target, 'millage': millage.target,
        'parcel_taxes': parcel_taxes.target, 'conn': conn,
    }


def zone_family_join(alias: str = '') -> str:
    """LEFT JOIN of the zone families table as `zf`, onto the lots table (aliased `alias`)."""
    prefix = f"{alias}." if alias else ''
    return (
        f'LEFT JOIN "{get_tables()["zone_families"]}" zf '
        f'ON zf.zf_jurisdiction = {prefix}zone_jurisdiction AND zf.zf_zone = {prefix}zone'
    )


def get_tables() -> dict[str, Any]:
    return _tables(json.dumps(get_settings(), sort_keys=True))


@functools.lru_cache(maxsize=2)
def _data_version(bucket: int) -> str:
    """Changes when either table changes, so browsers re-fetch tiles after a sync."""
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    tables = get_tables()
    parts = []
    with tables['conn'].engine.connect() as connection:
        for view in (*VIEWS, 'grid'):
            row = connection.execute(sqlalchemy.text(
                f"SELECT COUNT(*), COALESCE(ROUND(SUM(tax)), 0) FROM \"{tables[view]}\""
            )).fetchone()
            parts.append(f"{row[0]}-{int(row[1])}")
    return '-'.join(parts)


def data_version() -> str:
    try:
        return _data_version(int(time.time() // DATA_VERSION_SECONDS))
    except Exception:
        return 'none'


@functools.lru_cache(maxsize=TILE_CACHE_SIZE)
def _tile(view: str, z: int, x: int, y: int, version: str) -> bytes:
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    tables = get_tables()
    level = grid_level_for_zoom(z)
    if level is not None:
        sql = """
            WITH env AS (SELECT ST_TileEnvelope(:z, :x, :y) AS g)
            SELECT ST_AsMVT(t, :layer, 4096, 'geom') FROM (
                SELECT ST_AsMVTGeom(d.geometry, env.g, 4096, 16, true) AS geom,
                       'g' || d.level || ':' || d.i || ':' || d.j AS id,
                       COALESCE(ROUND(d.tax_per_acre), 0)::bigint AS tpa,
                       ROUND(d.tax)::bigint AS tax,
                       ROUND(d.acres::numeric, 1)::float AS acres,
                       0 AS stacked, 1 AS grid
                FROM "%s" d, env
                WHERE d.level = :level AND d.geometry && env.g
            ) t WHERE t.geom IS NOT NULL
        """ % tables['grid']
        with tables['conn'].engine.connect() as connection:
            data = connection.execute(
                sqlalchemy.text(sql), {'z': z, 'x': x, 'y': y, 'layer': view, 'level': level}
            ).scalar()
        return bytes(data or b'')
    id_col = 'lot_id' if view == 'lots' else 'pin'
    stacked = 'stacked' if view == 'lots' else '0'
    tax_per_acre = 'tax_per_acre' if view == 'lots' else 'tax / NULLIF(acres, 0)'
    ### Category attributes for the map's color-by and filter: pt (property type), lu (land-use
    ### code), td (tax district), uc (use class), mu (municipality), zn (zone), zf (zoning family).
    categories = f""",
                   COALESCE(d.proptype, '(none)') AS pt,
                   COALESCE(d.landuse, '(none)') AS lu,
                   COALESCE(d.tax_district, '(none)') AS td,
                   {USE_CLASS_SQL.replace('landuse', 'd.landuse').replace('proptype', 'd.proptype')} AS uc"""
    join = ''
    if view == 'lots':
        categories += f""",
                   COALESCE(d.municipality, '(none)') AS mu,
                   COALESCE(d.zone, '{NO_ZONE_LABEL}') AS zn,
                   {ZONE_FAMILY_SQL} AS zf"""
        join = zone_family_join('d')
    sql = f"""
        WITH env AS (SELECT ST_TileEnvelope(:z, :x, :y) AS g)
        SELECT ST_AsMVT(t, :layer, 4096, 'geom') FROM (
            SELECT ST_AsMVTGeom(d.geometry, env.g, 4096, 16, true) AS geom,
                   d.{id_col} AS id,
                   COALESCE(ROUND({tax_per_acre}), 0)::bigint AS tpa,
                   ROUND(d.tax)::bigint AS tax,
                   ROUND(d.acres::numeric, 3)::float AS acres,
                   {stacked}::int AS stacked{categories}
            FROM "{tables[view]}" d {join}
            CROSS JOIN env
            WHERE d.geometry && env.g
        ) t WHERE t.geom IS NOT NULL
    """
    with tables['conn'].engine.connect() as connection:
        data = connection.execute(
            sqlalchemy.text(sql), {'z': z, 'x': x, 'y': y, 'layer': view}
        ).scalar()
    return bytes(data or b'')


def feature_details(view: str, feature_id: str) -> Optional[dict[str, Any]]:
    """Details for the popup: the lot (with its stacked parcels) or the parcel (with its lot)."""
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    tables = get_tables()
    columns = """site_address, descr, subdivision, proptype, landuse, tax_district,
                 tax, fair_market_value, acres"""
    with tables['conn'].engine.connect() as connection:
        if view == 'lots':
            row = connection.execute(sqlalchemy.text(
                f"""SELECT lot_id AS id, {columns}, parcels, stacked, stack_rules, member_pins,
                           municipality, zone, zone_jurisdiction, zone_share, zones
                    FROM "{tables['lots']}" WHERE lot_id = :id"""
            ), {'id': feature_id}).mappings().fetchone()
            if not row:
                return None
            out = dict(row)
            members = out.pop('member_pins', None) or []
            if isinstance(members, str):
                members = json.loads(members)
            if members:
                rows = connection.execute(sqlalchemy.text(
                    f"""SELECT pin, descr, proptype, tax, acres FROM "{tables['parcels']}"
                        WHERE pin = ANY(:pins) ORDER BY tax DESC LIMIT 8"""
                ), {'pins': list(members)}).mappings().fetchall()
                out['members'] = [dict(r) for r in rows]
        else:
            row = connection.execute(sqlalchemy.text(
                f"""SELECT pin AS id, {columns} FROM "{tables['parcels']}" WHERE pin = :id"""
            ), {'id': feature_id}).mappings().fetchone()
            if not row:
                return None
            out = dict(row)
            lot = connection.execute(sqlalchemy.text(
                f"""SELECT lot_id, parcels, tax, acres FROM "{tables['lots']}"
                    WHERE lot_id = :id OR (member_pins IS NOT NULL AND member_pins::jsonb ? :id)
                    ORDER BY (lot_id = :id) DESC LIMIT 1"""
            ), {'id': feature_id}).mappings().fetchone()
            if lot and int(lot['parcels']) > 1:
                out['lot'] = dict(lot)
    for key, value in list(out.items()):
        if hasattr(value, 'item'):
            out[key] = value.item()
        elif value is not None and not isinstance(value, (str, int, float, list, dict, bool)):
            out[key] = float(value)
    out['tax_per_acre'] = (out['tax'] / out['acres']) if out.get('acres') else None
    out['view'] = view
    try:
        out['compare'] = compare_to_benchmarks(out)
    except Exception as e:
        warn(f"taxmap: benchmarks failed: {e}", stack=False)
    return out


@functools.lru_cache(maxsize=2)
def _benchmarks(bucket: int) -> dict[str, Any]:
    """
    Walmart stores' combined tax per acre, and the typical house's (median single-family lot,
    see TYPICAL_HOUSE_SQL) countywide and in each zoning district with enough houses.
    """
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    tables = get_tables()
    lots, parcels = tables['lots'], tables['parcels']
    with tables['conn'].engine.connect() as c:
        q = lambda sql: c.execute(sqlalchemy.text(sql)).mappings().fetchall()
        walmart = q(f"""
            SELECT SUM(l.tax) / SUM(l.acres) AS tax_per_acre, COUNT(*) AS n FROM "{lots}" l
            JOIN "{parcels}" p ON p.pin = l.lot_id
            WHERE p.owner ILIKE 'WAL%MART%' AND l.acres >= 10""")[0]
        county = q(f"""
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY tax_per_acre) AS tpa,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY tax) AS tax, COUNT(*) AS n
            FROM "{lots}" WHERE {TYPICAL_HOUSE_SQL}""")[0]
        zones = q(f"""
            SELECT zone_jurisdiction AS j, zone AS z,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY tax_per_acre) AS tpa,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY tax) AS tax, COUNT(*) AS n
            FROM "{lots}" WHERE {TYPICAL_HOUSE_SQL} AND zone IS NOT NULL
            GROUP BY 1, 2 HAVING COUNT(*) >= {TYPICAL_HOUSE_MIN}""")
    return {
        'walmart': {'tpa': _num(walmart['tax_per_acre']), 'stores': int(walmart['n'] or 0)},
        'house': {'tpa': _num(county['tpa']), 'tax': _num(county['tax']), 'n': int(county['n'] or 0)},
        'house_by_zone': {
            f"{r['j']}|{r['z']}": {'tpa': float(r['tpa']), 'tax': float(r['tax']), 'n': int(r['n'])}
            for r in zones
        },
    }


def benchmarks() -> dict[str, Any]:
    return _benchmarks(int(time.time() // DATA_VERSION_SECONDS))


def typical_house(jurisdiction: Optional[str] = None, zone: Optional[str] = None) -> dict[str, Any]:
    """The typical house in a zone (if it has enough houses), else countywide; says which."""
    b = benchmarks()
    by_zone = b['house_by_zone'].get(f"{jurisdiction}|{zone}") if zone else None
    if by_zone:
        return {**by_zone, 'zone': zone, 'jurisdiction': jurisdiction, 'scope': 'zone'}
    return {**b['house'], 'zone': None, 'jurisdiction': None, 'scope': 'county'}


def compare_to_benchmarks(details: dict[str, Any]) -> dict[str, Any]:
    """How many Walmart acres and typical houses a lot's tax per acre equals."""
    b = benchmarks()
    tpa = details.get('tax_per_acre')
    house = typical_house(details.get('zone_jurisdiction'), details.get('zone'))
    county_house = b['house']
    ratio = lambda a, c: (a / c) if a is not None and c else None
    return {
        'walmart_tpa': b['walmart']['tpa'],
        'walmart_x': ratio(tpa, b['walmart']['tpa']),
        'house': house,
        'house_x': ratio(tpa, house['tpa']),
        'county_house_tpa': county_house['tpa'],
        'county_house_x': ratio(tpa, county_house['tpa']),
    }


EQUIVALENT_MAX_HOUSES: int = 3000
WEB_MERCATOR_SCALE: float = 0.8206  # cos(34.85°): EPSG:3857 meters -> true meters here


def equivalent_houses(view: str, feature_id: str) -> Optional[dict[str, Any]]:
    """
    The nearest single-family houses (TYPICAL_HOUSE_SQL, nearest first) whose tax adds up to
    the clicked lot's or parcel's tax, as GeoJSON. Shows how much house land pays the same tax.
    Capped at EQUIVALENT_MAX_HOUSES (`complete` is false when the cap is hit first).
    """
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    tables = get_tables()
    id_col = 'lot_id' if view == 'lots' else 'pin'
    sql = f"""
        WITH target AS (
            SELECT {id_col} AS id, tax, acres, geometry FROM "{tables[view]}" WHERE {id_col} = :id
        ), near AS (
            SELECT h.lot_id, h.tax, h.acres, h.geometry,
                   ST_Distance(h.geometry, ST_Centroid(t.geometry)) AS dist
            FROM "{tables['lots']}" h, target t
            WHERE {TYPICAL_HOUSE_SQL.replace('landuse', 'h.landuse').replace('acres', 'h.acres').replace('stacked', 'h.stacked')}
              AND h.lot_id <> t.id AND h.tax > 0
            ORDER BY h.geometry <-> ST_Centroid(t.geometry)
            LIMIT {EQUIVALENT_MAX_HOUSES}
        ), run AS (
            SELECT n.*, SUM(n.tax) OVER (ORDER BY n.dist, n.lot_id) AS cum FROM near n
        ), picked AS (
            SELECT r.* FROM run r, target t WHERE r.cum - r.tax < t.tax
        )
        SELECT json_build_object(
            'target_tax', (SELECT tax FROM target),
            'target_acres', (SELECT acres FROM target),
            'houses', (SELECT COUNT(*) FROM picked),
            'tax', (SELECT COALESCE(SUM(tax), 0) FROM picked),
            'acres', (SELECT COALESCE(SUM(acres), 0) FROM picked),
            'radius_m', (SELECT MAX(dist) FROM picked),
            'target', (SELECT json_build_object(
                'type', 'Feature',
                'properties', json_build_object('id', id, 'tpa', ROUND(tax / NULLIF(acres, 0))),
                'geometry', ST_AsGeoJSON(ST_Transform(geometry, 4326), 6)::json
            ) FROM target),
            'geojson', json_build_object('type', 'FeatureCollection', 'features', COALESCE((
                SELECT json_agg(json_build_object(
                    'type', 'Feature',
                    'properties', json_build_object('id', lot_id, 'tpa', ROUND(tax / NULLIF(acres, 0))),
                    'geometry', ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(geometry, 0.5), 4326), 6)::json
                )) FROM picked), '[]'::json))
        )::text
    """
    with tables['conn'].engine.connect() as connection:
        raw = connection.execute(sqlalchemy.text(sql), {'id': feature_id}).scalar()
    out = json.loads(raw) if raw else None
    if not out or out.get('target_tax') is None:
        return None
    out['complete'] = (out['tax'] or 0) >= (out['target_tax'] or 0)
    ### EPSG:3857 distances are stretched by 1/cos(latitude); Greenville County is at ~34.85°N.
    if out.get('radius_m') is not None:
        out['radius_m'] = float(out['radius_m']) * WEB_MERCATOR_SCALE
    return out


#########################
# Breakdowns            #
#########################

### Groupings of the lots table. Every grouping covers every lot (missing values get an
### explicit bucket), so each one sums to the same county total.
BREAKDOWNS: dict[str, dict[str, str]] = {
    'use_class': {
        'title': 'Use class',
        'key': USE_CLASS_SQL,
    },
    'zone_family': {
        'title': 'Zoning family',
        'key': ZONE_FAMILY_SQL,
    },
    'proptype': {
        'title': 'Property type',
        'key': "COALESCE(proptype, '(none)')",
    },
    'landuse': {
        'title': 'Land-use code',
        'key': "COALESCE(landuse, '(none)')",
        'detail': "string_agg(DISTINCT COALESCE(proptype, '(none)'), ', ')",
    },
    'zone': {
        'title': 'Zoning district (by zoning jurisdiction)',
        'key': f"COALESCE(zone_jurisdiction, '(none)') || ' · ' || COALESCE(zone, '{NO_ZONE_LABEL}')",
    },
    'zone_code': {
        'title': 'Zoning code (all jurisdictions)',
        'key': f"COALESCE(zone, '{NO_ZONE_LABEL}')",
        'detail': "string_agg(DISTINCT COALESCE(zone_jurisdiction, '(none)'), ', ')",
    },
    'municipality': {
        'title': 'Municipality',
        'key': "COALESCE(municipality, '(none)')",
    },
    'tax_district': {
        'title': 'Tax district (DIST)',
        'key': "COALESCE(tax_district, '(none)')",
    },
}
### Columns a breakdown can be filtered on (drill-down), e.g. land-use codes within one type.
BREAKDOWN_FILTERS: dict[str, str] = {
    'use_class': USE_CLASS_SQL,
    'zone_family': ZONE_FAMILY_SQL,
    'proptype': "COALESCE(proptype, '(none)')",
    'landuse': "COALESCE(landuse, '(none)')",
    'municipality': "COALESCE(municipality, '(none)')",
    'zone': f"COALESCE(zone, '{NO_ZONE_LABEL}')",
    'zone_jurisdiction': "COALESCE(zone_jurisdiction, '(none)')",
    'tax_district': "COALESCE(tax_district, '(none)')",
}
### Tax district (DIST) names come from the County Auditor's millage sheets, parsed into
### projects/tax-per-acre/millage.csv (scripts/taxmap_parse_millage.py; one row per year and DIST,
### `city_name` blank outside municipalities) and loaded into the `millage` pipe.
### The sheet for TAX_YEAR matches the bills.
MILLAGE_YEAR: int = TAX_YEAR
MILLAGE_CSV_DEFAULT: str = '../projects/tax-per-acre/millage.csv'
### Fallback only when the millage table is missing: label a district with a municipality when
### this share of its lots lies inside that municipality's limits (or outside all of them).
DISTRICT_LABEL_SHARE: float = 0.95


def _millage_city_names() -> Optional[dict[str, str]]:
    """{DIST: city name or ''} from the millage table for MILLAGE_YEAR (else its latest year)."""
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    table = get_tables()['millage']
    with get_tables()['conn'].engine.connect() as connection:
        exists = connection.execute(sqlalchemy.text('SELECT to_regclass(:t)'), {'t': f'"{table}"'}).scalar()
        if not exists:
            return None
        rows = connection.execute(sqlalchemy.text(f"""
            SELECT dist, COALESCE(city_name, '') AS city_name FROM "{table}"
            WHERE year = COALESCE(
                (SELECT MAX(year) FROM "{table}" WHERE year = :year), (SELECT MAX(year) FROM "{table}")
            )
        """), {'year': MILLAGE_YEAR}).mappings().fetchall()
    return {str(r['dist']).strip(): str(r['city_name']).strip() for r in rows} or None


def fetch_millage(pipe: mrsm.Pipe, debug: bool = False) -> 'pd.DataFrame':
    """The parsed millage sheets (projects/tax-per-acre/millage.csv), one row per year and DIST."""
    pd = mrsm.attempt_import('pandas', lazy=False)
    path = _data_path('millage_csv', MILLAGE_CSV_DEFAULT)
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}; run scripts/taxmap_parse_millage.py.")
    df = pd.read_csv(path, dtype={'dist': str})
    df['dist'] = df['dist'].str.strip()
    for col in ('school_name', 'fire_name', 'other_name', 'sewer_name', 'city_name', 'sanitation_name'):
        if col in df:
            df[col] = df[col].astype('string').str.strip()
    return df


def _num(value: Any) -> Optional[float]:
    return None if value is None else float(value)


@functools.lru_cache(maxsize=4)
def _district_labels(bucket: int) -> dict[str, dict[str, Any]]:
    """
    For each tax district (DIST): its label from the millage table (`label_source` 'millage':
    the city, or 'Unincorporated' when it levies no city millage; codes not in the sheet stay
    unlabeled), plus the municipality most of its lots lie in by municipal limits and that share,
    as a cross-check. Without the millage table, labels fall back to that spatial share
    (`label_source` 'spatial', at least DISTRICT_LABEL_SHARE).
    """
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    tables = get_tables()
    sql = f"""
        WITH c AS (
            SELECT COALESCE(tax_district, '(none)') AS d, COALESCE(municipality, '(none)') AS m,
                   COUNT(*) AS n, SUM(tax) AS tax
            FROM "{tables['lots']}" GROUP BY 1, 2
        ), t AS (SELECT d, SUM(n) AS total FROM c GROUP BY d)
        SELECT DISTINCT ON (c.d) c.d, c.m, c.n::float / t.total AS share, t.total
        FROM c JOIN t USING (d)
        ORDER BY c.d, c.n DESC
    """
    with tables['conn'].engine.connect() as connection:
        rows = connection.execute(sqlalchemy.text(sql)).mappings().fetchall()
    try:
        cities = _millage_city_names()
    except Exception as e:
        warn(f"taxmap: could not read the millage table: {e}", stack=False)
        cities = None
    out = {}
    for r in rows:
        dist, share, name = str(r['d']), float(r['share']), str(r['m'])
        label, source = None, None
        if cities is not None:
            if dist in cities:
                label, source = (cities[dist] or 'Unincorporated'), 'millage'
        elif share >= DISTRICT_LABEL_SHARE and name != '(none)':
            label, source = ('Unincorporated' if name == UNINCORPORATED else name.title()), 'spatial'
        out[dist] = {
            'municipality': name, 'share': share, 'lots': int(r['total']),
            'label': label, 'label_source': source,
        }
    return out


def district_labels() -> dict[str, dict[str, Any]]:
    return _district_labels(int(time.time() // DATA_VERSION_SECONDS))


@functools.lru_cache(maxsize=64)
def _breakdown(by: str, filters_key: str, bucket: int) -> dict[str, Any]:
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    spec = BREAKDOWNS[by]
    filters = json.loads(filters_key)
    tables = get_tables()
    where, params = [], {}
    for i, (col, value) in enumerate(sorted(filters.items())):
        where.append(f"{BREAKDOWN_FILTERS[col]} = :f{i}")
        params[f"f{i}"] = value
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    detail = spec.get('detail', 'NULL')
    sql = f"""
        SELECT {spec['key']} AS key, {detail} AS detail,
               COUNT(*) AS lots, SUM(parcels) AS parcels, SUM(tax) AS tax, SUM(acres) AS acres
        FROM "{tables['lots']}" {zone_family_join()} {where_sql}
        GROUP BY 1 ORDER BY SUM(tax) DESC NULLS LAST
    """
    with tables['conn'].engine.connect() as connection:
        rows = connection.execute(sqlalchemy.text(sql), params).mappings().fetchall()
        county = connection.execute(sqlalchemy.text(
            f'SELECT COUNT(*) AS lots, SUM(tax) AS tax, SUM(acres) AS acres, '
            f'COUNT(*) FILTER (WHERE zone IS NOT NULL) AS zoned_lots, '
            f'SUM(tax) FILTER (WHERE zone IS NOT NULL) AS zoned_tax FROM "{tables["lots"]}"'
        )).mappings().fetchone()
    total_tax = sum(float(r['tax'] or 0) for r in rows)
    total_acres = sum(float(r['acres'] or 0) for r in rows)
    labels = district_labels() if by == 'tax_district' else {}
    out_rows = []
    for r in rows:
        tax, acres = float(r['tax'] or 0), float(r['acres'] or 0)
        detail_value = r['detail']
        if by == 'tax_district':
            info_ = labels.get(str(r['key']), {})
            detail_value = info_.get('label')
        out_rows.append({
            'key': str(r['key']),
            'detail': detail_value,
            'lots': int(r['lots']),
            'parcels': int(r['parcels'] or 0),
            'tax': tax,
            'acres': acres,
            'tax_per_acre': (tax / acres) if acres else None,
            'tax_share': (tax / total_tax) if total_tax else None,
            'acre_share': (acres / total_acres) if total_acres else None,
        })
    return {
        'by': by,
        'title': spec['title'],
        'filters': filters,
        'rows': out_rows,
        'total': {
            'lots': sum(r['lots'] for r in out_rows),
            'parcels': sum(r['parcels'] for r in out_rows),
            'tax': total_tax,
            'acres': total_acres,
            'tax_per_acre': (total_tax / total_acres) if total_acres else None,
        },
        'county': {
            'lots': int(county['lots']),
            'tax': _num(county['tax']),
            'acres': _num(county['acres']),
            'zoned_lots': int(county['zoned_lots']),
            'zoned_tax': _num(county['zoned_tax']),
        },
    }


def breakdown(by: str, filters: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """
    Revenue, acreage and revenue per acre of the lots grouped by `by` (a key of BREAKDOWNS),
    optionally within `filters` ({column: value}, columns from BREAKDOWN_FILTERS).
    """
    if by not in BREAKDOWNS:
        raise ValueError(f"Unknown breakdown '{by}'.")
    filters = {k: str(v) for k, v in (filters or {}).items() if v not in (None, '')}
    for col in filters:
        if col not in BREAKDOWN_FILTERS:
            raise ValueError(f"Cannot filter on '{col}'.")
    return _breakdown(by, json.dumps(filters, sort_keys=True), int(time.time() // DATA_VERSION_SECONDS))


@functools.lru_cache(maxsize=2)
def _srt_geojson(bucket: int) -> str:
    """The Swamp Rabbit Trail overlay as one GeoJSON FeatureCollection (a few small rows)."""
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    settings = get_settings()
    pipe = mrsm.Pipe('sql:bwg', 'srt', settings['location'], instance=settings['instance'])
    sql = f"""
        SELECT json_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(json_agg(json_build_object(
                'type', 'Feature',
                'properties', json_build_object(
                    'section', section, 'kind', kind, 'miles', ROUND(miles::numeric, 1)
                ),
                'geometry', ST_AsGeoJSON(ST_SimplifyPreserveTopology(geometry, 0.00001), 6)::json
            )), '[]'::json)
        )::text
        FROM "{pipe.target}"
    """
    with pipe.instance_connector.engine.connect() as connection:
        return connection.execute(sqlalchemy.text(sql)).scalar()


def srt_geojson() -> str:
    return _srt_geojson(int(time.time() // DATA_VERSION_SECONDS))


@api_plugin
def init_api(app):
    """Tiles, popup details, and the standalone map."""
    from fastapi import Response
    from fastapi.responses import HTMLResponse, JSONResponse

    @app.get('/taxmap/tiles/{view}/{z}/{x}/{y}.pbf', tags=['taxmap'], include_in_schema=False)
    def taxmap_tile(view: str, z: int, x: int, y: int) -> Response:
        if view not in VIEWS or not (0 <= z <= 22) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
            return Response(status_code=404)
        try:
            data = _tile(view, z, x, y, data_version())
        except Exception as e:
            warn(f"taxmap: tile {view}/{z}/{x}/{y} failed: {e}", stack=False)
            return Response(status_code=500)
        return Response(
            data,
            media_type='application/vnd.mapbox-vector-tile',
            headers={'Cache-Control': 'public, max-age=86400'},
        )

    @app.get('/taxmap/feature/{view}/{feature_id}', tags=['taxmap'], include_in_schema=False)
    def taxmap_feature(view: str, feature_id: str) -> Response:
        if view not in VIEWS or not re.fullmatch(r'[A-Za-z0-9]{1,20}', feature_id):
            return JSONResponse({'error': 'not found'}, status_code=404)
        details = feature_details(view, feature_id)
        if details is None:
            return JSONResponse({'error': 'not found'}, status_code=404)
        return JSONResponse(details, headers={'Cache-Control': 'public, max-age=3600'})

    @app.get('/taxmap/breakdown/{by}', tags=['taxmap'], include_in_schema=False)
    def taxmap_breakdown(
        by: str,
        proptype: Optional[str] = None,
        landuse: Optional[str] = None,
        municipality: Optional[str] = None,
        zone: Optional[str] = None,
        zone_jurisdiction: Optional[str] = None,
        tax_district: Optional[str] = None,
    ) -> Response:
        if by not in BREAKDOWNS:
            return JSONResponse({'error': 'not found'}, status_code=404)
        filters = {
            'proptype': proptype, 'landuse': landuse, 'municipality': municipality,
            'zone': zone, 'zone_jurisdiction': zone_jurisdiction, 'tax_district': tax_district,
        }
        try:
            data = breakdown(by, filters)
        except Exception as e:
            warn(f"taxmap: breakdown {by} failed: {e}", stack=False)
            return JSONResponse({'error': 'unavailable'}, status_code=500)
        return JSONResponse(data, headers={'Cache-Control': 'public, max-age=600'})

    @app.get('/taxmap/equivalent/{view}/{feature_id}', tags=['taxmap'], include_in_schema=False)
    def taxmap_equivalent(view: str, feature_id: str) -> Response:
        if view not in VIEWS or not re.fullmatch(r'[A-Za-z0-9]{1,20}', feature_id):
            return JSONResponse({'error': 'not found'}, status_code=404)
        try:
            data = equivalent_houses(view, feature_id)
        except Exception as e:
            warn(f"taxmap: equivalent houses for {view}/{feature_id} failed: {e}", stack=False)
            return JSONResponse({'error': 'unavailable'}, status_code=500)
        if data is None:
            return JSONResponse({'error': 'not found'}, status_code=404)
        return JSONResponse(data, headers={'Cache-Control': 'public, max-age=3600'})

    @app.get('/taxmap/srt.geojson', tags=['taxmap'], include_in_schema=False)
    def taxmap_srt() -> Response:
        try:
            data = srt_geojson()
        except Exception as e:
            warn(f"taxmap: srt overlay failed: {e}", stack=False)
            return JSONResponse({'error': 'unavailable'}, status_code=500)
        return Response(
            data,
            media_type='application/geo+json',
            headers={'Cache-Control': 'public, max-age=86400'},
        )

    @app.get('/taxmap/map', tags=['taxmap'], include_in_schema=False)
    def taxmap_map(view: str = 'lots') -> HTMLResponse:
        return HTMLResponse(render_map_html(view if view in VIEWS else 'lots'))


#########################
# Standalone map        #
#########################

### Sequential amber ramp for the dark basemap: low tax per acre recedes, high glows.
### Stops are log-spaced dollars per acre.
COLOR_STOPS: list[tuple[int, str]] = [
    (0, '#2e2622'),
    (2_500, '#3d2c24'),
    (5_000, '#4f3226'),
    (10_000, '#683a28'),
    (25_000, '#8f4528'),
    (50_000, '#c0522a'),
    (100_000, '#e2652f'),
    (250_000, '#f39a63'),
    (500_000, '#f9c49c'),
    (1_000_000, '#fff2e6'),
]


MAP_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tax per Acre</title>
<link rel="stylesheet" href="%(css)s">
<script src="%(js)s"></script>
<style>
  :root { --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #898781; --line: #2c2c2a; --accent: #d95926; }
  html, body { margin: 0; height: 100%%; background: #0d0d0d; color: var(--ink);
    font: 14px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  #map { position: absolute; inset: 0; }
  .panel { position: absolute; z-index: 2; background: rgba(26,26,25,0.92); border: 1px solid var(--line);
    border-radius: 8px; padding: 10px 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.4); }
  #controls { top: 10px; left: 10px; max-width: 290px; }
  #controls h1 { font-size: 15px; margin: 0 0 2px; }
  #controls .sub { color: var(--ink2); font-size: 12px; margin-bottom: 8px; }
  .seg { display: flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; margin-bottom: 6px; }
  .seg button { flex: 1; background: transparent; color: var(--ink2); border: 0; padding: 6px 8px; cursor: pointer; font: inherit; font-size: 12.5px; }
  .seg button.on { background: #3a2c24; color: var(--ink); box-shadow: inset 0 -2px 0 var(--accent); }
  .seg button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .hint { color: var(--muted); font-size: 11.5px; }
  label.row { display: flex; align-items: center; gap: 8px; color: var(--ink2); font-size: 12px; margin-top: 8px; }
  label.row input { flex: 1; accent-color: var(--accent); }
  label.row select { flex: 1; min-width: 0; background: #232321; color: var(--ink); border: 1px solid var(--line);
    border-radius: 6px; padding: 4px 6px; font: inherit; font-size: 12.5px; }
  label.row select:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  label.row .lbl { width: 42px; }
  .srt-key { display: inline-block; width: 18px; height: 4px; border-radius: 2px; background: %(srt_color)s;
    box-shadow: 0 0 0 1.5px #0d0d0d; margin-right: 2px; }
  #filter-stats { margin-top: 6px; color: var(--ink2); font-variant-numeric: tabular-nums; }
  .equiv-key { display: inline-block; width: 10px; height: 10px; border-radius: 2px; background: #f2d16b; margin-right: 2px; }
  #legend .cats { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 2px 10px; margin-top: 6px; font-size: 12px; color: var(--ink2); }
  #legend .cats label { display: flex; align-items: center; gap: 5px; cursor: pointer; line-height: 1.25; }
  #legend .cats input { margin: 0; accent-color: var(--accent); }
  #legend .cats .sw { width: 10px; height: 10px; border-radius: 2px; background: var(--c); flex: none; }
  #legend .cats label.off { color: var(--muted); }
  #legend .cats label.off .sw { opacity: 0.3; }
  #legend .cats-actions { grid-column: 1 / -1; color: var(--muted); font-size: 11px; margin-top: 2px; }
  #legend .cats-actions button { background: none; border: 0; color: var(--ink2); padding: 0; font: inherit; cursor: pointer; text-decoration: underline; }
  .pop a { color: #f39a63; }
  .pop a:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .pop .report { margin-top: 5px; }
  .pop .cmp { margin-top: 6px; border-top: 1px solid var(--line); padding-top: 5px; color: var(--ink2); }
  .pop .cmp b { color: var(--ink); font-variant-numeric: tabular-nums; }
  #legend { bottom: 28px; left: 10px; width: 290px; }
  #legend .ramp { height: 10px; border-radius: 3px; margin: 6px 0 3px;
    background: linear-gradient(90deg, %(gradient)s); }
  #legend .ticks { display: flex; justify-content: space-between; color: var(--muted); font-size: 11px; }
  #legend .title { color: var(--ink2); font-size: 12px; }
  .pop h3 { margin: 0 0 2px; font-size: 13.5px; }
  .pop .big { font-size: 18px; font-weight: 600; margin: 4px 0 2px; font-variant-numeric: tabular-nums; }
  .pop table { border-collapse: collapse; width: 100%%; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .pop td { padding: 1px 0; color: var(--ink2); }
  .pop td.v { text-align: right; color: var(--ink); }
  .pop .members { margin-top: 6px; border-top: 1px solid var(--line); padding-top: 4px; color: var(--muted); }
  .pop .note { color: var(--muted); margin-top: 6px; }
  /* Lot details: docked beside the map (right) or as a bottom sheet on phones, never over the
     highlighted houses; the camera moves them into the uncovered part of the map. */
  #info { top: 10px; right: 50px; width: 320px; max-height: calc(100%% - 20px); overflow-y: auto;
    z-index: 3; font-size: 12.5px; padding-top: 8px; overscroll-behavior: contain; }
  #info[hidden] { display: none; }
  #info-close { position: sticky; top: 0; float: right; margin: -4px -6px 0 6px; background: #232321; color: var(--ink2);
    border: 1px solid var(--line); border-radius: 6px; width: 28px; height: 28px; font-size: 16px; line-height: 1; cursor: pointer; }
  #info-close:focus-visible, #controls-toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  #controls .hd { display: flex; align-items: flex-start; gap: 8px; }
  #controls .hd h1 { flex: 1; }
  #controls-toggle { background: #232321; color: var(--ink2); border: 1px solid var(--line); border-radius: 6px;
    font: inherit; font-size: 11.5px; padding: 2px 8px; cursor: pointer; white-space: nowrap; }
  #controls.collapsed .body { display: none; }
  #controls.collapsed .sub { margin-bottom: 0; }
  @media (max-width: 600px) {
    #controls { max-width: calc(100%% - 60px); }
    #legend { width: 210px; bottom: 34px; }
    #info { top: auto; left: 0; right: 0; bottom: 0; width: auto; max-height: 45%%; border-radius: 12px 12px 0 0;
      padding: 8px 14px 14px; background: var(--surface); }
  }
</style>
</head>
<body>
<div id="map" role="region" aria-label="3D map of property tax per acre"></div>
<div id="controls" class="panel">
  <div class="hd"><h1>Property tax per acre</h1>
    <button type="button" id="controls-toggle" aria-expanded="true" aria-controls="controls-body">Hide</button></div>
  <div class="sub">Greenville County · %(tax_year)s tax bills</div>
  <div class="body" id="controls-body">
  <div class="hint" id="zoom-hint" style="margin-top:4px;color:#c3c2b7"></div>
  <label class="row"><span class="lbl">Height</span> <input id="height" type="range" min="-2" max="2" step="0.25" value="0" aria-label="Height exaggeration"><span id="height-label">1×</span></label>
  <label class="row"><span class="lbl">Color</span> <select id="colorby" aria-label="Color by">
    <option value="tpa">Tax per acre</option><option value="uc">Use class</option>
    <option value="zf">Zoning family</option></select></label>
  <label class="row"><span class="lbl">Show</span> <select id="filter" aria-label="Show only"><option value="">Everything</option></select></label>
  <div class="hint" id="filter-stats" aria-live="polite"></div>
  <label class="row"><input id="srt-toggle" type="checkbox" checked style="flex:0"> <span class="srt-key"></span>Swamp Rabbit Trail</label>
  <label class="row"><input id="equiv-toggle" type="checkbox" checked style="flex:0"> <span class="equiv-key"></span>Equal-tax houses on click</label>
  </div>
</div>
<div id="info" class="panel" hidden role="dialog" aria-label="Lot details">
  <button type="button" id="info-close" aria-label="Close details">×</button>
  <div id="info-body"></div>
</div>
<div id="legend" class="panel">
  <div class="title" id="legend-title">Tax per acre (height is linear)</div>
  <div id="legend-ramp"><div class="ramp"></div>
  <div class="ticks"><span>$0</span><span>$10k</span><span>$50k</span><span>$250k</span><span>$1M+</span></div></div>
  <div class="cats" id="legend-cats" hidden></div>
</div>
<script>
const COLOR_STOPS = %(color_stops)s;
// Categorical modes: tile property -> [[class, color], ...]. Classes sharing a color are a family;
// the legend checkboxes hide and show each class.
const CATEGORIES = %(categories)s;
const CAT_TITLES = { uc: 'Use class', zf: 'Zoning family' };
const HIGHLIGHT = '#f2d16b';     // houses paying the same tax as the clicked lot
const SRT_COLOR = %(srt_color_js)s;  // muted green: a reference line, not data
// Greenville County GIS property report for one PIN (owner, deed, land-use name, values).
const GCGIS_REPORT = 'https://www.gcgis.org/Apps/Property_Report_NJ/Default.aspx?pin=';
const pinLink = (pin) => '<a href="' + GCGIS_REPORT + encodeURIComponent(pin) + '" target="_blank" rel="noopener"'
  + ' title="Open the county property report in a new tab">' + esc(pin) + '</a>';
const NO_ZONE = %(no_zone)s;
const VERSION = %(version)s;
const GRID_MAX_ZOOM = %(grid_max_zoom)s;
const BASE_HEIGHT = 0.00025;  // meters of height per dollar of tax per acre ($1M/acre = 250 m)
const params = new URLSearchParams(location.search);
const view = 'lots';
let heightScale = 1;
let colorBy = { pt: 'uc', uc: 'uc', zf: 'zf' }[params.get('color')] || 'tpa';
let filter = params.get('filter') || '';
const LOTS_ONLY = ['mu', 'zn', 'zf'];  // zone and municipality are assigned per lot
let equivIds = [];               // ids shown in the equal-tax highlight (see showEquivalent())
// Classes hidden with the legend checkboxes, per mode (kept in the URL as ?hide=uc:Industrial,...).
const hidden = { uc: new Set(), zf: new Set() };
for (const item of (params.get('hide') || '').split(',')) {
  const [k, ...rest] = item.split(':');
  if (hidden[k] && rest.length) { hidden[k].add(rest.join(':')); }
}
const stats = {};                // 'key:value' -> breakdown row, for the filter summary

const map = new maplibregl.Map({
  container: 'map',
  style: %(style)s,
  center: [-82.3990, 34.8502],
  zoom: 14.2,
  pitch: 62,
  bearing: -28,
  maxPitch: 80,
  hash: true,
  attributionControl: { compact: true, customAttribution: 'Parcels &amp; tax: Greenville County GIS' }
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
if (document.fullscreenEnabled) {
  // The whole page goes full screen, so the panels come along.
  map.addControl(new maplibregl.FullscreenControl({ container: document.body }), 'top-right');
} else if (window.self !== window.top) {
  // Embedded without allow="fullscreen": open the map on its own instead.
  map.addControl({
    onAdd() {
      const box = document.createElement('div');
      box.className = 'maplibregl-ctrl maplibregl-ctrl-group';
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = '⤢'; b.style.fontSize = '17px';
      b.title = 'Open the map full screen in a new tab'; b.setAttribute('aria-label', b.title);
      b.addEventListener('click', () => window.open(location.href, '_blank', 'noopener'));
      box.appendChild(b);
      return box;
    },
    onRemove() {},
  }, 'top-right');
}

// The control panel folds away (folded from the start on small screens) to free the map.
const controlsEl = document.getElementById('controls');
function setControlsOpen(open) {
  controlsEl.classList.toggle('collapsed', !open);
  const b = document.getElementById('controls-toggle');
  b.textContent = open ? 'Hide' : 'Controls';
  b.setAttribute('aria-expanded', open ? 'true' : 'false');
}
setControlsOpen(!(window.innerWidth <= 600 || window.innerHeight <= 520));
document.getElementById('controls-toggle').addEventListener('click',
  () => setControlsOpen(controlsEl.classList.contains('collapsed')));

function rampExpr() {
  const expr = ['interpolate', ['linear'], ['get', 'tpa']];
  for (const [value, color] of COLOR_STOPS) { expr.push(value, color); }
  return expr;
}
const isCat = () => colorBy in CATEGORIES;
function colorExpr() {
  if (!isCat()) { return rampExpr(); }
  const match = ['match', ['get', colorBy]];
  for (const [value, color] of CATEGORIES[colorBy]) { match.push(value, color); }
  match.push('#6b6a64');
  // The zoomed-out grid mixes classes, so it keeps the tax-per-acre ramp.
  return ['case', ['has', 'grid'], rampExpr(), match];
}
function splitFilter(f) {
  const i = f.indexOf(':');
  return i < 0 ? [null, null] : [f.slice(0, i), f.slice(i + 1)];
}
function filterExpr() {
  const parts = [];
  const [key, value] = splitFilter(filter);
  if (key) { parts.push(['==', ['get', key], value]); }  // grid cells have no categories, so they hide
  // Lots drawn in the equal-tax highlight layers are left out here, so they don't overlap.
  if (equivIds.length) { parts.push(['!', ['in', ['get', 'id'], ['literal', equivIds]]]); }
  if (isCat() && hidden[colorBy].size) {
    // Grid cells have no class; keep them unless filtering.
    parts.push(['any', ['has', 'grid'], ['!', ['in', ['get', colorBy], ['literal', [...hidden[colorBy]]]]]]);
  }
  return parts.length === 0 ? null : parts.length === 1 ? parts[0] : ['all', ...parts];
}
function renderLegendCats() {
  const el = document.getElementById('legend-cats');
  if (!isCat()) { el.innerHTML = ''; return; }
  el.innerHTML = CATEGORIES[colorBy].map(([cls, color]) => {
    const on = !hidden[colorBy].has(cls);
    return '<label class="' + (on ? '' : 'off') + '"><input type="checkbox" data-cls="' + esc(cls) + '"'
      + (on ? ' checked' : '') + '><span class="sw" style="--c:' + color + '"></span>' + esc(cls) + '</label>';
  }).join('') + '<div class="cats-actions">Same color = same family. <button type="button" data-act="all">Show all</button>'
    + ' · <button type="button" data-act="none">Hide all</button></div>';
}
document.getElementById('legend-cats').addEventListener('change', (e) => {
  const cls = e.target.dataset.cls;
  if (!cls) { return; }
  if (e.target.checked) { hidden[colorBy].delete(cls); } else { hidden[colorBy].add(cls); }
  applyStyle();
});
document.getElementById('legend-cats').addEventListener('click', (e) => {
  const act = e.target.dataset && e.target.dataset.act;
  if (!act) { return; }
  hidden[colorBy] = act === 'all' ? new Set() : new Set(CATEGORIES[colorBy].map(([c]) => c));
  applyStyle();
});
function applyStyle() {
  for (const id of ['lots']) {
    if (!map.getLayer(id)) { continue; }
    map.setPaintProperty(id, 'fill-extrusion-color', colorExpr());
    map.setFilter(id, filterExpr());
  }
  document.getElementById('legend-ramp').hidden = isCat();
  document.getElementById('legend-cats').hidden = !isCat();
  document.getElementById('legend-title').textContent = isCat()
    ? CAT_TITLES[colorBy] + ' (height is tax per acre)' : 'Tax per acre (height is linear)';
  renderLegendCats();
  const url = new URL(location.href);
  if (isCat()) { url.searchParams.set('color', colorBy); } else { url.searchParams.delete('color'); }
  if (filter) { url.searchParams.set('filter', filter); } else { url.searchParams.delete('filter'); }
  const hide = Object.entries(hidden).flatMap(([k, s]) => [...s].map((c) => k + ':' + c));
  if (hide.length) { url.searchParams.set('hide', hide.join(',')); } else { url.searchParams.delete('hide'); }
  history.replaceState(null, '', url);
  updateFilterStats();
  updateZoomHint();
}
// Zoomed out, area-averaged grid cells are short, so heights are exaggerated there
// and ease to true scale by zoom 13.
const ZOOM_EXAGGERATION = [[9, 100], [10, 60], [11, 24], [12, 6], [13, 1]];
function exaggeration(z) {
  const s = ZOOM_EXAGGERATION;
  if (z <= s[0][0]) { return s[0][1]; }
  for (let i = 1; i < s.length; i++) {
    if (z <= s[i][0]) {
      const [z0, f0] = s[i - 1], [z1, f1] = s[i];
      return f0 + (f1 - f0) * (z - z0) / (z1 - z0);
    }
  }
  return 1;
}
function heightExpr() {
  const expr = ['interpolate', ['linear'], ['zoom']];
  for (const [z, f] of ZOOM_EXAGGERATION) { expr.push(z, ['*', ['get', 'tpa'], BASE_HEIGHT * heightScale * f]); }
  return expr;
}

map.on('load', () => {
  // The basemap's own 3D buildings would compete with the data.
  for (const layer of map.getStyle().layers) {
    if (layer.type === 'fill-extrusion') { map.setLayoutProperty(layer.id, 'visibility', 'none'); }
  }
  for (const v of ['lots']) {
    map.addSource(v, {
      type: 'vector',
      tiles: [location.origin + '/taxmap/tiles/' + v + '/{z}/{x}/{y}.pbf?v=' + VERSION],
      minzoom: 9, maxzoom: 16,
    });
    map.addLayer({
      id: v, type: 'fill-extrusion', source: v, 'source-layer': v,
      layout: { visibility: v === view ? 'visible' : 'none' },
      filter: filterExpr() || ['literal', true],
      paint: {
        'fill-extrusion-color': colorExpr(),
        'fill-extrusion-height': heightExpr(),
        'fill-extrusion-base': 0,
        'fill-extrusion-opacity': 0.92,
        'fill-extrusion-vertical-gradient': true,
      },
    });
    map.on('click', v, (e) => showPopup(v, e));
    map.on('mouseenter', v, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', v, () => { map.getCanvas().style.cursor = ''; });
  }
  // Swamp Rabbit Trail overlay, added after the extrusions so it draws on top of them.
  // It ignores the "Show" filter: it is a reference line, not tax data.
  map.addSource('srt', { type: 'geojson', data: location.origin + '/taxmap/srt.geojson?v=' + VERSION });
  const srtVis = srtOn ? 'visible' : 'none';
  map.addLayer({
    id: 'srt-casing', type: 'line', source: 'srt', layout: { 'line-cap': 'round', 'line-join': 'round', visibility: srtVis },
    paint: { 'line-color': '#0d0d0d', 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 3, 16, 8] },
  });
  // No click handler: clicks fall through to the lot or parcel underneath.
  const srtWidth = ['interpolate', ['linear'], ['zoom'], 9, 1.5, 16, 4];
  map.addLayer({
    id: 'srt', type: 'line', source: 'srt', filter: ['!=', ['get', 'kind'], 'access'],
    layout: { 'line-cap': 'round', 'line-join': 'round', visibility: srtVis },
    paint: { 'line-color': SRT_COLOR, 'line-width': srtWidth },
  });
  map.addLayer({
    id: 'srt-access', type: 'line', source: 'srt', filter: ['==', ['get', 'kind'], 'access'],
    layout: { 'line-join': 'round', visibility: srtVis },
    paint: { 'line-color': SRT_COLOR, 'line-width': srtWidth, 'line-dasharray': [1.5, 1.5] },
  });
  // Houses paying the same tax as the clicked lot (filled on click; see showEquivalent()).
  map.addSource('equiv', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'equiv', type: 'fill-extrusion', source: 'equiv', filter: ['!', ['has', 'target']],
    paint: { 'fill-extrusion-color': HIGHLIGHT, 'fill-extrusion-height': heightExpr(), 'fill-extrusion-opacity': 0.95 },
  }, 'srt-casing');
  map.addLayer({
    id: 'equiv-target', type: 'fill-extrusion', source: 'equiv', filter: ['has', 'target'],
    paint: { 'fill-extrusion-color': '#f0efec', 'fill-extrusion-height': heightExpr(), 'fill-extrusion-opacity': 0.95 },
  }, 'srt-casing');
  for (const lid of ['equiv', 'equiv-target']) {
    map.on('click', lid, (e) => showPopup(view, e));
    map.on('mouseenter', lid, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', lid, () => { map.getCanvas().style.cursor = ''; });
  }
  setView(view);
  applyStyle();
});

// Dims every lot while the equal-tax houses are shown (extrusion opacity is per layer, so the
// highlighted houses and the clicked lot are drawn again at full opacity in their own layers).
let equivOn = new URL(location.href).searchParams.get('equiv') !== '0';
let equivRequest = 0;
function setDimmed(ids) {
  equivIds = ids;
  for (const id of ['lots']) {
    if (!map.getLayer(id)) { continue; }
    map.setPaintProperty(id, 'fill-extrusion-opacity', ids.length ? 0.2 : 0.92);
    map.setFilter(id, filterExpr());
  }
}
function clearEquivalent() {
  equivRequest++;
  if (!map.getSource('equiv')) { return; }
  map.getSource('equiv').setData({ type: 'FeatureCollection', features: [] });
  if (equivIds.length) { setDimmed([]); }
}
async function showEquivalent(v, id, d) {
  clearEquivalent();
  if (!equivOn) { return; }
  const request = equivRequest;
  const el = () => document.getElementById('equiv-line');
  let e;
  try {
    const r = await fetch('/taxmap/equivalent/' + v + '/' + encodeURIComponent(id));
    e = await r.json();
  } catch (err) { if (el()) { el().textContent = ''; } return; }
  if (request !== equivRequest) { return; }  // another click or a close since
  if (!e || e.error || !e.houses) { if (el()) { el().textContent = 'No nearby single-family houses to compare.'; } return; }
  const target = e.target ? [{ ...e.target, properties: { ...e.target.properties, target: 1 } }] : [];
  const features = e.geojson.features.concat(target);
  map.getSource('equiv').setData({ type: 'FeatureCollection', features });
  setDimmed(features.map((f) => String(f.properties.id)));
  fitToFeatures(features);
  if (el()) {
    const times = e.target_acres ? e.acres / e.target_acres : null;
    const miles = (e.radius_m || 0) / 1609.34;
    el().innerHTML = '<span class="equiv-key"></span>Same tax as the <b>' + e.houses.toLocaleString('en-US')
      + '</b> nearest single-family houses' + (e.complete ? '' : ' (stopped at ' + e.houses.toLocaleString('en-US') + ')')
      + ', on <b>' + e.acres.toFixed(1) + ' acres</b>'
      + (times && times >= 1.5 ? ' (' + Math.round(times).toLocaleString('en-US') + '× this land)' : '')
      + ', within ' + (miles < 0.25 ? Math.round(e.radius_m * 3.28084).toLocaleString('en-US') + ' ft' : miles.toFixed(1) + ' mi') + '.';
  }
}
document.getElementById('equiv-toggle').checked = equivOn;
document.getElementById('equiv-toggle').addEventListener('change', (e) => {
  equivOn = e.target.checked;
  if (!equivOn) { clearEquivalent(); const el = document.getElementById('equiv-line'); if (el) { el.textContent = ''; } }
  const url = new URL(location.href);
  if (equivOn) { url.searchParams.delete('equiv'); } else { url.searchParams.set('equiv', '0'); }
  history.replaceState(null, '', url);
});

let srtOn = new URL(location.href).searchParams.get('srt') !== '0';
document.getElementById('srt-toggle').checked = srtOn;
document.getElementById('srt-toggle').addEventListener('change', (e) => {
  srtOn = e.target.checked;
  for (const id of ['srt-casing', 'srt', 'srt-access']) {
    if (map.getLayer(id)) { map.setLayoutProperty(id, 'visibility', srtOn ? 'visible' : 'none'); }
  }
  const url = new URL(location.href);
  if (srtOn) { url.searchParams.delete('srt'); } else { url.searchParams.set('srt', '0'); }
  history.replaceState(null, '', url);
});

function setView(v) { closeInfo(); }

function updateZoomHint() {
  const z = map.getZoom();
  const f = exaggeration(z);
  const zoomedOut = z < GRID_MAX_ZOOM + 1;
  let text = zoomedOut
    ? (z < 11 ? '~400 m' : z < 12 ? '~200 m' : '~100 m') + ' grid, heights ×' + (Math.round(f * 10) / 10)
      + '. Zoom in for lots.'
    : '';
  if (zoomedOut && filter) { text = 'Zoom in to see matching lots.'; }
  document.getElementById('zoom-hint').textContent = text;
}
map.on('zoom', updateZoomHint);
map.on('load', updateZoomHint);

document.getElementById('height').addEventListener('input', (e) => {
  heightScale = Math.pow(2, Number(e.target.value));
  document.getElementById('height-label').textContent = (heightScale >= 1 ? heightScale : heightScale.toFixed(2)) + '×';
  for (const id of ['lots', 'equiv', 'equiv-target']) {
    if (map.getLayer(id)) { map.setPaintProperty(id, 'fill-extrusion-height', heightExpr()); }
  }
});

const typeLabel = (s) => String(s || '').toLowerCase().replace(/(^|[\s-])\w/g, (c) => c.toUpperCase());
document.getElementById('colorby').value = colorBy;
document.getElementById('colorby').addEventListener('change', (e) => { colorBy = e.target.value; applyStyle(); });
document.getElementById('filter').addEventListener('change', (e) => {
  filter = e.target.value;
  applyStyle();
});
function updateFilterStats() {
  const el = document.getElementById('filter-stats');
  const r = stats[filter];
  if (!filter || !r) { el.textContent = ''; return; }
  el.textContent = money(r.tax) + ' tax · ' + Math.round(r.acres).toLocaleString('en-US') + ' ac · '
    + money(r.tax_per_acre) + '/ac · ' + r.lots.toLocaleString('en-US') + ' lots ('
    + (100 * r.tax_share).toFixed(1) + '%% of county tax)';
}
// Filter choices and their totals come from the same breakdowns as the page's table.
const FILTER_GROUPS = [
  ['uc', 'use_class', 'Use class', (r) => r.key],
  ['zf', 'zone_family', 'Zoning family', (r) => r.key],
  ['pt', 'proptype', 'Property type', (r) => typeLabel(r.key)],
  ['mu', 'municipality', 'Municipality', (r) => r.key === 'UNINCORPORATED' ? 'Unincorporated' : typeLabel(r.key)],
  ['zn', 'zone_code', 'Zoning code', (r) => r.key + ' · ' + r.lots.toLocaleString('en-US') + ' lots'],
  ['td', 'tax_district', 'Tax district', (r) => 'DIST ' + r.key + (r.detail ? ' · ' + r.detail : '')],
];
(async () => {
  const select = document.getElementById('filter');
  for (const [key, by, title, label] of FILTER_GROUPS) {
    let data;
    try { data = await (await fetch('/taxmap/breakdown/' + by)).json(); } catch (err) { continue; }
    if (!data || !data.rows) { continue; }
    const group = document.createElement('optgroup');
    group.label = title;
    group.dataset.key = key;
    const rows = by === 'zone_code' ? [...data.rows].sort((a, b) => a.key.localeCompare(b.key)) : data.rows;
    for (const r of rows) {
      const opt = document.createElement('option');
      opt.value = key + ':' + r.key;
      opt.textContent = label(r);
      group.appendChild(opt);
      stats[opt.value] = r;
    }
    select.appendChild(group);
  }
  if (filter) { select.value = filter; }
  updateFilterStats();
})();

const money = (v) => v === null || v === undefined ? '–' : '$' + Math.round(v).toLocaleString('en-US');
const acres = (v) => v === null || v === undefined ? '–' : (v < 0.1 ? v.toFixed(3) : v.toFixed(2)) + ' ac';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Lot details live in a docked panel (#info), not a popup at the click, so they never sit on top
// of the highlighted houses: the camera moves the lot and its houses into the uncovered area.
const infoEl = document.getElementById('info');
let infoId = null;
function openInfo(html, id) {
  infoId = id;
  document.getElementById('info-body').innerHTML = html;
  infoEl.hidden = false;
  infoEl.scrollTop = 0;
}
function closeInfo() {
  if (infoEl.hidden) { return; }
  infoEl.hidden = true;
  document.getElementById('info-body').innerHTML = '';
  infoId = null;
  clearEquivalent();
}
document.getElementById('info-close').addEventListener('click', closeInfo);
// A click on empty map (no lot, parcel or highlighted house) closes the details, as a popup would.
map.on('click', (e) => {
  const layers = ['lots', 'equiv', 'equiv-target'].filter((id) => map.getLayer(id));
  if (!map.queryRenderedFeatures(e.point, { layers }).length) { closeInfo(); }
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeInfo(); } });

// Map padding that keeps a fitted area clear of the panels (sides on wide screens; top and bottom
// on phones, where the controls sit on top and the details are a bottom sheet).
function overlayPadding() {
  const W = map.getContainer().clientWidth, H = map.getContainer().clientHeight;
  const pad = { top: 40, right: 40, bottom: 40, left: 40 };
  const narrow = W <= 600;
  if (!infoEl.hidden) {
    const r = infoEl.getBoundingClientRect();
    if (narrow) { pad.bottom += r.height; } else { pad.right += r.width + 50; }
  }
  const c = controlsEl.getBoundingClientRect();
  if (narrow) { pad.top += c.height; } else { pad.left += c.width + 10; }
  const fit = (a, b, size) => {  // leave at least 120 px of map between the panels
    const room = size - 120;
    if (pad[a] + pad[b] > room) { const k = room / (pad[a] + pad[b]); pad[a] *= k; pad[b] *= k; }
  };
  fit('left', 'right', W); fit('top', 'bottom', H);
  return pad;
}
function fitToFeatures(features) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const visit = (c) => {
    if (typeof c[0] === 'number') {
      x0 = Math.min(x0, c[0]); y0 = Math.min(y0, c[1]); x1 = Math.max(x1, c[0]); y1 = Math.max(y1, c[1]);
    } else { c.forEach(visit); }
  };
  for (const f of features) { if (f.geometry) { visit(f.geometry.coordinates); } }
  if (!isFinite(x0)) { return; }
  const cam = map.cameraForBounds([[x0, y0], [x1, y1]], { padding: overlayPadding(), bearing: map.getBearing() });
  if (!cam) { return; }
  // A tilted camera sees less of the far side than a top-down one, so back off a little.
  const zoom = Math.min(cam.zoom - (map.getPitch() > 30 ? 0.4 : 0), 17.5);
  map.easeTo({ center: cam.center, zoom, bearing: map.getBearing(), pitch: map.getPitch(), duration: 900 });
}

async function showPopup(v, e) {
  const f = e.features && e.features[0];
  if (!f) { return; }
  const id = f.properties.id;
  closeInfo();
  if (f.properties.grid) {
    openInfo(
      '<div class="pop"><h3>Grid cell</h3><div class="big">' + money(f.properties.tpa) + ' / acre</div>'
      + '<table><tr><td>Tax billed</td><td class="v">' + money(f.properties.tax) + '</td></tr>'
      + '<tr><td>Parcel land</td><td class="v">' + acres(f.properties.acres) + '</td></tr></table>'
      + '<div class="note">Area-weighted average of the lots in this cell. Zoom in to see each '
      + (v === 'lots' ? 'lot' : 'parcel') + '.</div></div>', id);
    return;
  }
  openInfo('<div class="pop"><div class="big">' + money(f.properties.tpa) + ' / acre</div><div class="note">Loading…</div></div>', id);
  let d;
  try {
    const r = await fetch('/taxmap/feature/' + v + '/' + encodeURIComponent(id));
    d = await r.json();
  } catch (err) { return; }
  if (!d || d.error || infoId !== id) { return; }  // failed, or another click since
  const title = esc(d.site_address || d.subdivision || d.descr || ('PIN ' + d.id));
  let html = '<div class="pop"><h3>' + title + '</h3>'
    + '<div class="big">' + money(d.tax_per_acre) + ' / acre</div>'
    + '<table>'
    + '<tr><td>Tax billed</td><td class="v">' + money(d.tax) + '</td></tr>'
    + '<tr><td>Land</td><td class="v">' + acres(d.acres) + '</td></tr>'
    + '<tr><td>Market value</td><td class="v">' + money(d.fair_market_value) + '</td></tr>'
    + '<tr><td>Type</td><td class="v">' + esc((d.proptype || '').toLowerCase()) + (d.landuse ? ' (' + esc(d.landuse) + ')' : '') + '</td></tr>'
    + (v === 'lots' ? '<tr><td>Zone</td><td class="v">' + esc(d.zone || NO_ZONE)
        + (d.zone && d.zone_jurisdiction ? ' · ' + esc(typeLabel(d.zone_jurisdiction)) : '')
        + (d.zone && d.zones > 1 && d.zone_share < 0.995 ? ' (' + Math.round(100 * d.zone_share) + '%% of lot)' : '') + '</td></tr>'
      + '<tr><td>Municipality</td><td class="v">' + esc(d.municipality === 'UNINCORPORATED' ? 'unincorporated' : typeLabel(d.municipality)) + '</td></tr>' : '')
    + '<tr><td>Tax district</td><td class="v">' + esc(d.tax_district || '–') + '</td></tr>'
    + '<tr><td>' + (v === 'lots' && d.parcels > 1 ? 'Main PIN' : 'PIN') + '</td><td class="v">' + pinLink(d.id) + '</td></tr>'
    + '</table>'
    + '<div class="report"><a href="' + GCGIS_REPORT + encodeURIComponent(d.id) + '" target="_blank" rel="noopener">'
    + 'County property report ↗</a></div>';
  if (v === 'lots' && d.parcels > 1) {
    html += '<div class="members">' + (d.parcels - 1) + ' unit parcel' + (d.parcels > 2 ? 's' : '') + ' stacked here';
    for (const m of (d.members || []).slice(0, 6)) {
      html += '<br>' + esc(m.descr || m.pin) + ' · ' + money(m.tax) + ' · ' + pinLink(m.pin);
    }
    if (d.parcels > 6) { html += '<br>…'; }
    html += '</div>';
  }
  html += compareHtml(d);
  html += '<div class="cmp" id="equiv-line">' + (equivOn ? 'Finding houses that pay the same tax…' : '') + '</div>';
  html += '</div>';
  openInfo(html, id);
  if (!equivOn) { document.getElementById('equiv-line').remove(); }
  showEquivalent(v, id, d);
}

const times = (x) => x === null || x === undefined ? '–'
  : x >= 10 ? Math.round(x).toLocaleString('en-US') + '×' : x >= 1 ? x.toFixed(1) + '×' : x.toFixed(2) + '×';
// Tax per acre as multiples of a Walmart acre and of the typical house in this lot's zone.
function compareHtml(d) {
  const c = d.compare;
  if (!c || !d.tax_per_acre) { return ''; }
  const h = c.house || {};
  const where = h.scope === 'zone'
    ? 'in ' + esc(h.zone) + ' (' + esc(typeLabel(h.jurisdiction)) + ' zoning, median of ' + h.n.toLocaleString('en-US') + ')'
    : 'countywide (median of ' + (h.n || 0).toLocaleString('en-US') + ')'
      + (d.zone && d.zone !== NO_ZONE ? '; ' + esc(d.zone) + ' has fewer than 25 houses' : '');
  let out = '<div class="cmp">Per acre, this pays <b>' + times(c.walmart_x) + '</b> a Walmart acre and <b>'
    + times(c.house_x) + '</b> a typical house ' + where + '.';
  if (h.scope === 'zone' && c.county_house_x) { out += ' Countywide: ' + times(c.county_house_x) + '.'; }
  return out + '</div>';
}
</script>
</body>
</html>
"""


def render_map_html(view: str) -> str:
    stops = COLOR_STOPS
    gradient = ', '.join(f"{color} {100 * i / (len(stops) - 1):.0f}%" for i, (_, color) in enumerate(stops))
    return MAP_HTML % {
        'css': MAPLIBRE_CSS_URL,
        'js': MAPLIBRE_JS_URL,
        'style': json.dumps(MAPLIBRE_STYLE_URL),
        'gradient': gradient,
        'color_stops': json.dumps([[value, color] for value, color in stops]),
        'categories': json.dumps({
            'uc': [list(row) for row in USE_CLASSES],
            'zf': [list(row) for row in ZONE_FAMILIES],
        }),
        'no_zone': json.dumps(NO_ZONE_LABEL),
        'tax_year': TAX_YEAR,
        'srt_color': SRT_COLOR,
        'srt_color_js': json.dumps(SRT_COLOR),
        'version': json.dumps(data_version()),
        'grid_max_zoom': GRID_MAX_ZOOM,
    }


#########################
# Meerschaum page       #
#########################

INK_PRIMARY: str = '#f0efec'
INK_SECONDARY: str = '#c3c2b7'
INK_MUTED: str = '#898781'
SURFACE: str = '#1a1a19'
HAIRLINE: str = '#2c2c2a'
SERIES_LAND: str = '#3987e5'
SERIES_TAX: str = '#d95926'


@functools.lru_cache(maxsize=2)
def _comparisons(bucket: int) -> dict[str, Any]:
    """Benchmarks and land-vs-revenue shares, from the lots and parcels tables."""
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    tables = get_tables()
    lots, parcels = tables['lots'], tables['parcels']
    with tables['conn'].engine.connect() as c:
        q = lambda sql: c.execute(sqlalchemy.text(sql)).mappings().fetchall()
        county = q(f'SELECT SUM(tax) tax, SUM(acres) acres FROM "{parcels}"')[0]
        densest = q(f"""
            SELECT COALESCE(site_address, descr) AS label, tax_per_acre, acres FROM "{lots}"
            WHERE acres >= 0.5 ORDER BY tax_per_acre DESC NULLS LAST LIMIT 1""")
        mall = q(f"""
            SELECT l.tax_per_acre, l.acres FROM "{lots}" l JOIN "{parcels}" p ON p.pin = l.lot_id
            WHERE p.owner ILIKE 'SIMON HAYWOOD%' ORDER BY l.tax DESC LIMIT 1""")
        walmart = q(f"""
            SELECT SUM(l.tax) / SUM(l.acres) AS tax_per_acre, COUNT(*) AS n FROM "{lots}" l
            JOIN "{parcels}" p ON p.pin = l.lot_id
            WHERE p.owner ILIKE 'WAL%MART%' AND l.acres >= 10""")
        downtown = q(f"""
            WITH dt AS (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(-82.3990, 34.8502), 4326), 3857) g)
            SELECT SUM(l.tax) / SUM(l.acres) AS tax_per_acre, SUM(l.acres) acres FROM "{lots}" l, dt
            WHERE ST_DWithin(l.geometry, dt.g, 600)""")
        home = q(f"""
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY tax_per_acre) AS tax_per_acre FROM "{lots}"
            WHERE landuse = '1100' AND acres BETWEEN 0.1 AND 2 AND stacked = 0""")
        shares = q(f"""
            SELECT COALESCE(proptype, 'OTHER') AS proptype, SUM(acres) acres, SUM(tax) tax
            FROM "{parcels}" GROUP BY 1 ORDER BY SUM(tax) DESC""")

    bench = []
    if densest:
        label = str(densest[0]['label'] or 'downtown').title()
        bench.append((f"Densest lot over ½ acre ({label})", densest[0]['tax_per_acre']))
    if downtown and downtown[0]['tax_per_acre']:
        bench.append(('Downtown core (lots within ~⅓ mile of Main & Washington)', downtown[0]['tax_per_acre']))
    if mall:
        bench.append(('Haywood Mall (main mall lot)', mall[0]['tax_per_acre']))
    if walmart and walmart[0]['tax_per_acre']:
        bench.append((f"Walmart stores ({walmart[0]['n']} lots over 10 acres, combined)", walmart[0]['tax_per_acre']))
    if home and home[0]['tax_per_acre']:
        bench.append(('Typical single-family lot (median)', home[0]['tax_per_acre']))
    bench.append(('County average (all parcels)', county['tax'] / county['acres']))

    total_tax = sum(float(r['tax'] or 0) for r in shares)
    total_acres = sum(float(r['acres'] or 0) for r in shares)
    share_rows = [
        {
            'type': str(r['proptype']).title().replace('Multi-Family', 'Multi-family'),
            'land': 100 * float(r['acres'] or 0) / total_acres,
            'tax': 100 * float(r['tax'] or 0) / total_tax,
        }
        for r in shares
    ]
    return {
        'bench': [(label, float(value)) for label, value in bench],
        'shares': share_rows,
        'total_tax': total_tax,
        'total_acres': total_acres,
    }


def comparisons() -> dict[str, Any]:
    return _comparisons(int(time.time() // DATA_VERSION_SECONDS))


### Chart colors: the validated eight-hue dark palette (bars are adjacent marks, and every bar is
### labeled on its axis, so identity never rests on color alone). Each group keeps one color on
### every chart: Greenville and single-family are blue, commercial orange, and so on; the map's
### three hues (blue, orange, aqua) mean the same things here.
C_BLUE, C_ORANGE, C_AQUA, C_YELLOW = '#3987e5', '#d95926', '#199e70', '#c98500'
C_MAGENTA, C_GREEN, C_VIOLET, C_RED, C_NEUTRAL = '#d55181', '#008300', '#9085e9', '#e66767', '#6b6a64'
GROUP_COLORS: dict[str, dict[str, str]] = {
    'use_class': {
        'Single-family': C_BLUE, 'Commercial': C_ORANGE, 'Apartments': C_AQUA, 'Industrial': C_YELLOW,
        'Mobile home': C_MAGENTA, 'Small multi-family': C_GREEN, 'Vacant residential': C_VIOLET,
        'Agricultural': C_RED, 'Other': C_NEUTRAL,
    },
    'zone_family': {
        'Residential': C_BLUE, 'Commercial & office': C_ORANGE, 'Mixed-use': C_AQUA,
        'Industrial & services': C_YELLOW, 'Civic, parks & special': C_MAGENTA, 'Agricultural': C_GREEN,
        'Planned development': C_VIOLET, 'Unzoned': C_NEUTRAL, 'Unclassified': C_NEUTRAL,
    },
    'proptype': {
        'RESIDENTIAL': C_BLUE, 'COMMERCIAL': C_ORANGE, 'MULTI-FAMILY': C_AQUA, 'INDUSTRIAL': C_YELLOW,
        'MOBILE HOME': C_MAGENTA, 'AGRICULTURAL': C_GREEN, 'OTHER': C_NEUTRAL,
    },
    'municipality': {
        'GREENVILLE': C_BLUE, 'GREER': C_ORANGE, 'MAULDIN': C_AQUA, 'SIMPSONVILLE': C_YELLOW,
        'FOUNTAIN INN': C_MAGENTA, 'TRAVELERS REST': C_VIOLET, UNINCORPORATED: C_NEUTRAL,
    },
}
### Benchmarks on the comparison chart, by their label's start.
BENCH_COLORS: list[tuple[str, str]] = [
    ('Densest', C_ORANGE), ('Downtown', C_AQUA), ('Haywood', C_VIOLET), ('Walmart', C_YELLOW),
    ('Typical', C_BLUE), ('County', C_NEUTRAL),
]


@functools.lru_cache(maxsize=2)
def _zone_family_lookup(bucket: int) -> dict[str, str]:
    """{'JURISDICTION · ZONE': family} for coloring zone bars by their family."""
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    tables = get_tables()
    with tables['conn'].engine.connect() as c:
        rows = c.execute(sqlalchemy.text(
            f'SELECT zf_jurisdiction, zf_zone, family FROM "{tables["zone_families"]}"'
        )).fetchall()
    return {f"{j} · {z}": fam for j, z, fam in rows}


def group_color(by: str, row: dict[str, Any]) -> str:
    """The chart color of one breakdown group (see GROUP_COLORS)."""
    key = str(row.get('key'))
    if by in GROUP_COLORS:
        return GROUP_COLORS[by].get(key, C_NEUTRAL)
    if by == 'tax_district':
        label = str(row.get('detail') or '').upper()
        return GROUP_COLORS['municipality'].get(label, C_NEUTRAL)
    if by == 'landuse':
        first = str(row.get('detail') or '').split(',')[0].strip()
        return GROUP_COLORS['proptype'].get(first, C_NEUTRAL)
    families = _zone_family_lookup(int(time.time() // DATA_VERSION_SECONDS))
    if by == 'zone':
        return GROUP_COLORS['zone_family'].get(families.get(key, ''), C_NEUTRAL)
    if by == 'zone_code':
        fams = {fam for k, fam in families.items() if k.endswith(f" · {key}")}
        return GROUP_COLORS['zone_family'].get(fams.pop(), C_NEUTRAL) if len(fams) == 1 else C_NEUTRAL
    return C_NEUTRAL


### (name, lots filter, line color, dash): unincorporated is neutral grey everywhere, so it is
### dashed here to stay distinct from the light countywide line.
CONCENTRATION_GROUPS: list[tuple[str, str, str, str]] = [
    ('Countywide', 'TRUE', '#c3c2b7', 'solid'),
    ('City of Greenville', "municipality = 'GREENVILLE'", C_BLUE, 'solid'),
    ('Unincorporated', f"municipality = '{UNINCORPORATED}'", '#8f8d86', 'dash'),
]


@functools.lru_cache(maxsize=2)
def _concentration(bucket: int) -> dict[str, Any]:
    """
    Lorenz-style curves: lots sorted from the highest tax per acre down, cumulative share of land
    (x) against cumulative share of tax (y), each within its own group. ~400 points per curve.
    """
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    tables = get_tables()
    out = []
    with tables['conn'].engine.connect() as c:
        for name, where, color, dash in CONCENTRATION_GROUPS:
            rows = c.execute(sqlalchemy.text(f"""
                WITH l AS (
                    SELECT acres, tax,
                           SUM(acres) OVER (ORDER BY tax_per_acre DESC NULLS LAST, lot_id) AS ca,
                           SUM(tax) OVER (ORDER BY tax_per_acre DESC NULLS LAST, lot_id) AS ct,
                           ROW_NUMBER() OVER (ORDER BY tax_per_acre DESC NULLS LAST, lot_id) AS rn,
                           COUNT(*) OVER () AS n
                    FROM "{tables['lots']}" WHERE acres > 0 AND {where}
                ), t AS (SELECT SUM(acres) AS a, SUM(tax) AS x FROM "{tables['lots']}" WHERE acres > 0 AND {where})
                SELECT 100 * ca / t.a AS land, 100 * ct / NULLIF(t.x, 0) AS tax
                FROM l, t WHERE MOD(rn, GREATEST(n / 400, 1)) = 0 OR rn = n OR rn <= 50
                ORDER BY rn
            """)).fetchall()
            pts = [(0.0, 0.0)] + [(float(r[0]), float(r[1] or 0)) for r in rows]
            at = lambda pct: next((t for l, t in pts if l >= pct), 100.0)
            out.append({'name': name, 'color': color, 'dash': dash, 'points': pts, 'top1': at(1.0), 'top10': at(10.0)})
    return {'curves': out}


def concentration() -> dict[str, Any]:
    return _concentration(int(time.time() // DATA_VERSION_SECONDS))


YEAR_GROUPS: dict[str, dict[str, str]] = {
    'municipality': {
        'title': 'Municipality (by tax district)',
        'key': "COALESCE(NULLIF(upper(trim(m.city_name)), ''), CASE WHEN m.dist IS NULL THEN '(unlabeled district)' ELSE 'UNINCORPORATED' END)",
    },
    ### Classified by each parcel's current code in every year, so the county's recoding between
    ### years (e.g. apartments 120 -> 1182) doesn't show up as change.
    'use_class': {
        'title': 'Use class',
        'key': USE_CLASS_SQL.replace('landuse', 'COALESCE(p.landuse, t.landuse)')
                            .replace('proptype', 'COALESCE(p.proptype, t.proptype)'),
    },
}


@functools.lru_cache(maxsize=4)
def _year_change(by: str, bucket: int) -> dict[str, Any]:
    """Tax by group in each tax year of `parcel_taxes`, and the change between the last two."""
    sqlalchemy = mrsm.attempt_import('sqlalchemy', lazy=False)
    tables = get_tables()
    key = YEAR_GROUPS[by]['key']
    with tables['conn'].engine.connect() as c:
        rows = c.execute(sqlalchemy.text(f"""
            SELECT t.tax_year AS year, {key} AS g, SUM(t.tax) AS tax, COUNT(*) AS parcels
            FROM "{tables['parcel_taxes']}" t
            LEFT JOIN "{tables['millage']}" m ON m.year = t.tax_year AND m.dist = t.tax_district
            LEFT JOIN "{tables['parcels']}" p ON p.pin = t.pin
            GROUP BY 1, 2
        """)).mappings().fetchall()
    years = sorted({int(r['year']) for r in rows})
    if len(years) < 2:
        return {'years': years, 'rows': []}
    y0, y1 = years[-2], years[-1]
    by_group: dict[str, dict[int, dict[str, float]]] = {}
    for r in rows:
        by_group.setdefault(str(r['g']), {})[int(r['year'])] = {'tax': float(r['tax'] or 0), 'parcels': int(r['parcels'])}
    out = []
    for g, ys in by_group.items():
        a, b = ys.get(y0, {}).get('tax', 0.0), ys.get(y1, {}).get('tax', 0.0)
        out.append({
            'key': g, 'before': a, 'after': b, 'change': b - a, 'pct': (100 * (b - a) / a) if a else None,
            'parcels_before': ys.get(y0, {}).get('parcels', 0), 'parcels_after': ys.get(y1, {}).get('parcels', 0),
        })
    out.sort(key=lambda r: -r['after'])
    total_a, total_b = sum(r['before'] for r in out), sum(r['after'] for r in out)
    return {
        'years': years, 'y0': y0, 'y1': y1, 'rows': out,
        'total': {'before': total_a, 'after': total_b, 'pct': 100 * (total_b - total_a) / total_a if total_a else None},
    }


def year_change(by: str) -> dict[str, Any]:
    if by not in YEAR_GROUPS:
        raise ValueError(f"Unknown grouping '{by}'.")
    return _year_change(by, int(time.time() // DATA_VERSION_SECONDS))


def _layout(fig: Any, height: int, margin: Optional[dict[str, int]] = None, **kwargs: Any) -> Any:
    """Shared dark chart styling: transparent surface, recessive grid, readable ink."""
    fig.update_layout(
        height=height,
        margin=margin or {'l': 10, 'r': 90, 't': 10, 'b': 30},
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font={'color': INK_SECONDARY, 'size': 12},
        hoverlabel={'bgcolor': SURFACE, 'bordercolor': HAIRLINE, 'font': {'color': INK_PRIMARY}},
        **kwargs,
    )
    return fig


EMPTY_FIGURE: dict[str, Any] = {'data': [], 'layout': {'paper_bgcolor': 'rgba(0,0,0,0)', 'plot_bgcolor': 'rgba(0,0,0,0)'}}
DOLLARS_PER_ACRE_AXIS: dict[str, Any] = {
    'gridcolor': HAIRLINE, 'zeroline': True, 'zerolinecolor': '#383835', 'tickprefix': '$',
    'tickformat': '~s', 'title': {'text': 'Property tax per acre', 'font': {'size': 12}},
}


def _bench_color(label: str) -> str:
    return next((color for prefix, color in BENCH_COLORS if label.startswith(prefix)), C_NEUTRAL)


def _bench_figure(bench: list[tuple[str, float]]) -> Any:
    go = mrsm.attempt_import('plotly.graph_objects', lazy=False)
    labels = [label for label, _ in bench][::-1]
    values = [value for _, value in bench][::-1]
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation='h',
        marker={'color': [_bench_color(label) for label in labels], 'cornerradius': 4,
                'line': {'color': SURFACE, 'width': 2}},
        text=[f"${v:,.0f}" for v in values], textposition='outside', cliponaxis=False,
        textfont={'color': INK_PRIMARY, 'size': 12},
        hovertemplate='%{y}<br><b>$%{x:,.0f}</b> per acre<extra></extra>',
    ))
    return _layout(fig, 60 + 48 * len(bench), xaxis=DOLLARS_PER_ACRE_AXIS,
                   yaxis={'automargin': True, 'tickfont': {'color': INK_PRIMARY}}, bargap=0.35)


SHARE_GROUPINGS: list[tuple[str, str]] = [
    ('use_class', 'Use class'), ('municipality', 'Municipality'), ('zone_family', 'Zoning family'),
    ('proptype', 'Property type'),
]
SHARES_TOP_N: int = 7


def _shares_figure(data: dict[str, Any]) -> Any:
    """Two 100% bars, land and tax revenue, split by group: who has the land vs. who pays."""
    go = mrsm.attempt_import('plotly.graph_objects', lazy=False)
    by = data['by']
    rows = sorted(data['rows'], key=lambda r: -(r['tax'] or 0))
    top, rest = rows[:SHARES_TOP_N], rows[SHARES_TOP_N:]
    groups = [(_breakdown_label(by, r), group_color(by, r), r['acre_share'] or 0, r['tax_share'] or 0) for r in top]
    if rest:
        groups.append((f"{len(rest)} more", C_NEUTRAL, sum(r['acre_share'] or 0 for r in rest),
                       sum(r['tax_share'] or 0 for r in rest)))
    fig = go.Figure()
    for name, color, land, tax in groups:
        values = [100 * land, 100 * tax]
        fig.add_trace(go.Bar(
            name=name, y=['Land', 'Tax revenue'], x=values, orientation='h',
            marker={'color': color, 'line': {'color': SURFACE, 'width': 2}},
            text=[f"{v:.0f}%" if v >= 6 else '' for v in values], textposition='inside',
            insidetextanchor='middle', textfont={'color': '#ffffff', 'size': 12},
            hovertemplate=f'{name}<br>%{{y}}: <b>%{{x:.1f}}%%</b><extra></extra>'.replace('%%', '%'),
        ))
    return _layout(
        fig, 250, {'l': 10, 'r': 10, 't': 10, 'b': 10}, barmode='stack', bargap=0.25,
        xaxis={'range': [0, 100], 'showgrid': False, 'zeroline': False, 'ticksuffix': '%', 'visible': False},
        yaxis={'automargin': True, 'tickfont': {'color': INK_PRIMARY, 'size': 13}, 'autorange': 'reversed'},
        legend={'orientation': 'h', 'y': -0.08, 'x': 0, 'yanchor': 'top', 'font': {'color': INK_SECONDARY},
                'traceorder': 'normal'},
    )


def _concentration_figure(data: dict[str, Any]) -> Any:
    """Cumulative share of tax against cumulative share of land, most productive land first."""
    go = mrsm.attempt_import('plotly.graph_objects', lazy=False)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[0, 100], y=[0, 100], mode='lines', name='Even (every acre pays the same)',
        line={'color': '#4a4a46', 'width': 1.5, 'dash': 'dot'}, hoverinfo='skip',
    ))
    for curve in data['curves']:
        xs = [x for x, _ in curve['points']]
        ys = [y for _, y in curve['points']]
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode='lines', name=curve['name'],
            line={'color': curve['color'], 'width': 2.5, 'dash': curve.get('dash', 'solid')},
            hovertemplate=(
                f"{curve['name']}<br>Top %{{x:.1f}}% of land pays <b>%{{y:.1f}}%</b> of the tax<extra></extra>"
            ),
        ))
    county = data['curves'][0]
    fig.add_annotation(
        x=1, y=county['top1'], xref='x', yref='y', ax=60, ay=10, showarrow=True, arrowcolor=INK_MUTED,
        arrowwidth=1, text=f"1% of land: {county['top1']:.0f}% of tax", font={'color': INK_PRIMARY, 'size': 12},
        bgcolor=SURFACE,
    )
    return _layout(
        fig, 360, {'l': 10, 'r': 20, 't': 10, 'b': 40},
        xaxis={'range': [0, 100], 'gridcolor': HAIRLINE, 'zeroline': False, 'ticksuffix': '%',
               'title': {'text': 'Share of land (highest tax per acre first)', 'font': {'size': 12}}},
        yaxis={'range': [0, 101], 'gridcolor': HAIRLINE, 'zeroline': False, 'ticksuffix': '%',
               'title': {'text': 'Share of property tax', 'font': {'size': 12}}},
        legend={'orientation': 'h', 'y': -0.2, 'x': 0, 'yanchor': 'top', 'font': {'color': INK_SECONDARY}},
        hovermode='closest',
    )


YEAR_MIN_TAX: float = 1_000_000.0


def _year_label(by: str, key: str) -> str:
    if by == 'municipality':
        return 'Unincorporated' if key == UNINCORPORATED else key.title() if not key.startswith('(') else key
    return key


def _year_figure(data: dict[str, Any], by: str) -> Any:
    """Percent change in tax by group between the last two tax years, colored by group."""
    go = mrsm.attempt_import('plotly.graph_objects', lazy=False)
    ### Groups under $1M swing by large percents on small dollars, so they're left out.
    rows = [r for r in data['rows'] if r['pct'] is not None and r['before'] >= YEAR_MIN_TAX][::-1]
    lo = min([0.0] + [r['pct'] for r in rows]); hi = max([0.0] + [r['pct'] for r in rows])
    span = max(hi - lo, 1.0)
    x_range = [lo - (0.45 * span if lo < 0 else 0.02 * span), hi + 0.45 * span]
    color_by = 'municipality' if by == 'municipality' else 'use_class'
    fig = go.Figure(go.Bar(
        x=[r['pct'] for r in rows], y=[_year_label(by, r['key']) for r in rows], orientation='h',
        marker={'color': [GROUP_COLORS[color_by].get(r['key'], C_NEUTRAL) for r in rows], 'cornerradius': 4,
                'line': {'color': SURFACE, 'width': 2}},
        text=[f"{r['pct']:+.1f}% ({'+' if r['change'] >= 0 else '−'}${abs(r['change']) / 1e6:,.1f}M)" for r in rows],
        textposition='outside', cliponaxis=False, textfont={'color': INK_PRIMARY, 'size': 12},
        customdata=[[r['before'], r['after']] for r in rows],
        hovertemplate=(
            f"%{{y}}<br>{data['y0']}: $%{{customdata[0]:,.0f}}<br>{data['y1']}: $%{{customdata[1]:,.0f}}"
            "<br><b>%{x:+.1f}%</b><extra></extra>"
        ),
    ))
    return _layout(
        fig, 60 + 34 * max(len(rows), 1), {'l': 10, 'r': 20, 't': 10, 'b': 30},
        xaxis={'gridcolor': HAIRLINE, 'zeroline': True, 'zerolinecolor': '#5a5a55', 'ticksuffix': '%',
               'range': x_range,
               'title': {'text': f"Change in property tax, {data['y0']} to {data['y1']}", 'font': {'size': 12}}},
        yaxis={'automargin': True, 'tickfont': {'color': INK_PRIMARY}}, bargap=0.3,
    )


BREAKDOWN_TOP_N: int = 15
PROPTYPE_OPTIONS: list[str] = [
    'RESIDENTIAL', 'COMMERCIAL', 'MULTI-FAMILY', 'INDUSTRIAL', 'MOBILE HOME', 'OTHER', 'AGRICULTURAL',
]


def _breakdown_label(by: str, row: dict[str, Any]) -> str:
    key = row['key']
    if by == 'proptype':
        return key.title().replace('Multi-Family', 'Multi-family')
    if by == 'municipality':
        return 'Unincorporated' if key == UNINCORPORATED else key.title()
    if by == 'zone':
        juris, _, zone = key.partition(' · ')
        return f"{zone} ({juris.title()})"
    if by == 'tax_district':
        return f"DIST {key}" + (f" ({row['detail']})" if row.get('detail') else '')
    if by == 'landuse':
        return f"{key} ({str(row.get('detail') or '').lower()})"
    return key


def _breakdown_figure(data: dict[str, Any]) -> Any:
    """Tax per acre of the top groups by revenue, each bar in its group's color."""
    go = mrsm.attempt_import('plotly.graph_objects', lazy=False)
    rows = [r for r in data['rows'] if r['tax_per_acre'] is not None][:BREAKDOWN_TOP_N][::-1]
    labels = [_breakdown_label(data['by'], r) for r in rows]
    fig = go.Figure(go.Bar(
        x=[r['tax_per_acre'] for r in rows], y=labels, orientation='h',
        marker={'color': [group_color(data['by'], r) for r in rows], 'cornerradius': 4,
                'line': {'color': SURFACE, 'width': 2}},
        text=[f"${r['tax_per_acre']:,.0f}" for r in rows], textposition='outside', cliponaxis=False,
        textfont={'color': INK_PRIMARY, 'size': 12},
        customdata=[[r['tax'], r['acres'], 100 * (r['tax_share'] or 0)] for r in rows],
        hovertemplate=(
            '%{y}<br><b>$%{x:,.0f}</b> per acre<br>$%{customdata[0]:,.0f} tax (%{customdata[2]:.1f}%)'
            '<br>%{customdata[1]:,.0f} acres<extra></extra>'
        ),
    ))
    return _layout(fig, 60 + 30 * max(len(rows), 1), xaxis=DOLLARS_PER_ACRE_AXIS,
                   yaxis={'automargin': True, 'tickfont': {'color': INK_PRIMARY}}, bargap=0.3)


def _breakdown_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Table rows, plus a total row that shows the groups add up to the whole."""
    records = [
        {
            'group': _breakdown_label(data['by'], r),
            'lots': r['lots'],
            'tax': round(r['tax']),
            'tax_share': round(100 * (r['tax_share'] or 0), 2),
            'acres': round(r['acres'], 1),
            'acre_share': round(100 * (r['acre_share'] or 0), 2),
            'tax_per_acre': round(r['tax_per_acre']) if r['tax_per_acre'] is not None else None,
        }
        for r in data['rows']
    ]
    t = data['total']
    records.append({
        'group': 'Total', 'lots': t['lots'], 'tax': round(t['tax']), 'tax_share': 100.0,
        'acres': round(t['acres'], 1), 'acre_share': 100.0,
        'tax_per_acre': round(t['tax_per_acre']) if t['tax_per_acre'] else None,
    })
    return records


@web_page(PAGE_PATH, login_required=False, page_group='Taxes')
def tax_per_acre_page():
    """Property tax per acre: 3D map plus comparisons."""
    dcc = mrsm.attempt_import('dash.dcc', lazy=False)
    html = mrsm.attempt_import('dash.html', lazy=False)
    dbc = mrsm.attempt_import('dash_bootstrap_components', lazy=False)
    card_style = {'backgroundColor': SURFACE, 'border': f'1px solid {HAIRLINE}'}
    sub_style = {'color': INK_SECONDARY, 'fontSize': '0.85rem'}
    note_style = {'color': INK_MUTED, 'fontSize': '0.8rem'}
    graph = lambda gid, color=SERIES_TAX: dcc.Loading(
        dcc.Graph(id=gid, config={'displayModeBar': False}), type='dot', color=color,
    )
    radios = lambda rid, options, value: dbc.RadioItems(
        id=rid, inline=True, value=value, options=[{'label': t, 'value': k} for k, t in options],
        style={'fontSize': '0.85rem'}, className='mb-1',
    )
    card = lambda body, **kw: dbc.Card(dbc.CardBody(body), style=card_style, **kw)
    return html.Div(
        [
            dcc.Location(id='taxmap-location', refresh=False),
            html.H3('Greenville County property tax per acre', className='mb-1'),
            html.Div(
                f"Every lot's {TAX_YEAR} property tax divided by its land area. "
                "Source: Greenville County GIS.",
                style={'color': INK_SECONDARY, 'fontSize': '0.9rem'}, className='mb-2',
            ),
            html.Iframe(
                id='taxmap-frame', src='/taxmap/map', title='3D map of property tax per acre',
                style={
                    'width': '100%', 'height': '76vh', 'minHeight': '480px', 'border': f'1px solid {HAIRLINE}',
                    'borderRadius': '8px', 'backgroundColor': '#0d0d0d',
                },
                allow='fullscreen',
            ),
            dbc.Row(
                [
                    dbc.Col(card([
                        html.H5('Tax per acre, compared', className='mb-1'),
                        html.Div(
                            [
                                html.Span('Compare'),
                                dcc.Dropdown(
                                    id='taxmap-bench-lot', clearable=False, searchable=False,
                                    style={'minWidth': '220px', 'flex': '1 1 220px', 'color': '#111'},
                                ),
                                html.Span('with a Walmart acre and a typical house in'),
                                dcc.Dropdown(
                                    id='taxmap-bench-zone', clearable=False, value='', placeholder='Countywide',
                                    style={'minWidth': '220px', 'flex': '1 1 220px', 'color': '#111'},
                                ),
                            ],
                            style={'display': 'flex', 'flexWrap': 'wrap', 'alignItems': 'center', 'gap': '6px',
                                   'fontSize': '0.85rem', 'color': INK_SECONDARY},
                            className='mb-1',
                        ),
                        html.Div(id='taxmap-bench-sub', style=sub_style, **{'aria-live': 'polite'}),
                        html.Div('Typical house: the median single-family lot of 0.1–2 acres.', style=note_style),
                        graph('taxmap-bench'),
                    ]), lg=7, className='mb-3'),
                    dbc.Col(card([
                        html.H5('Who has the land, who pays the tax', className='mb-1'),
                        radios('taxmap-shares-by', SHARE_GROUPINGS, 'use_class'),
                        html.Div(id='taxmap-shares-sub', style=sub_style),
                        graph('taxmap-shares', SERIES_LAND),
                    ]), lg=5, className='mb-3'),
                ],
                className='g-3 mt-2',
            ),
            dbc.Row(
                [
                    dbc.Col(card([
                        html.H5('How concentrated the tax base is', className='mb-1'),
                        html.Div(id='taxmap-concentration-sub', style=sub_style),
                        graph('taxmap-concentration', SERIES_LAND),
                    ]), lg=6, className='mb-3'),
                    dbc.Col(card([
                        html.H5(id='taxmap-year-title', children='Change from last year', className='mb-1'),
                        radios('taxmap-year-by', [(k, v['title']) for k, v in YEAR_GROUPS.items()], 'municipality'),
                        html.Div(id='taxmap-year-sub', style=sub_style),
                        graph('taxmap-year'),
                    ]), lg=6, className='mb-3'),
                ],
                className='g-3',
            ),
            card([
                html.H5('Tax revenue by land use, zone and jurisdiction', className='mb-1'),
                dbc.Row([
                    dbc.Col(dbc.RadioItems(
                        id='taxmap-breakdown-by', inline=True, value='use_class',
                        options=[{'label': spec['title'], 'value': key} for key, spec in BREAKDOWNS.items()],
                        style={'fontSize': '0.9rem'},
                    ), md=9),
                    dbc.Col(dcc.Dropdown(
                        id='taxmap-breakdown-proptype', placeholder='All property types', clearable=True,
                        options=[
                            {'label': t.title().replace('Multi-Family', 'Multi-family'), 'value': t}
                            for t in PROPTYPE_OPTIONS
                        ],
                        style={'fontSize': '0.9rem', 'color': '#111'},
                    ), md=3),
                ], className='g-2 mb-2'),
                html.Div(id='taxmap-breakdown-sub', style=sub_style),
                dbc.Row([
                    dbc.Col(graph('taxmap-breakdown-chart'), lg=5),
                    dbc.Col(dcc.Loading(html.Div(id='taxmap-breakdown-table'), type='dot', color=SERIES_TAX), lg=7),
                ], className='g-3'),
            ], className='mb-3'),
        ],
        style={'padding': '16px', 'maxWidth': '1500px', 'margin': '0 auto'},
    )


@dash_plugin(public=True)
def init_dash(dash_app):
    """The page's charts (public, to match the public page)."""
    from dash import Input, Output

    @dash_app.callback(
        Output('taxmap-bench', 'figure'),
        Output('taxmap-bench-lot', 'options'),
        Output('taxmap-bench-lot', 'value'),
        Output('taxmap-bench-zone', 'options'),
        Output('taxmap-concentration', 'figure'),
        Output('taxmap-concentration-sub', 'children'),
        Input('taxmap-location', 'pathname'),
    )
    def render_comparisons(_pathname):
        try:
            data = comparisons()
            bench = benchmarks()
        except Exception as e:
            warn(f"taxmap: comparisons failed: {e}", stack=False)
            return EMPTY_FIGURE, [], None, [], EMPTY_FIGURE, 'Data unavailable.'
        ### Anything on the chart except the two yardsticks themselves can be compared.
        lots = [
            {'label': label, 'value': label} for label, _ in data['bench']
            if not label.startswith(('Walmart', 'Typical'))
        ]
        house = bench['house']
        zones = [{'label': f"Countywide (median of {house['n']:,} houses)", 'value': ''}] + [
            {
                'label': f"{key.split('|', 1)[1]} · {key.split('|', 1)[0].title()} zoning ({z['n']:,} houses)",
                'value': key,
            }
            for key, z in sorted(bench['house_by_zone'].items(), key=lambda kv: -kv[1]['n'])
        ]
        try:
            conc = concentration()
            county = conc['curves'][0]
            conc_sub = (
                f"The most productive 1% of the county's land pays {county['top1']:.0f}% of its property tax, "
                f"and 10% pays {county['top10']:.0f}%."
            )
            conc_fig = _concentration_figure(conc)
        except Exception as e:
            warn(f"taxmap: concentration failed: {e}", stack=False)
            conc_fig, conc_sub = EMPTY_FIGURE, 'Data unavailable.'
        return (
            _bench_figure(data['bench']), lots, (lots[0]['value'] if lots else None), zones, conc_fig, conc_sub,
        )

    @dash_app.callback(
        Output('taxmap-bench-sub', 'children'),
        Input('taxmap-bench-lot', 'value'),
        Input('taxmap-bench-zone', 'value'),
    )
    def render_bench_sentence(lot, zone):
        try:
            values = dict(comparisons()['bench'])
            bench = benchmarks()
        except Exception:
            return ''
        tpa = values.get(lot)
        if not tpa:
            return ''
        walmart = bench['walmart']
        if zone:
            jurisdiction, code = zone.split('|', 1)
            house = typical_house(jurisdiction, code)
        else:
            house = typical_house()
        where = (
            f"in {house['zone']} ({house['jurisdiction'].title()} zoning, ${house['tpa']:,.0f}/acre)"
            if house['scope'] == 'zone'
            else f"countywide (${house['tpa']:,.0f}/acre)"
        )
        parts = [f"{lot} pays ${tpa:,.0f} per acre: "]
        if walmart['tpa']:
            parts.append(f"{tpa / walmart['tpa']:,.1f}× a Walmart acre (${walmart['tpa']:,.0f})")
        if house['tpa']:
            parts.append(f" and {tpa / house['tpa']:,.1f}× a typical house {where}.")
        return ''.join(parts)

    @dash_app.callback(
        Output('taxmap-shares', 'figure'),
        Output('taxmap-shares-sub', 'children'),
        Input('taxmap-shares-by', 'value'),
    )
    def render_shares(by):
        by = by if by in dict(SHARE_GROUPINGS) else 'use_class'
        try:
            data = breakdown(by)
        except Exception as e:
            warn(f"taxmap: shares failed: {e}", stack=False)
            return EMPTY_FIGURE, 'Data unavailable.'
        t = data['total']
        return _shares_figure(data), f"{t['acres']:,.0f} acres and ${t['tax'] / 1e6:,.0f}M in property tax."

    @dash_app.callback(
        Output('taxmap-year', 'figure'),
        Output('taxmap-year-sub', 'children'),
        Output('taxmap-year-title', 'children'),
        Input('taxmap-year-by', 'value'),
    )
    def render_year(by):
        by = by if by in YEAR_GROUPS else 'municipality'
        try:
            data = year_change(by)
        except Exception as e:
            warn(f"taxmap: year change failed: {e}", stack=False)
            return EMPTY_FIGURE, 'Data unavailable.', 'Change from last year'
        if not data['rows']:
            return EMPTY_FIGURE, 'Only one tax year so far.', 'Change from last year'
        t = data['total']
        sub = (
            f"Countywide: ${t['before'] / 1e6:,.1f}M → ${t['after'] / 1e6:,.1f}M ({t['pct']:+.1f}%), "
            "including new parcels."
        )
        if by == 'use_class':
            sub += ' Both years use each parcel’s current land-use code.'
        return _year_figure(data, by), sub, f"Change from {data['y0']} to {data['y1']}"

    @dash_app.callback(
        Output('taxmap-breakdown-chart', 'figure'),
        Output('taxmap-breakdown-table', 'children'),
        Output('taxmap-breakdown-sub', 'children'),
        Input('taxmap-breakdown-by', 'value'),
        Input('taxmap-breakdown-proptype', 'value'),
    )
    def render_breakdown(by, proptype):
        dash_table = mrsm.attempt_import('dash.dash_table', lazy=False)
        by = by if by in BREAKDOWNS else 'use_class'
        try:
            data = breakdown(by, {'proptype': proptype} if proptype else None)
        except Exception as e:
            warn(f"taxmap: breakdown failed: {e}", stack=False)
            return EMPTY_FIGURE, None, 'Data unavailable.'
        county = data['county']
        scope = f"{proptype.title()} lots" if proptype else 'All lots'
        sub = (
            f"{scope}: ${data['total']['tax'] / 1e6:,.1f}M of ${county['tax'] / 1e6:,.1f}M "
            f"on {data['total']['acres']:,.0f} acres. Chart: the {min(BREAKDOWN_TOP_N, len(data['rows']))} "
            "groups with the most revenue."
        )
        money_fmt = {'specifier': '$,.0f'}
        table = dash_table.DataTable(
            data=_breakdown_records(data),
            columns=[
                {'name': data['title'], 'id': 'group'},
                {'name': 'Lots', 'id': 'lots', 'type': 'numeric', 'format': {'specifier': ',d'}},
                {'name': 'Tax revenue', 'id': 'tax', 'type': 'numeric', 'format': money_fmt},
                {'name': '% of tax', 'id': 'tax_share', 'type': 'numeric', 'format': {'specifier': '.1f'}},
                {'name': 'Acres', 'id': 'acres', 'type': 'numeric', 'format': {'specifier': ',.0f'}},
                {'name': '% of land', 'id': 'acre_share', 'type': 'numeric', 'format': {'specifier': '.1f'}},
                {'name': 'Tax per acre', 'id': 'tax_per_acre', 'type': 'numeric', 'format': money_fmt},
            ],
            sort_action='native', page_size=20, page_action='native',
            style_as_list_view=True,
            style_table={'overflowX': 'auto'},
            style_header={'backgroundColor': SURFACE, 'color': INK_SECONDARY, 'fontWeight': '600',
                          'borderBottom': f'1px solid {HAIRLINE}'},
            style_cell={'backgroundColor': SURFACE, 'color': INK_PRIMARY, 'border': 'none',
                        'borderBottom': f'1px solid {HAIRLINE}', 'fontSize': '0.85rem',
                        'fontFamily': 'inherit', 'padding': '4px 8px',
                        'fontVariantNumeric': 'tabular-nums'},
            style_cell_conditional=[{'if': {'column_id': 'group'}, 'textAlign': 'left', 'maxWidth': '260px',
                                     'overflow': 'hidden', 'textOverflow': 'ellipsis'}],
            style_data_conditional=[{'if': {'filter_query': '{group} = "Total"'}, 'fontWeight': '600',
                                     'borderTop': f'1px solid {INK_MUTED}'}],
            export_format='csv',
        )
        return _breakdown_figure(data), table, sub
