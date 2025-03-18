#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Parse the brokenspoke-analyzer result files.
"""

from typing import Any
import os
import meerschaum as mrsm

bwg = mrsm.Plugin('bwg')


def register(pipe: mrsm.Pipe) -> dict[str, Any]:
    """
    Return the default parameters.
    """
    params = {
        'columns': {
            'primary': 'id',
        },
        'indices': {
            'score_id': 'score_id',
        },
    } if pipe.metric_key == 'neighborhood_overall_scores' else {
        'columns': {
            'primary': 'id',
        },
        'indices': {
            'source_blockid10': 'source_blockid10',
            'target_blockid10': 'target_blockid10',
        },
    }
    return params


def fetch(pipe: mrsm.Pipe):
    """
    Parse the `brokenspoke-analyzer` CSV files.
    """
    pd = mrsm.attempt_import('pandas')
    data_path = bwg.module.get_data_path()
    results_path = (data_path / 'brokenspoke-analyzer_results') / 'results'
    greenville_path = results_path / 'united states' / 'south carolina' / 'greenville'

    paths = []
    for version_dir in os.listdir(greenville_path):
        version_dir_path = greenville_path / version_dir
        for filename in os.listdir(version_dir_path):
            if filename.endswith('.csv') and pipe.metric_key in filename:
                paths.append(version_dir_path / filename)

    dataframes = [
        pd.read_csv(path, dtype=str)
        for path in paths
    ]
    df = pd.concat(dataframes, ignore_index=True)
    return df
