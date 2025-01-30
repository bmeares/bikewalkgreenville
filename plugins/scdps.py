#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# vim:fenc=utf-8

"""
Fetch historical collision data.
"""

import meerschaum as mrsm
from meerschaum.utils.warnings import info

URL: str = 'https://opendata.arcgis.com/api/v3/datasets/52ae3a22b72740a29b9d7e98d4b395fc_16/downloads/data?format=csv&spatialRefId=4326&where=1=1'


def fetch(pipe: mrsm.Pipe, **kw) -> 'pd.DataFrame':
    """
    Fetch the 2017 to 2021 collisions data.
    """
    from meerschaum.utils.misc import wget
    from meerschaum.config.paths import CACHE_RESOURCES_PATH
    pd = mrsm.attempt_import('pandas')
    dest_path = CACHE_RESOURCES_PATH / 'scdps-collisions.csv'
    if not dest_path.exists():
        info("Downloading data from SCDPS...")
        _ = wget(URL, dest_path)
        info("Finished downloading.")
    df = pd.read_csv(dest_path, dtype=str)
    return df
