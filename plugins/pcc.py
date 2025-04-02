#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Parse the data from PCC.
"""

import meerschaum as mrsm

required: list[str] = ['pandas', 'pyarrow', 'geopandas', 'pyogrio']

bwg = mrsm.Plugin('bwg')


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
    unzip_path = pcc_path / 'PCC_Road_Stress_Public'
    file_path = unzip_path / 'all_roads_with_fc_web.shp'
    return gpd.read_file(file_path)
