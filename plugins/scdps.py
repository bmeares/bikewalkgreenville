#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# vim:fenc=utf-8

"""
Fetch historical collision data.
"""

import meerschaum as mrsm
from meerschaum.utils.misc import generate_password
from meerschaum.utils.warnings import info

URL: str = 'https://opendata.arcgis.com/api/v3/datasets/52ae3a22b72740a29b9d7e98d4b395fc_16/downloads/data?format=csv&spatialRefId=4326&where=1=1'

def fetch(pipe: mrsm.Pipe, **kw) -> 'pd.DataFrame':
    """
    Fetch the 2017 to 2021 collisions data.
    """
    from meerschaum.utils.packages import import_pandas
    pd = import_pandas()

    session_id = generate_password(6)
    conn = mrsm.get_connector(f"sql:{session_id}", flavor='duckdb', database=':memory:')

    info("Downloading data from SCDPS...")
    data = pd.read_csv(URL, dtype=str)
    info(f"Finished downloading.")

    temp_pipe = mrsm.Pipe(
        connector = str(conn),
        metric = pipe.metric_key,
        location = pipe.location_key,
        instance = pipe.instance_keys,
        parameters = pipe.parameters,
    )
    df = temp_pipe.fetch(**kw)
    return df
