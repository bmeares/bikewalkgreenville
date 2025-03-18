#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Manage BWG plugins.
"""

import pathlib
from meerschaum.config import get_plugin_config, write_plugin_config
from meerschaum.utils.warnings import warn, info


def setup():
    from meerschaum.config.paths import ROOT_DIR_PATH
    from meerschaum.utils.prompt import prompt
    cf = get_plugin_config() or {}
    if (existing_data_path := cf.get('data_path', None)):
        info(f"BWG data path set to {existing_data_path}")
        return True, "Success"
    default_data_path = ROOT_DIR_PATH.parent / 'data'
    default_data_path_str = default_data_path.as_posix() if default_data_path.exists() else None
    try:
        data_path_str = prompt("Path to BWG static data directory:", default=default_data_path_str)
    except (Exception, KeyboardInterrupt):
        return False, "Did not get the SCDPS data path."
    info(f"BWG data path set to {data_path_str}")
    cf['data_path'] = data_path_str
    write_plugin_config(cf)
    return True, "Success"


def get_data_path() -> pathlib.Path:
    """Return the data path configured in the plugin settings."""
    from meerschaum.config.paths import ROOT_DIR_PATH
    cf = get_plugin_config()
    data_path_str = cf.get('data_path', None)
    if not data_path_str:
        raise FileNotFoundError("No data path defined. Run `setup plugin bwg`.")
    data_path_str = data_path_str.replace('{MRSM_ROOT_DIR}', ROOT_DIR_PATH.as_posix())
    return pathlib.Path(data_path_str)
