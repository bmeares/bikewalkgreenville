#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# vim:fenc=utf-8

"""
Download and sync collisions data.
"""

import meerschaum as mrsm
URL: str = 'https://opendata.arcgis.com/api/v3/datasets/52ae3a22b72740a29b9d7e98d4b395fc_16/downloads/data?format=csv&spatialRefId=4326&where=1=1'

required = ['pandas', 'duckdb']

def fetch(pipe: mrsm.Pipe, **kw) -> 'pd.DataFrame':
    """
    Download and parse the collisions CSV.
    """
    import pandas as pd
    import duckdb
    df = pd.read_csv(URL)
    return duckdb.query("SELECT * FROM df WHERE \"County\" = 'Greenville'").df()
