#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Parse the Greenville County data and shapefiles.
"""

import meerschaum as mrsm

bwg = mrsm.Plugin('bwg')

required = ['geopandas', 'pyogrio', 'pandas']

def fetch(pipe: mrsm.Pipe, **kwargs):
    """
    Parse the `greenville county` shapefiles.
    """
    pd, gpd, _ = mrsm.attempt_import(
        'pandas', 'geopandas', 'pyogrio',
        venv='greenville-county',
        lazy=False,
    )
    data_path = bwg.module.get_data_path()
    county_path = data_path / 'greenville county'
    metric_path = county_path / pipe.metric_key

    greenville_county_params = pipe.parameters.get('greenville-county', {})
    filetype = greenville_county_params.get('filetype', 'shp')

    file_path = metric_path / (pipe.target + '.' + filetype)
    if not file_path.exists():
        raise FileNotFoundError(f"Path does not exist:\n{file_path}")

    df = gpd.read_file(file_path) if filetype == 'shp' else pd.read_csv(file_path)
    return df
