#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Parse the replica data.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Union, List, Dict
import meerschaum as mrsm
from meerschaum.config import get_plugin_config, write_plugin_config

__version__ = '0.0.1'

required: list[str] = []
bwg = mrsm.Plugin('bwg')


def register(pipe: mrsm.Pipe):
    """Return the default parameters for a new pipe."""
    return {
        'columns': {
            'datetime': None,
        }
    }


def fetch(
    pipe: mrsm.Pipe,
    begin: datetime | None = None,
    end: datetime | None = None,
    **kwargs
):
    """Return or yield dataframes."""
    pd = mrsm.attempt_import('pandas', lazy=False)
    replica_cf = pipe.parameters.get('replica', {})
    dataset_name = replica_cf.get('dataset', pipe.metric_key)
    data_path = bwg.module.get_data_path()
    replica_path = data_path / 'Replica'
    dataset_folders = [
        (replica_path / name)
        for name in os.listdir(replica_path)
        if name.startswith(dataset_name)
    ]
    file_paths = []
    for folder_path in dataset_folders:
        for filename in os.listdir(folder_path):
            if filename.endswith('.csv'):
                file_paths.append(folder_path / filename)

    dfs = []
    for path in file_paths:
        df = pd.read_csv(path, dtype=str)
        df['filename'] = path.name
        dfs.append(df)

    return pd.concat(dfs)
