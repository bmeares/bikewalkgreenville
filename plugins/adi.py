#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Ingest the ADI files.
"""

import os
import zipfile
import meerschaum as mrsm
from meerschaum.utils.warnings import info

bwg = mrsm.Plugin('bwg')


def fetch(pipe: mrsm.Pipe, **kwargs):
    """
    Ingest the ADI files.
    """
    if pipe.metric_key == 'nhgis':
        return (
            chunk
            for chunk in parse_nhgis_files()
        )

    return parse_adi_files()


def parse_adi_files():
    pd = mrsm.attempt_import('pandas')
    data_path = bwg.module.get_data_path()
    adi_path = data_path / 'ADI'
    
    dfs = []
    columns = ['year', 'GISJOIN', 'FIPS', 'ADI_NATRNK', 'ADI_STATERNK']
    for filename in os.listdir(adi_path):
        if not filename.endswith('.csv'):
            continue
        file_path = adi_path / filename
        df = pd.read_csv(file_path, dtype=str) 
        year = filename.split('_')[1]
        df['year'] = int(year)
        dfs.append(df[[col for col in columns if col in df.columns]])

    return pd.concat(dfs)


def parse_nhgis_files():
    gpd, pd = mrsm.attempt_import('geopandas', 'pandas')
    data_path = bwg.module.get_data_path()
    adi_path = data_path / 'ADI'
    zip_names = [
        filename
        for filename in os.listdir(adi_path)
        if filename.endswith('.zip') and filename.startswith('nhgis')
    ]
    dir_names = [filename[:(-1 * len('.zip'))] for filename in zip_names]
    for dir_name in dir_names:
        dir_path = adi_path / dir_name
        if dir_path.exists():
            continue

        zip_path = adi_path / (dir_name + '.zip')
        info(f"Extracting '{zip_path}'...")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(dir_path)

    for dir_name in dir_names:
        dir_path = adi_path / dir_name
        info(f"Reading '{dir_path}'...")
        year = int(dir_name[-4:])
        gdf = gpd.read_file(dir_path)
        cols_to_rename = [col for col in gdf.columns if col.endswith('10')]
        for col in cols_to_rename:
            gdf[col[:(-1 * len('10'))]] = gdf[col]
        cols_to_skip = cols_to_rename + [
            col for col in gdf.columns if col.lower().startswith('shape_')
        ]
        gdf['year'] = year
        yield gdf[[col for col in gdf.columns if col not in cols_to_skip]]
