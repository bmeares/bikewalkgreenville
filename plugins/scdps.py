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


bwg = mrsm.Plugin('bwg')


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


def fetch(pipe: mrsm.Pipe, **kwargs) -> 'pd.DataFrame':
    """
    Fetch the 2017 to 2021 collisions data.
    """
    if pipe.metric_key == 'fatalities':
        return parse_fatalities_spreadsheets()
    if pipe.metric_key == 'UNITS':
        return parse_units_file()
    if pipe.metric_key == 'OCCUPANT':
        return parse_occupant_file()
    if pipe.metric_key == 'LOCATION':
        return parse_location_file()
    return parse_collisions_gis_data()


def parse_units_file():
    scdps_2024_path = bwg.module.get_data_path() / 'scdps' / 'SCDPS_2024'
    pd = mrsm.attempt_import('pandas')
    file_path = scdps_2024_path / 'Greenville County UNITS.csv'
    return pd.read_csv(file_path, dtype='str')


def parse_occupant_file():
    scdps_2024_path = bwg.module.get_data_path() / 'scdps' / 'SCDPS_2024'
    pd = mrsm.attempt_import('pandas')
    file_path = scdps_2024_path / 'Greenville County Stats OCCUPANT.csv'
    return pd.read_csv(file_path, dtype='str')


def parse_location_file():
    scdps_2024_path = bwg.module.get_data_path() / 'scdps' / 'SCDPS_2024'
    pd = mrsm.attempt_import('pandas')
    file_path = scdps_2024_path / 'Greenville County LOCATION.csv'
    return pd.read_csv(file_path, dtype='str')


def parse_collisions_gis_data() -> 'pd.DataFrame':
    """
    Parse the Collisions data:
    https://opendata.arcgis.com/api/v3/datasets/52ae3a22b72740a29b9d7e98d4b395fc_16/downloads/data?format=csv&spatialRefId=4326&where=1=1
    """
    pd = mrsm.attempt_import('pandas')
    collisions_path = bwg.module.get_data_path() / 'collisions'
    if not collisions_path.exists():
        raise FileNotFoundError(f"Path does not exist: {collisions_path}")
    dataframes = []
    for filename in os.listdir(collisions_path):
        file_path = collisions_path / filename
        df = pd.read_csv(file_path, dtype=str).fillna(pd.NA)
        df['CrashDate'] = pd.to_datetime(df['CrashDate'])
        dataframes.append(df)
    return pd.concat(dataframes, ignore_index=True)


def parse_2009_data() -> 'pd.DataFrame':
    """
    Parse the 2009 data files.
    """
    (openpyxl, pytz) = mrsm.attempt_import('openpyxl', 'pytz', venv='scdps') 
    pd = mrsm.attempt_import('pandas')
    eastern = pytz.timezone("US/Eastern")

    scdps_data_path = bwg.module.get_data_path() / 'scdps'
    if not scdps_data_path.exists():
        raise FileNotFoundError(f"Path does not exist: {scdps_data_path}")




def parse_fatalities_spreadsheets() -> 'pd.DataFrame':
    """
    Parse the SCDPS Excel files and return a Pandas DataFrame.
    """
    (openpyxl, pytz) = mrsm.attempt_import('openpyxl', 'pytz', venv='scdps') 
    pd = mrsm.attempt_import('pandas')
    eastern = pytz.timezone("US/Eastern")

    fatalities_data_path = bwg.module.get_data_path() / 'fatalities'
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
