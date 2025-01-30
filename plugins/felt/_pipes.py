#! /usr/bin/env python3
# vim:fenc=utf-8

"""
Implement the pipes methods for the instance connector interface.
https://meerschaum.io/reference/connectors/instance-connectors/
"""

from typing import Any, Union
from datetime import datetime, timedelta, timezone
import meerschaum as mrsm
from meerschaum.utils.warnings import warn


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
    Return the `_id` for the pipe if it exists.

    Parameters
    ----------
    pipe: mrsm.Pipe
        The pipe whose `_id` to fetch.

    Returns
    -------
    The `_id` for the pipe's document or `None`.
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
    warn("TODO")
    return False


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
    return False, "Not implemented"


def sync_pipe(
    self,
    pipe: mrsm.Pipe,
    df: 'pd.DataFrame' = None,
    debug: bool = False,
    **kwargs: Any
) -> mrsm.SuccessTuple:
    """
    Upsert new documents into the pipe's collection.

    Parameters
    ----------
    pipe: mrsm.Pipe
        The pipe whose collection should receive the new documents.

    df: Union['pd.DataFrame', Iterator['pd.DataFrame']], default None
        The data to be synced.

    Returns
    -------
    A `SuccessTuple` indicating success.
    """
    return False, "Not implemented"


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
    return False, "Not implemented"


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
    return None


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
    warn("TODO")
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
    warn("TODO")
    return {}
