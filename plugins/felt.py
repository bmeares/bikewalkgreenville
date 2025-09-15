#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sync pipes to Felt layers.
"""

import os
import tempfile
from datetime import datetime
from typing import Any, Union

import meerschaum as mrsm
from meerschaum.connectors import InstanceConnector, make_connector

__version__ = '0.1.0'

required: list[str] = ['felt-python']


@make_connector
class FeltConnector(InstanceConnector):
    """Implement 'felt' connectors."""

    REQUIRED_ATTRIBUTES: list[str] = ['token', 'map_id', 'instance_keys']
    OPTIONAL_ATTRIBUTES: list[str] = ['project_id']

    @property
    def instance_connector(self):
        """
        Return the instance connector used to store metadata for the pipes.
        """
        return mrsm.get_connector(self.instance_keys)

    def fetch(
        self,
        pipe: mrsm.Pipe,
        debug: bool = False,
        **kwargs
    ):
        """
        Extract a layer and return a dataframe.
        """
        felt_python = mrsm.attempt_import('felt_python', venv='felt')
        gpd = mrsm.attempt_import('geopandas')
        fetch_cf = pipe.parameters.get('fetch', {})
        layer_id = fetch_cf.get('layer_id')
        layer_name = fetch_cf.get('layer', fetch_cf.get('layer_name'))
        if not layer_id and not layer_name:
            raise ValueError(f"No layer ID or layer name configured for {pipe}.")

        layer_id = layer_id or self.get_target_layer_id(layer_name, debug=debug)
        if not layer_id:
            raise ValueError(f"Could not determine layer ID for {pipe}.")

        with tempfile.TemporaryDirectory() as tempdir:
            file_name = os.path.join(tempdir, f'{layer_id}.gpkg')
            felt_python.download_layer(
                self.map_id,
                layer_id,
                file_name=file_name,
                api_token=self.token,
            )
            gdf = gpd.read_file(file_name, layer='parsed')

        cols_to_del = [col for col in gdf.columns if col.startswith('felt:')]
        for col in cols_to_del:
            del gdf[col]

        return gdf

    def register_pipe(
        self,
        pipe: mrsm.Pipe,
        debug: bool = False,
        **kwargs: Any
    ) -> mrsm.SuccessTuple:
        """
        Insert the pipe's attributes into the internal `pipes` table.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe to be registered.

        Returns
        -------
        A `SuccessTuple` of the result.
        """
        return self.instance_connector.register_pipe(pipe, debug=debug, **kwargs)

    def get_pipe_attributes(
        self,
        pipe: mrsm.Pipe,
        debug: bool = False,
        **kwargs: Any
    ) -> dict[str, Any]:
        """
        Return the pipe's document from the internal `pipes` collection.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe whose attributes should be retrieved.

        Returns
        -------
        The document that matches the keys of the pipe.
        """
        return self.instance_connector.get_pipe_attributes(pipe, debug=debug, **kwargs)

    def get_pipe_id(
        self,
        pipe: mrsm.Pipe,
        debug: bool = False,
        **kwargs: Any
    ) -> str | int | None:
        """
        Return the ID for the pipe if it exists.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe whose ID to fetch.

        Returns
        -------
        The ID for the pipe or `None`.
        """
        return self.instance_connector.get_pipe_id(pipe, debug=debug, **kwargs)

    def edit_pipe(
        self,
        pipe: mrsm.Pipe,
        debug: bool = False,
        **kwargs: Any
    ) -> mrsm.SuccessTuple:
        """
        Edit the attributes of the pipe.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe whose in-memory parameters must be persisted.

        Returns
        -------
        A `SuccessTuple` indicating success.
        """
        return self.instance_connector.edit_pipe(pipe, debug=debug, **kwargs)

    def delete_pipe(
        self,
        pipe: mrsm.Pipe,
        debug: bool = False,
        **kwargs: Any
    ) -> mrsm.SuccessTuple:
        """
        Delete a pipe's registration from the `pipes` collection.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe to be deleted.

        Returns
        -------
        A `SuccessTuple` indicating success.
        """
        return self.instance_connector.delete_pipe(pipe, debug=debug, **kwargs)

    def fetch_pipes_keys(
        self,
        connector_keys: list[str] | None = None,
        metric_keys: list[str] | None = None,
        location_keys: list[str] | None = None,
        tags: list[str] | None = None,
        debug: bool = False,
        **kwargs: Any
    ) -> list[tuple[str, str, str]]:
        """
        Return a list of tuples for the registered pipes' keys according to the provided filters.

        Parameters
        ----------
        connector_keys: list[str] | None, default None
            The keys passed via `-c`.

        metric_keys: list[str] | None, default None
            The keys passed via `-m`.

        location_keys: list[str] | None, default None
            The keys passed via `-l`.

        tags: List[str] | None, default None
            Tags passed via `--tags` which are stored under `parameters:tags`.

        Returns
        -------
        A list of connector, metric, and location keys in tuples.
        You may return the string "None" for location keys in place of nulls.

        Examples
        --------
        >>> import meerschaum as mrsm
        >>> conn = mrsm.get_connector('example:demo')
        >>> 
        >>> pipe_a = mrsm.Pipe('a', 'demo', tags=['foo'], instance=conn)
        >>> pipe_b = mrsm.Pipe('b', 'demo', tags=['bar'], instance=conn)
        >>> pipe_a.register()
        >>> pipe_b.register()
        >>> 
        >>> conn.fetch_pipes_keys(['a', 'b'])
        [('a', 'demo', 'None'), ('b', 'demo', 'None')]
        >>> conn.fetch_pipes_keys(metric_keys=['demo'])
        [('a', 'demo', 'None'), ('b', 'demo', 'None')]
        >>> conn.fetch_pipes_keys(tags=['foo'])
        [('a', 'demo', 'None')]
        >>> conn.fetch_pipes_keys(location_keys=[None])
        [('a', 'demo', 'None'), ('b', 'demo', 'None')]
        
        """
        return self.instance_connector.fetch_pipes_keys(
            connector_keys=connector_keys,
            metric_keys=metric_keys,
            location_keys=location_keys,
            tags=tags,
            debug=debug,
            **kwargs
        )

    def get_pipe_layer_id(
        self,
        pipe: mrsm.Pipe,
        check_parameters: bool = True,
        save: bool = True,
        debug: bool = False,
    ) -> Union[str, None]:
        """
        Return the pipe's layer ID (either from `parameters` or deduced from the `target`).
        """
        felt_cf = pipe.parameters.get('felt', {}) if check_parameters else {}
        configured_layer_id = felt_cf.get('layer_id', None)
        if configured_layer_id:
            return configured_layer_id

        layer_id = self.get_target_layer_id(pipe.target, debug=debug)
        if save and check_parameters:
            pipe.update_parameters({'felt': {'layer_id': layer_id}})

        return layer_id

    def get_target_layer_id(self, target: str, debug: bool = False) -> Union[str, None]:
        """
        Return a layer ID for a target name, if one exists.
        """
        felt_python = mrsm.attempt_import('felt_python', venv='felt')
        layers = felt_python.list_layers(map_id=self.map_id, api_token=self.token)
        layer_id = None

        for layer in layers:
            if layer.get('name') == target:
                layer_id = layer['id']
                break

        if layer_id:
            return layer_id

        groups = felt_python.list_layer_groups(self.map_id, api_token=self.token)
        for group in groups:
            for layer in group['layers']:
                if layer.get('name') == target and layer.get('type', 'layer') == 'layer':
                    layer_id = layer['id']
                    break

        return layer_id

    def pipe_exists(
        self,
        pipe: mrsm.Pipe,
        debug: bool = False,
        **kwargs: Any
    ) -> bool:
        """
        Check whether a pipe's target table exists.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe to check whether its table exists.

        Returns
        -------
        A `bool` indicating the table exists.
        """
        return (
            self.get_pipe_layer_id(
                pipe,
                check_parameters=False,
                save=False,
                debug=debug,
            ) is not None
        )

    def drop_pipe(
        self,
        pipe: mrsm.Pipe,
        debug: bool = False,
        **kwargs: Any
    ) -> mrsm.SuccessTuple:
        """
        Drop a pipe's collection if it exists.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe to be dropped.

        Returns
        -------
        A `SuccessTuple` indicating success.
        """
        layer_id = self.get_pipe_layer_id(pipe, debug=debug)
        if layer_id is None:
            return True, "Nothing to drop."

        felt_python = mrsm.attempt_import('felt_python', venv='felt')
        try:
            felt_python.delete_layer(self.map_id, layer_id, self.token)
        except Exception as e:
            return False, f"Failed to delete layer '{layer_id}':\n{e}"

        return True, "Success"

    def sync_pipe(
        self,
        pipe: mrsm.Pipe,
        df: 'pd.DataFrame',
        check_existing: bool = True,
        debug: bool = False,
        **kwargs: Any
    ) -> mrsm.SuccessTuple:
        """
        Upsert new documents into the pipe's target table.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe to which the data should be upserted.

        df: pd.DataFrame
            The data to be synced.

        Returns
        -------
        A `SuccessTuple` indicating success.
        """
        felt_python = mrsm.attempt_import('felt_python', venv='felt')
        target = pipe.target
        layer_id = self.get_pipe_layer_id(pipe, debug=debug)
        rowcount = len(df)

        if not pipe.exists(debug=debug):
            result = (
                felt_python.upload_geodataframe(self.map_id, df, target, api_token=self.token)
                if 'geodataframe' in str(type(df)).lower()
                else felt_python.upload_dataframe(self.map_id, df, target, api_token=self.token)
            )
            layer_id = result.get('layer_id')
            layer_group_id = result.get('layer_group_id')
            if layer_id and layer_group_id:
                pipe.update_parameters({
                    'felt': {
                        'layer_id': layer_id,
                        'layer_group_id': layer_group_id,
                    },
                })
                return True, f"Created layer '{layer_id}' ({rowcount} rows)."

        file_ext = 'gpkg' if 'geodataframe' in str(type(df)).lower() else 'csv'
        with tempfile.TemporaryDirectory() as tempdir:
            file_name = os.path.join(tempdir, f"{target}.{file_ext}")

            if file_ext == 'gpkg':
                df.to_file(file_name, driver='GPKG')
            else:
                df.to_csv(file_name)

            result = felt_python.refresh_file_layer(
                self.map_id,
                layer_id,
                file_name,
                api_token=self.token,
            )

        return True, f"Successfully replaced layer '{layer_id}' ({rowcount} rows)."


    def clear_pipe(
        self,
        pipe: mrsm.Pipe,
        begin: datetime | int | None = None,
        end: datetime | int | None = None,
        params: dict[str, Any] | None = None,
        debug: bool = False,
    ) -> mrsm.SuccessTuple:
        """
        Delete rows within `begin`, `end`, and `params`.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe whose rows to clear.

        begin: datetime | int | None, default None
            If provided, remove rows >= `begin`.

        end: datetime | int | None, default None
            If provided, remove rows < `end`.

        params: dict[str, Any] | None, default None
            If provided, only remove rows which match the `params` filter.

        Returns
        -------
        A `SuccessTuple` indicating success.
        """
        ### TODO Write a query to remove rows which match `begin`, `end`, and `params`.
        return True, "Success"

    def get_pipe_data(
        self,
        pipe: mrsm.Pipe,
        select_columns: list[str] | None = None,
        omit_columns: list[str] | None = None,
        begin: datetime | int | None = None,
        end: datetime | int | None = None,
        params: dict[str, Any] | None = None,
        debug: bool = False,
        **kwargs: Any
    ) -> Union['pd.DataFrame', None]:
        """
        Query a pipe's target table and return the DataFrame.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe with the target table from which to read.

        select_columns: list[str] | None, default None
            If provided, only select these given columns.
            Otherwise select all available columns (i.e. `SELECT *`).

        omit_columns: list[str] | None, default None
            If provided, remove these columns from the selection.

        begin: datetime | int | None, default None
            The earliest `datetime` value to search from (inclusive).

        end: datetime | int | None, default None
            The lastest `datetime` value to search from (exclusive).

        params: dict[str | str] | None, default None
            Additional filters to apply to the query.

        Returns
        -------
        The target table's data as a DataFrame.
        """
        from meerschaum.utils.dataframe import query_df
        gpd = mrsm.attempt_import('geopandas')
        felt_python = mrsm.attempt_import('felt_python', venv='felt')

        layer_id = self.get_pipe_layer_id(pipe, debug=debug)
        dt_col = pipe.columns.get("datetime", None)
        if layer_id is None:
            return None

        with tempfile.TemporaryDirectory() as tempdir:
            file_name = os.path.join(tempdir, f'{layer_id}.gpkg')
            felt_python.download_layer(
                self.map_id,
                layer_id,
                file_name,
                self.token,
            )
            gdf = gpd.read_file(file_name, layer='parsed')

        cols_to_del = [col for col in gdf.columns if col.startswith('felt:')]
        for col in cols_to_del:
            del gdf[col]

        return query_df(
            gdf,
            begin=begin,
            end=end,
            params=params,
            datetime_column=dt_col,
            select_columns=select_columns,
            omit_columns=omit_columns,
            inplace=True,
            debug=debug,
        )

    def get_sync_time(
        self,
        pipe: mrsm.Pipe,
        params: dict[str, Any] | None = None,
        newest: bool = True,
        debug: bool = False,
        **kwargs: Any
    ) -> datetime | int | None:
        """
        Return the most recent value for the `datetime` axis.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe whose collection contains documents.

        params: dict[str, Any] | None, default None
            Filter certain parameters when determining the sync time.

        newest: bool, default True
            If `True`, return the maximum value for the column.

        Returns
        -------
        The largest `datetime` or `int` value of the `datetime` axis. 
        """
        ### TODO write a query to get the largest value for `dt_col`.
        ### If `newest` is `False`, return the smallest value.
        ### Apply the `params` filter in case of multiplexing.
        return None

    def get_pipe_columns_types(
        self,
        pipe: mrsm.Pipe,
        debug: bool = False,
        **kwargs: Any
    ) -> dict[str, str]:
        """
        Return the data types for the columns in the target table for data type enforcement.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe whose target table contains columns and data types.

        Returns
        -------
        A dictionary mapping columns to data types.
        """
        columns_types = {}

        ### Return a dictionary mapping the columns
        ### to their Pandas dtypes, e.g.:
        ### `{'foo': 'int64'`}`
        ### or to SQL-style dtypes, e.g.:
        ### `{'bar': 'INT'}`
        return columns_types

    def get_pipe_rowcount(
        self,
        pipe: mrsm.Pipe,
        begin: datetime | int | None = None,
        end: datetime | int | None = None,
        params: dict[str, Any] | None = None,
        remote: bool = False,
        debug: bool = False,
        **kwargs: Any
    ) -> int:
        """
        Return the rowcount for the pipe's table.

        Parameters
        ----------
        pipe: mrsm.Pipe
            The pipe whose table should be counted.

        begin: datetime | int | None, default None
            If provided, only count rows >= `begin`.

        end: datetime | int | None, default None
            If provided, only count rows < `end`.

        params: dict[str, Any] | None
            If provided, only count rows othat match the `params` filter.

        remote: bool, default False
            If `True`, return the rowcount for the pipe's fetch definition.
            In this case, `self` refers to `Pipe.connector`, not `Pipe.instance_connector`.

        Returns
        -------
        The rowcount for this pipe's table according the given parameters.
        """
        ### TODO write a query to count how many rows exist in `table_name` according to the filters.
        table_name = pipe.target
        count = 0
        return count
