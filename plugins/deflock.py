#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fetch data from deflock.me
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Union, List, Dict
import meerschaum as mrsm
from meerschaum.config import get_plugin_config, write_plugin_config

__version__ = '0.0.1'

REGIONS_URL: str = 'https://cdn.deflock.me/regions/index.json'


def register(pipe: mrsm.Pipe):
    """Return the default parameters for a new pipe."""
    return {
        'columns': {
            'primary': 'id',
        },
        'dtypes': {
            'tags': 'json',
            'lat': 'numeric',
            'lon': 'numeric',
        },
    }


def fetch(
    pipe: mrsm.Pipe,
    begin: datetime | None = None,
    end: datetime | None = None,
    **kwargs
):
    """Return or yield dataframes."""
    requests = mrsm.attempt_import('requests')
    regions_response = requests.get(REGIONS_URL)
    if not regions_response:
        raise ValueError("Could not fetch regions.")

    regions_data = regions_response.json()
    expiration_utc = regions_data.get('expiration_utc', None)
    if not expiration_utc:
        raise ValueError("Could not get `expiration_utc`.")

    tile_url = regions_data['tile_url']
    regions = [region_str.split('/', maxsplit=1) for region_str in regions_data['regions']]
    urls = [
        tile_url.format(lat=lat, lon=lon)
        for lat, lon in regions
    ]

    for url in urls:
        response = requests.get(url, params={'v': str(expiration_utc)})
        if response:
            yield response.json()
