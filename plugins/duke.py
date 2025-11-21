#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fetch data from the Duke Lighting Map.
"""

import meerschaum as mrsm
from meerschaum.utils.warnings import warn, info

__version__ = '0.0.1'

BASE_URL: str = 'https://salor-api.duke-energy.app/streetlights'
DEFAULT_BOUNDS: list[tuple[float, float]] = [
    (34.484337813842046, -82.76415771006502), # sw
    (35.21548588615223, -82.14558853156991), # ne
]


def fetch(
    pipe: mrsm.Pipe,
    workers: int | None = None,
    **kwargs
):
    """Return or yield dataframes."""
    requests = mrsm.attempt_import('requests')
    duke_params = pipe.parameters.get('duke', {})
    bounds = duke_params.get('bounds', DEFAULT_BOUNDS)
    step_deg = duke_params.get('step_deg', 0.1)

    def make_request(swne_latlon: tuple[tuple[float, float], tuple[float, float]]):
        sw_latlon, ne_latlon = swne_latlon
        sw_lat, sw_lon = sw_latlon
        ne_lat, ne_lon = ne_latlon
        info(f"Requesting bounds: {swne_latlon}")
        try:
            response = requests.get(
                BASE_URL,
                params={
                    'swLat': sw_lat,
                    'swLong': sw_lon,
                    'neLat': ne_lat,
                    'neLong': ne_lon,
                },
            )
            data = response.json()
            if isinstance(data, dict) and 'message' in data:
                warn(f"No data returned for bounds: {sw_latlon=} {ne_latlon=}")
                return []
            return data
        except Exception as e:
            warn(f"Failed to request for bounds: {sw_latlon=} {ne_latlon=}:\n{e}")
            return []

    for chunk_bounds in iterate_bbox(bounds[0], bounds[1], step_deg=step_deg):
        yield make_request(chunk_bounds)


def iterate_bbox(sw_latlon: tuple[float, float], ne_latlon: tuple[float, float], step_deg=0.01):
    lat = sw_latlon[0]
    sw_lat, sw_lon = sw_latlon
    ne_lat, ne_lon = ne_latlon
    while lat < ne_lat:
        next_lat = min(lat + step_deg, ne_lat)
        lon = sw_lon

        while lon < ne_lon:
            next_lon = min(lon + step_deg, ne_lon)
            yield (lat, lon), (next_lat, next_lon)
            lon = next_lon

        lat = next_lat
