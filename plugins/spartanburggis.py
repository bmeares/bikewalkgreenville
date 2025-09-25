#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Download geometry data from Spartanburg's ArcGIS instance.
"""

import meerschaum as mrsm

__version__ = '0.0.1'

BASE_URL: str = 'https://maps.spartanburgcounty.org/server/rest/services/GIS'


def fetch(pipe: mrsm.Pipe, **kwargs):
    """Return or yield dataframes."""
    import io
    import shutil

    gis_cf = pipe.parameters.get('spartanburggis', {})
    layer = gis_cf.get('layer', None)
    if not layer:
        raise ValueError("No layer configured.")

    requests, gpd, _ = mrsm.attempt_import('requests', 'geopandas', 'pyogrio')
    url = f"{BASE_URL}/{layer}/FeatureServer/0/query"

    response = requests.get(url, params={'f': 'pjson'})
    if not response:
        raise ValueError(f"Failed to retrieve layer:\n{layer.text}")

    gdf = gpd.read_file(response.text)

    if pipe.metric_key == 'trails' and pipe.location_key == 'dan':
        gdf = gdf.dissolve(by='Name').reset_index()

    return gdf
