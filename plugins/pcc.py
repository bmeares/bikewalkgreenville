#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Parse the data from PCC.
"""

import meerschaum as mrsm

__version__ = '0.0.1'

required: list[str] = ['pandas', 'pyarrow', 'geopandas', 'pyogrio']

bwg = mrsm.Plugin('bwg')

KNOWN_DTYPES: dict[str, str] = {
    'COUNTY_ID': 'Int32',
    'ROUTE_NUMB': 'Int32',
    'fc_type': 'Int8',
}


def fetch(pipe: mrsm.Pipe):
    """
    Parse and return the shapefile data.
    """
    pd, _, gpd, _ = mrsm.attempt_import(
        'pandas', 'pyarrow', 'geopandas', 'pyogrio',
        venv='pcc',
        lazy=False,
    )
    data_path = bwg.module.get_data_path()
    pcc_path = data_path / 'Road Stress GIS Data from PCC'
    zip_path = pcc_path / 'PCC_Road_Stress_Public.zip'
    gdf = gpd.read_file(zip_path)
    for col, typ in KNOWN_DTYPES.items():
        gdf[col] = gdf[col].astype(typ)
    return gdf
