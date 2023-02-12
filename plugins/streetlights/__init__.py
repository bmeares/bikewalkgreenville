#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# vim:fenc=utf-8

"""
Ingest the traffic data.
"""

__version__ = '0.0.1'
required = ['pandas', 'python-dateutil']

import os
import pathlib
from typing import Dict, Any, List, Union

DATA_DIR_PATH: pathlib.Path = pathlib.Path(__file__).parent / 'data'

def register(pipe: 'mrsm.Pipe', **kw) -> Dict[str, Any]:
    """
    Return the default parameters for this pipe.
    """
    filename_pattern = (
        'traffic_'
        + pipe.metric_key + '_' + pipe.location_key
        + '_all.csv'
    )
    return {
        'columns': {
            'datetime': 'begin_date',
            'zone': 'Zone ID',
            'grid': 'Grid ID',
            'block': 'Block Group ID',
        },
        'dtypes': {
            'Zone ID': 'Int64',
            'Grid ID': 'Int64',
            'Average Daily Zone Traffic (StL Index)': 'Int64',
            'Block Group ID': 'Int64',
        },
        'traffic': {
            'pattern': filename_pattern,
        },
    }


def fetch(pipe: 'mrsm.Pipe', **kw) -> 'pd.DataFrame':
    """
    Read the CSVs and return a Pandas DataFrame.
    """
    csv_file_paths = get_csv_file_paths(pipe)
    import pandas as pd
    import dateutil.parser

    dfs = []
    for file_path in csv_file_paths:
        df = pd.read_csv(file_path)
        if 'Data Periods' not in df.columns:
            print(df)
            print(file_path)
            continue
        df['begin_date'] = df['Data Periods'].apply(
            lambda x: x.split(' - ')[0]
        ).apply(
            lambda x: dateutil.parser.parse(x)
        )
        df['end_date'] = df['Data Periods'].apply(
            lambda x: x.split(' - ')[1]
        ).apply(
            lambda x: dateutil.parser.parse(x)
        )
        dfs.append(df)
    return pd.concat(dfs)


def get_csv_file_paths(pipe: 'mrsm.Pipe') -> List[pathlib.Path]:
    """
    Get a list of CSV files to be parsed.
    If nothing can be found, raise an exception.
    """
    if not DATA_DIR_PATH.exists():
        raise Exception(f"Data directory '{DATA_DIR_PATH}' does not exist.")

    filename_pattern = pipe.parameters.get('traffic', {}).get('pattern', None)
    if not filename_pattern:
        raise Exception(f"No filename pattern set for {pipe}.")

    csv_file_paths = [
        DATA_DIR_PATH / filename
        for filename in os.listdir(DATA_DIR_PATH)
        if filename_pattern in filename
    ]
    if not csv_file_paths:
        raise Exception(f"No CSV files were found in '{DATA_DIR_PATH}'.")
    return csv_file_paths
