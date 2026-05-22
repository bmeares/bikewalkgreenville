#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Parse the Greenville County data and shapefiles.
"""

import os
import time
import pathlib
import shutil
import zipfile
from urllib.parse import urljoin

import meerschaum as mrsm
from meerschaum.utils.warnings import info
from meerschaum.plugins import make_action

__version__ = '0.2.0'

bwg = mrsm.Plugin('bwg')

required = ['geopandas', 'pyogrio', 'pandas[pyarrow]', 'requests']

BASE_URL = "https://www.gcgis.org"
SUBMIT_JOB_URL: str = f"{BASE_URL}/arcgis/rest/services/GreenvilleJS/ExportLayers/GPServer/ExportLayers/submitJob"
OUTPUT_URL_TEMPLATE: str = f"{BASE_URL}/arcgis/rest/services/GreenvilleJS/ExportLayers/GPServer/ExportLayers/jobs/" + "{job_id}"
LAYER_NAMES: list[str] = [
    'PRISMA Health Swamp Rabbit Trail',
]
DEFAULT_PAGE_SIZE: int = 2000


def fetch(pipe: mrsm.Pipe, **kwargs):
    """
    Parse the `greenville county` shapefiles.
    """
    pd, _, gpd, _ = mrsm.attempt_import(
        'pandas', 'pyarrow', 'geopandas', 'pyogrio',
        venv='greenville-county',
        lazy=False,
    )
    data_path = bwg.module.get_data_path()
    county_path = data_path / 'greenville county'
    metric_path = county_path / pipe.metric_key

    greenville_county_params = pipe.parameters.get('greenville-county', {})
    filetype = greenville_county_params.get('filetype', 'shp')
    layer = greenville_county_params.get('layer', None)
    mapserver_url = greenville_county_params.get('mapserver', None)

    if mapserver_url:
        out_sr = _extract_out_sr(pipe) or greenville_county_params.get('out_sr')
        return fetch_paginated_mapserver(
            mapserver_url,
            layer=layer,
            layer_id=greenville_county_params.get('layer_id'),
            where=greenville_county_params.get('where', '1=1'),
            page_size=greenville_county_params.get('page_size', DEFAULT_PAGE_SIZE),
            out_sr=out_sr,
        )

    file_path = (
        (metric_path / (pipe.target + '.' + filetype))
        if not layer
        else fetch_layer([layer])
    )
    if not file_path.exists():
        raise FileNotFoundError(f"Path does not exist:\n{file_path}")

    df = gpd.read_file(file_path) if filetype == 'shp' else pd.read_csv(file_path)
    return df


def _extract_out_sr(pipe: 'mrsm.Pipe') -> int | None:
    """
    Pull the SRID out of a `geometry[..., <srid>]` dtype hint if present.
    """
    import re
    dtypes = pipe.parameters.get('dtypes', {}) or {}
    geom_dtype = dtypes.get('geometry')
    if not geom_dtype or not isinstance(geom_dtype, str):
        return None
    m = re.search(r',\s*(\d+)\s*\]', geom_dtype)
    return int(m.group(1)) if m else None


def fetch_paginated_mapserver(
    mapserver_url: str,
    layer: str | None = None,
    layer_id: int | None = None,
    where: str = '1=1',
    page_size: int = DEFAULT_PAGE_SIZE,
    out_sr: int | None = None,
):
    """
    Query an ArcGIS REST MapServer layer with pagination and return a GeoDataFrame.

    The GP `ExportLayers` service silently truncates results above `MaxExportPublic`
    (1000), so big layers like `Sidewalk` (~18k features) must be paged via the
    underlying MapServer's `query` endpoint.
    """
    requests, gpd, _ = mrsm.attempt_import(
        'requests', 'geopandas', 'pyogrio',
        venv='greenville-county',
        lazy=False,
    )
    mapserver_url = mapserver_url.rstrip('/')

    if layer_id is None:
        if not layer:
            raise ValueError("Must provide `layer` or `layer_id` for paginated fetch.")
        layer_id = resolve_layer_id(mapserver_url, layer)

    layer_url = f"{mapserver_url}/{layer_id}"
    layer_meta = requests.get(f"{layer_url}?f=json", timeout=60).json()
    server_max = layer_meta.get('maxRecordCount') or DEFAULT_PAGE_SIZE
    page_size = min(page_size, server_max)

    if out_sr is None:
        src_sr = layer_meta.get('sourceSpatialReference') or layer_meta.get('extent', {}).get('spatialReference', {})
        out_sr = src_sr.get('latestWkid') or src_sr.get('wkid')

    query_url = f"{layer_url}/query"
    count_resp = requests.get(
        query_url,
        params={'where': where, 'returnCountOnly': 'true', 'f': 'json'},
        timeout=60,
    ).json()
    total = count_resp.get('count', 0)
    info(f"Fetching {total} features from '{layer_meta.get('name')}' in pages of {page_size}.")

    frames = []
    offset = 0
    while offset < total:
        params = {
            'where': where,
            'outFields': '*',
            'resultOffset': offset,
            'resultRecordCount': page_size,
            'orderByFields': layer_meta.get('objectIdField') or 'OBJECTID',
            'f': 'geojson',
        }
        if out_sr:
            params['outSR'] = out_sr

        resp = requests.get(query_url, params=params, timeout=120)
        resp.raise_for_status()
        page = resp.json()
        features = page.get('features', [])
        if not features:
            break

        gdf = gpd.GeoDataFrame.from_features(features, crs=(f"EPSG:{out_sr}" if out_sr else None))
        frames.append(gdf)
        offset += len(features)
        info(f"Fetched {min(offset, total)}/{total} features.")

        if len(features) < page_size:
            break

    if not frames:
        return gpd.GeoDataFrame(geometry=[], crs=(f"EPSG:{out_sr}" if out_sr else None))

    import pandas as pd
    combined = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True),
        crs=frames[0].crs,
    )
    return combined


def resolve_layer_id(mapserver_url: str, layer_name: str) -> int:
    """
    Look up a layer's numeric id by name on a MapServer.
    """
    requests = mrsm.attempt_import('requests', venv='greenville-county', lazy=False)
    resp = requests.get(f"{mapserver_url}?f=json", timeout=60)
    resp.raise_for_status()
    data = resp.json()
    for layer in data.get('layers', []):
        if layer.get('name') == layer_name:
            return layer['id']
    raise ValueError(
        f"Layer '{layer_name}' not found at '{mapserver_url}'. "
        f"Available: {[l.get('name') for l in data.get('layers', [])]}"
    )


@make_action
def fetch_layer(
    action: list[str],
    force: bool = False,
    timeout_seconds: int = 30,
) -> pathlib.Path:
    """
    Scrape the county GIS website.
    """
    if not action:
        raise ValueError("Provide a layer to fetch.")

    layer = action[0]
    data_path = bwg.module.get_data_path()
    gcgis_path = data_path / 'gcgis'
    gcgis_path.mkdir(parents=True, exist_ok=True)
    layer_path = gcgis_path / layer

    if layer_path.exists():
        if force:
            shutil.rmtree(layer_path)
        else:
            return layer_path

    job_id = submit_job(layer)
    job_path = download_job_output(job_id, timeout=timeout_seconds)

    with zipfile.ZipFile(job_path, 'r') as zip_ref:
        zip_ref.extractall(layer_path)

    #  job_path.unlink()

    shapefiles = [
        (layer_path / filename)
        for filename in os.listdir(layer_path)
        if filename.endswith('.shp')
    ]
    if not shapefiles:
        raise FileNotFoundError(f"Could not find a shapefile for layer '{layer}'.")

    return layer_path
   

def submit_job(layer: str) -> str:
    """
    Create a new job and return its ID.
    """
    requests = mrsm.attempt_import('requests')
    if not layer:
        raise ValueError("No layer defined.")

    response = requests.get(
        SUBMIT_JOB_URL,
        params={
            'f': 'json',
            'maxRec': 100000,
            'formatType': 'Shapefile',
            'layerNames': layer,
            'clip': 'False',
        },
        headers={'Referer': 'https://www.gcgis.org/apps/greenvillejs/'},
    )
    return response.json()['jobId']
 

def download_job_output(job_id: str, timeout: float | int = 30) -> pathlib.Path:
    """
    Download a job's export file.
    """
    from meerschaum.utils.misc import wget
    data_path = bwg.module.get_data_path()
    gcgis_path = data_path / 'gcgis'
    gcgis_path.mkdir(parents=True, exist_ok=True)
    job_path = gcgis_path / (job_id + '.zip')

    start = time.time()

    while (time.time() - start) < timeout:
        download_url = check_job_output(job_id)
        if download_url is None:
            time.sleep(3)
            info(f"Waiting for job '{job_id}'...")
            continue

        info(f"Downloading job '{job_id}'...")
        wget(download_url, job_path)
        if not job_path.exists():
            raise FileNotFoundError(f"Failed to download job '{job_id}'.")

        info("Downloaded job.")

        return job_path

    raise Exception(f"Job '{job_id}' did not complete within {timeout} seconds.")


def check_job_output(job_id: str) -> str | None:
    """
    If a job has completed, return its output download URL.
    Otherwise return `None`.
    """
    requests = mrsm.attempt_import('requests')
    url = OUTPUT_URL_TEMPLATE.format(job_id=job_id)
    response = requests.get(url, params={'f': 'json'})
    data = response.json()
    job_status = data['jobStatus']
    if job_status != 'esriJobSucceeded':
        if job_status == 'esriJobFailed':
            raise Exception(f"Job '{job_id}' failed to export.")

        return None

    out_zip_path_rel_url = data['results']['OutZipPath']['paramUrl']
    out_zip_url = f"{url}/{out_zip_path_rel_url}"
    response = requests.get(out_zip_url, params={'f': 'json', 'returnType': 'data'})

    data = response.json()
    download_rel_url = data['value']
    download_url = urljoin(BASE_URL, download_rel_url)
    return download_url
