#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Download shapefiles from ArcGIS.
"""

from datetime import datetime
import meerschaum as mrsm
from meerschaum.utils.warnings import dprint

__version__ = '0.0.1'

required: list[str] = []

BASE_URL: str = 'https://hub.arcgis.com/api/v3'


def fetch(
    pipe: mrsm.Pipe,
    begin: datetime | None = None,
    end: datetime | None = None,
    debug: bool = False,
    **kwargs
):
    """Return or yield dataframes."""
    import io
    import shutil

    arcgis_cf = pipe.parameters.get('arcgis', {})
    dataset_id = arcgis_cf.get('dataset', None)
    srid = arcgis_cf.get('srid', 4326)
    if not dataset_id:
        raise ValueError("No dataset configured.")

    requests, gpd = mrsm.attempt_import('requests', 'geopandas')
    url = f"{BASE_URL}/datasets/{dataset_id}_0/downloads/data/"

    if debug:
        dprint("Download shapefile...")

    with requests.get(
        url,
        stream=True,
        params={
            'format': 'shp',
            'where': '1=1',
            'spatialRefId': str(srid),
        },
    ) as response:
        response.raise_for_status()

        buffer = io.BytesIO()
        response.raw.decode_content = True
        shutil.copyfileobj(response.raw, buffer)
        buffer.seek(0)

        if debug:
            dprint("Download complete.")

        gdf = gpd.read_file(buffer)

    return gdf
