#!/usr/local/bin/python
# -*- coding: utf-8 -*-
"""
torndb - PostgreSQL version
A lightweight wrapper around psycopg2, API-compatible with the original
pymysql-based torndb used by this project.
"""

from __future__ import absolute_import, division, with_statement
import itertools
import logging
import time
import psycopg2
import psycopg2.extras
import psycopg2.extensions

__author__ = 'myh '
__date__ = '2025/12/31 '

version = "0.4"
version_info = (0, 4, 0, 0)

# Alias common psycopg2 exceptions so existing code that catches these works
IntegrityError  = psycopg2.IntegrityError
OperationalError = psycopg2.OperationalError


class Connection(object):
    """
    A lightweight wrapper around psycopg2, API-compatible with the original
    pymysql torndb.Connection.

    Key differences from MySQL version:
    - %s placeholders work the same way (psycopg2 also uses %s)
    - Backtick identifiers have been replaced with double-quotes in SQL strings
    - Returned rows are Row objects (dict subclass), same as before
    """

    def __init__(self, host, database, user=None, password=None,
                 max_idle_time=7 * 3600, connect_timeout=10,
                 time_zone="+0:00", charset="utf8", sql_mode="TRADITIONAL"):
        self.host = host
        self.database = database
        self.max_idle_time = float(max_idle_time)

        pair = host.split(":")
        pg_host = pair[0]
        pg_port = int(pair[1]) if len(pair) == 2 else 5432

        self._db_args = dict(
            host=pg_host,
            port=pg_port,
            dbname=database,
            connect_timeout=connect_timeout,
        )
        if user is not None:
            self._db_args["user"] = user
        if password is not None:
            self._db_args["password"] = password

        self._db = None
        self._last_use_time = time.time()
        try:
            self.reconnect()
        except Exception:
            logging.error(f"Cannot connect to PostgreSQL on {self.host}", exc_info=True)

    def __del__(self):
        self.close()

    def close(self):
        if getattr(self, "_db", None) is not None:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

    def reconnect(self):
        self.close()
        self._db = psycopg2.connect(**self._db_args)
        # autocommit so each statement commits immediately (same behaviour as MySQL version)
        self._db.autocommit = True

    def iter(self, query, *parameters, **kwparameters):
        """Returns an iterator for the given query and parameters."""
        self._ensure_connected()
        cursor = self._db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            self._execute(cursor, query, parameters, kwparameters)
            column_names = [d[0] for d in cursor.description]
            for row in cursor:
                yield Row(zip(column_names, row))
        finally:
            cursor.close()

    def query(self, query, *parameters, **kwparameters):
        """Returns a row list for the given query and parameters."""
        cursor = self._cursor()
        try:
            self._execute(cursor, query, parameters, kwparameters)
            if cursor.description is None:
                return []
            column_names = [d[0] for d in cursor.description]
            return [Row(itertools.zip_longest(column_names, row)) for row in cursor]
        finally:
            cursor.close()

    def get(self, query, *parameters, **kwparameters):
        """Returns the singular row returned by the given query, or None."""
        rows = self.query(query, *parameters, **kwparameters)
        if not rows:
            return None
        elif len(rows) > 1:
            raise Exception("Multiple rows returned for Database.get() query")
        return rows[0]

    def execute(self, query, *parameters, **kwparameters):
        """Executes the given query, returning the lastrowid from the query."""
        return self.execute_lastrowid(query, *parameters, **kwparameters)

    def execute_lastrowid(self, query, *parameters, **kwparameters):
        """Executes the given query, returning the lastrowid (or None)."""
        cursor = self._cursor()
        try:
            self._execute(cursor, query, parameters, kwparameters)
            # Try to get lastrowid via RETURNING or cursor.lastrowid not available in psycopg2
            # Return rowcount as a reasonable fallback
            return cursor.rowcount
        finally:
            cursor.close()

    def execute_rowcount(self, query, *parameters, **kwparameters):
        """Executes the given query, returning the rowcount from the query."""
        cursor = self._cursor()
        try:
            self._execute(cursor, query, parameters, kwparameters)
            return cursor.rowcount
        finally:
            cursor.close()

    def executemany(self, query, parameters):
        return self.executemany_lastrowid(query, parameters)

    def executemany_lastrowid(self, query, parameters):
        cursor = self._cursor()
        try:
            cursor.executemany(query, parameters)
            return cursor.rowcount
        finally:
            cursor.close()

    def executemany_rowcount(self, query, parameters):
        cursor = self._cursor()
        try:
            cursor.executemany(query, parameters)
            return cursor.rowcount
        finally:
            cursor.close()

    update    = execute_rowcount
    updatemany = executemany_rowcount
    insert    = execute_lastrowid
    insertmany = executemany_lastrowid

    def _ensure_connected(self):
        if self._db is None or (time.time() - self._last_use_time > self.max_idle_time):
            self.reconnect()
        # Also check if connection is still alive (closed=0 means open)
        elif self._db.closed != 0:
            self.reconnect()
        self._last_use_time = time.time()

    def _cursor(self):
        self._ensure_connected()
        return self._db.cursor()

    def _execute(self, cursor, query, parameters, kwparameters):
        try:
            return cursor.execute(query, kwparameters or parameters or None)
        except OperationalError:
            logging.error(f"Error connecting to PostgreSQL on {self.host}")
            self.close()
            raise


class Row(dict):
    """A dict that allows for object-like property access syntax."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)
