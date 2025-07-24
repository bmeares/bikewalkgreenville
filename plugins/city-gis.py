#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Implement the plugin 'city-gis'.

See the Writing Plugins guide for more information:
https://meerschaum.io/reference/plugins/writing-plugins/
"""

from datetime import datetime
import meerschaum as mrsm

__version__ = '0.0.1'

required = ['geopandas', 'pyogrio', 'pandas[pyarrow]']

bwg = mrsm.Plugin('bwg')

FEAT_CODES: dict[str, dict[int, str]] = {
    'Parking': {
        121: 'Paved',
        122: 'Unpaved',
        123: 'Background',
        124: 'Residential Driveway',
        127: 'Under Construction',
    },
}


def fetch(
    pipe: mrsm.Pipe,
    begin: datetime | None = None,
    end: datetime | None = None,
    **kwargs
):
    """Return or yield dataframes."""
    pd, _, gpd, _ = mrsm.attempt_import(
        'pandas', 'pyarrow', 'geopandas', 'pyogrio',
        venv='city-gis',
        lazy=False,
    )

    if pipe.metric_key == 'feature_codes':
        docs = []
        for metric_key, feat_codes_names in FEAT_CODES.items():
            docs.extend([
                {
                    'FEAT_CODE': feat_code,
                    'feat_name': feat_name,
                    'metric_key': pipe.metric_key,
                }
                for feat_code, feat_name in feat_codes_names.items()
            ])

        return docs

    data_path = bwg.module.get_data_path()
    city_path = data_path / 'city of greenville'
    shapefiles_path = (city_path / 'Data_Shapefiles')

    city_params = pipe.parameters.get('city', {})
    filetype = city_params.get('filetype', 'shp')

    file_path = shapefiles_path / (pipe.metric_key + '.' + filetype)
    if not file_path.exists():
        raise FileNotFoundError(f"Path does not exist:\n{file_path}")

    df = gpd.read_file(file_path) if filetype == 'shp' else pd.read_csv(file_path)
    return df
