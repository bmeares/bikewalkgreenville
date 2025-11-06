#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Implement the plugin 'city-gis'.

See the Writing Plugins guide for more information:
https://meerschaum.io/reference/plugins/writing-plugins/
"""

import os
import shutil
import zipfile
from datetime import datetime

import meerschaum as mrsm
from meerschaum.plugins import make_action

__version__ = '0.0.1'

required = ['geopandas', 'pyogrio', 'pandas[pyarrow]']

bwg = mrsm.Plugin('bwg')

SHAPEFILES_URL: str = "https://citygis.greenvillesc.gov/GISDataDownload/Data_Shapefiles.zip"
FEAT_CODES: dict[str, dict[str, str]] = {
    'Parking': {
        '121': 'Paved',
        '122': 'Unpaved',
        '123': 'Background',
        '124': 'Residential Driveway',
        '127': 'Under Construction',
    },
    'MajorWaterBodies': {
        '80': 'River',
        '81': 'Water Body',
        '82': 'Marsh',
        '83': 'Island',
        '84': 'Waterfall',
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
    layer = city_params.get('layer', pipe.metric_key)

    file_path = shapefiles_path / (layer + '.' + filetype)
    if not file_path.exists():
        raise FileNotFoundError(f"Path does not exist:\n{file_path}")

    df = gpd.read_file(file_path) if filetype == 'shp' else pd.read_csv(file_path)
    return df


@make_action
def fetch_city_gis(force: bool = False, **kwargs) -> mrsm.SuccessTuple:
    """
    Download the City of Greenville's GIS data.
    """
    from meerschaum.utils.misc import wget
    data_path = bwg.module.get_data_path()
    city_path = data_path / 'city of greenville'
    zip_path = city_path / 'Data_Shapefiles.zip'
    shapefiles_path = (city_path / 'Data_Shapefiles')
    city_path.mkdir(parents=True, exist_ok=True)

    if shapefiles_path.exists():
        if force:
            if zip_path.exists():
                zip_path.unlink()
            shutil.rmtree(shapefiles_path)
        else:
            return True, "Already downloaded. Run again with `--force` to overwrite the shapefiles."

    wget(SHAPEFILES_URL, zip_path) 
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(city_path)

    zip_path.unlink()
    return True, "Success"
