#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Download a shared Google Map.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Union, List, Dict
from io import BytesIO

import meerschaum as mrsm

__version__ = '0.0.2'

required: list[str] = ['requests', 'geopandas', 'shapely']

DOWNLOAD_URL_TEMPLATE: str = 'https://www.google.com/maps/d/u/0/kml?mid={map_id}&forcekml=1'


def register(pipe: mrsm.Pipe):
    """Return the default parameters for a new pipe."""
    from meerschaum.utils.prompt import prompt
    map_id = pipe.parameters.get('gmaps', {}).get('map_id') or prompt("Google Maps ID (`mid`):")
    return {
        'gmaps': {
            'map_id': map_id,
        },
        'columns': {
        }
    }


def fetch(
    pipe: mrsm.Pipe,
    **kwargs
):
    """Return or yield dataframes."""
    import xml.etree.ElementTree as ET
    requests, gpd, shapely = mrsm.attempt_import('requests', 'geopandas', 'shapely', venv='gmaps')

    map_id = pipe.parameters.get('gmaps', {}).get('map_id', None)
    if not map_id:
        raise ValueError("Missing Map ID.")

    url = DOWNLOAD_URL_TEMPLATE.format(map_id=map_id)
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    docs = []
    for pm in root.findall('.//kml:Placemark', ns):
        name = pm.find('kml:name', ns)
        coords = pm.find('.//kml:coordinates', ns)
        if coords is not None and coords.text:
            coords_list = [
                tuple(map(float, c.split(',')))
                for c in coords.text.strip().split()
            ]
            if len(coords_list) > 1:
                docs.append({
                    "Name": name.text if name is not None else "Unnamed",
                    "geometry": shapely.LineString(coords_list)
                })

    gdf = gpd.GeoDataFrame(docs, crs="EPSG:4326")
    return gdf
