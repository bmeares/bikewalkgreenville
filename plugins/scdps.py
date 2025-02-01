#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# vim:fenc=utf-8

"""
Fetch historical collision data.
"""

import os
import pathlib
from typing import Any

import meerschaum as mrsm
from meerschaum.utils.warnings import info
from meerschaum.config import get_plugin_config, write_plugin_config

__version__ = '0.0.1'
required: list[str] = ['openpyxl', 'pytz']


def setup():
    from meerschaum.config.paths import ROOT_DIR_PATH
    from meerschaum.utils.prompt import prompt
    cf = get_plugin_config() or {}
    if (existing_data_path := cf.get('data_path', None)):
        info(f"SCDPS data path set to {existing_data_path}")
        return True, "Success"
    default_data_path = ROOT_DIR_PATH.parent / 'data'
    default_data_path_str = default_data_path.as_posix() if default_data_path.exists() else None
    try:
        data_path_str = prompt("Path to SCDPS static data directory:", default=default_data_path_str)
    except (Exception, KeyboardInterrupt):
        return False, "Did not get the SCDPS data path."
    info(f"SCDPS data path set to {data_path_str}")
    cf['data_path'] = data_path_str
    write_plugin_config(cf)
    return True, "Success"


def register(pipe: mrsm.Pipe) -> dict[str, Any]:
    """Return the default parameters for a pipe."""
    if pipe.metric_key == 'fatalities':
        return {
            'columns': {
                'datetime': 'datetime',
                'crash_number': 'crash_number',
                'victim_mode': 'victim_mode',
            },
            'indices': {
                'iac': 'iac',
            },
            'dtypes': {
                'base_distance_offset': 'numeric',
                'crash_number': 'Int64',
                'decimal_degrees_longitude': 'numeric[12,9]',
                'decimal_degrees_latitude': 'numeric[12,9]',
                'latitude': 'Int64',
                'longitude': 'Int64',
                'lat_degrees': 'Int64',
                'lat_minutes': 'numeric',
                'lat_seconds': 'numeric',
                'lon_degrees': 'Int64',
                'lon_minutes': 'numeric',
                'lon_seconds': 'numeric',
            },
        }
    return {
        'columns': {
            'datetime': 'CrashDate',
            'accident_number': 'AccidentNumber',
            'object_id': 'OBJECTID',
        },
        'indices': {
            'county': 'County',
        },
        'dtypes': {
            'X': 'float64',
            'Y': 'float64',
            'OBJECTID': 'Int64',
            'AccidentNumber': 'Int64',
            'CrashDate': 'datetime64[ns]',
            'hour': 'Int64',
            'NumberOfUnits': 'Int64',
            'NumberOfFatalities': 'Int64',
            'NumberOfInjuries': 'Int64',
            'Possible_Injuries': 'Int64',
            'Suspected_Minor_Injuries': 'Int64',
            'Suspected_Serious_Injuries': 'Int64',
            'Latitude': 'numeric',
            'Longitude': 'numeric',
            'CMV': 'Int64',
            'TotalNumberOfOccupants': 'Int64',
            'Year': 'Int64',
            'MonthNumber': 'Int64',
        },
    }


def fetch(pipe: mrsm.Pipe, **kwargs) -> 'pd.DataFrame':
    """
    Fetch the 2017 to 2021 collisions data.
    """
    if pipe.metric_key == 'fatalities':
        return parse_fatalities_spreadsheets()
    return parse_collisions_gis_data()


def get_data_path() -> pathlib.Path:
    """Return the data path configured in the plugin settings."""
    cf = get_plugin_config()
    data_path_str = cf.get('data_path', None)
    if not data_path_str:
        raise FileNotFoundError("No data path defined. Run `setup plugin scdps`.")
    return pathlib.Path(data_path_str)


def parse_collisions_gis_data() -> 'pd.DataFrame':
    """
    Parse the Collisions data:
    https://opendata.arcgis.com/api/v3/datasets/52ae3a22b72740a29b9d7e98d4b395fc_16/downloads/data?format=csv&spatialRefId=4326&where=1=1
    """
    pd = mrsm.attempt_import('pandas')
    collisions_path = get_data_path() / 'collisions'
    if not collisions_path.exists():
        raise FileNotFoundError(f"Path does not exist: {collisions_path}")
    dataframes = []
    for filename in os.listdir(collisions_path):
        file_path = collisions_path / filename
        df = pd.read_csv(file_path, dtype=str).fillna(pd.NA)
        df['CrashDate'] = pd.to_datetime(df['CrashDate'])
        dataframes.append(df)
    return pd.concat(dataframes, ignore_index=True)


def parse_fatalities_spreadsheets() -> 'pd.DataFrame':
    """
    Parse the SCDPS Excel files and return a Pandas DataFrame.
    """
    (openpyxl, pytz) = mrsm.attempt_import('openpyxl', 'pytz', venv='scdps') 
    pd = mrsm.attempt_import('pandas')
    eastern = pytz.timezone("US/Eastern")

    fatalities_data_path = get_data_path() / 'fatalities'
    if not fatalities_data_path.exists():
        raise FileNotFoundError(f"Path does not exist: {fatalities_data_path}")

    dataframes = []
    for filename in os.listdir(fatalities_data_path):
        if not filename.endswith('.xlsx'):
            continue
        path = fatalities_data_path / filename
        victim_mode = filename.rstrip('.xlsx').split(' ')[:-1][-1].lower()
        df = pd.read_excel(path, dtype=str)
        df["time"] = df["time"].astype(str).str.zfill(4)
        df["datetime"] = pd.to_datetime(
            df["date"].astype(str) + " " + df["time"].str[:2] + ":" + df["time"].str[2:]
        )
        df["datetime"] = (
            df["datetime"].dt.tz_localize(eastern, ambiguous="NaT", nonexistent="NaT")
        ).dt.tz_convert("UTC")
        df['victim_mode'] = victim_mode
        dataframes.append(df)
    return pd.concat(dataframes, ignore_index=True)
