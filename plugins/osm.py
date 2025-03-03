#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Fetch data from OpenStreetMap.
"""

required = ['OSMPythonTools']


import meerschaum as mrsm
from meerschaum.actions import make_action


@make_action
def explore_osm(action) -> mrsm.SuccessTuple:
    """
    """
    if not action:
        return False, "Provide an OpenStreetMap ID."
    way = query_osm_id(int(action[0]))
    mrsm.pprint(way)
    return True, "Success"


def query_osm_id(osm_id: int):
    OSMPythonTools_api = mrsm.attempt_import('OSMPythonTools.api', venv='osm')
    Api = OSMPythonTools_api.Api
    api = Api()
    way = api.query(f"way/{osm_id}")
    return way
