#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Parse the Strava Metro data.
"""

import os
from typing import Any
import pathlib
import meerschaum as mrsm


required = []
bwg = mrsm.Plugin('bwg')


def register(pipe: mrsm.Pipe) -> dict[str, Any]:
    """
    Return the pipe parameters.
    """
    columns = {
        'edge_uid': 'edge_uid',
        'osm_reference_id': 'osm_reference_id',
    }
    if pipe.metric_key != 'osm_ids':
        columns['datetime'] = 'hour'
    return {
        'columns': columns,
        'indices': {
            'activity_type': 'activity_type',
        },
    }



def fetch(pipe: mrsm.Pipe):
    """
    Parse the Strava Metro CSV files and return the dataframes.
    """
    if pipe.metric_key == 'osm_ids':
        return fetch_srt_ids()

    pd = mrsm.attempt_import('pandas')
    data_path = bwg.module.get_data_path()
    strava_path = data_path / 'strava'
    file_paths = []
    for dir_name in os.listdir(strava_path):
        dir_path = strava_path / dir_name
        if not dir_path.is_dir():
            continue
        for file_name in os.listdir(dir_path):
            if not file_name.endswith('.csv'):
                continue
            file_paths.append(dir_path / file_name)
    
    dataframes = [pd.read_csv(file_path) for file_path in file_paths]
    df = pd.concat(dataframes, ignore_index=True)
    return df


def fetch_srt_ids():
    """
    Return the distinct OSM IDs for the Swamp Rabbit Trail from the sample data file.
    """
    pd = mrsm.attempt_import('pandas')
    data_path = bwg.module.get_data_path()
    strava_path = data_path / 'strava'
    edges_dir_path = strava_path / '199_edges_hourly_2024-12-01-2024-12-31_ped'
    csv_path = edges_dir_path / 'f66a669a66f7938a2678ed2f45ee552ffc51e91458d257c6f24b182306c044a1-1740687426145.csv'
    if not csv_path.exists():
        raise FileNotFoundError(f"Cannot find Strava Metro sample file:\n{csv_path}")

    df = pd.read_csv(csv_path)
    return df[['osm_reference_id', 'edge_uid']].drop_duplicates(ignore_index=True)
