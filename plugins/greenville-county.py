#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Parse the Greenville County data and shapefiles.
"""

import os
import time
import pathlib
import zipfile
from urllib.parse import urljoin

import meerschaum as mrsm
from meerschaum.utils.warnings import info

bwg = mrsm.Plugin('bwg')

required = ['geopandas', 'pyogrio', 'pandas[pyarrow]', 'requests']

BASE_URL = "https://www.gcgis.org"
SUBMIT_JOB_URL: str = f"{BASE_URL}/arcgis/rest/services/GreenvilleJS/ExportLayers/GPServer/ExportLayers/submitJob"
OUTPUT_URL_TEMPLATE: str = f"{BASE_URL}/arcgis/rest/services/GreenvilleJS/ExportLayers/GPServer/ExportLayers/jobs/" + "{job_id}"
LAYER_NAMES: list[str] = [
    'PRISMA Health Swamp Rabbit Trail',
]


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

    file_path = (
        (metric_path / (pipe.target + '.' + filetype))
        if not (layer := greenville_county_params.get('layer', None))
        else fetch_layer(layer)
    )
    if not file_path.exists():
        raise FileNotFoundError(f"Path does not exist:\n{file_path}")

    df = gpd.read_file(file_path) if filetype == 'shp' else pd.read_csv(file_path)
    return df


def fetch_layer(layer: str) -> pathlib.Path:
    """
    Scrape the county GIS website.
    """
    job_id = submit_job(layer)
    job_path = download_job_output(job_id)
    job_dir_path = job_path.parent / job_id

    with zipfile.ZipFile(job_path, 'r') as zip_ref:
        zip_ref.extractall(job_dir_path)

    shapefiles = [
        (job_dir_path / filename)
        for filename in os.listdir(job_dir_path)
        if filename.endswith('.shp')
    ]
    if not shapefiles:
        raise FileNotFoundError(f"Could not find a shapefile for layer '{layer}'.")

    return shapefiles[0]
   

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
            #  'clipExtent': '1573370,1095411,1581953,1103186',
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
