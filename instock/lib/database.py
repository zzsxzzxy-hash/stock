#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库工具模块（PostgreSQL 版本）
已从 MySQL/pymysql 迁移到 PostgreSQL/psycopg2
"""

import logging
import os
import psycopg2
import psycopg2.extras
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.types import NVARCHAR

__author__ = 'myh '
__date__ = '2025/12/31 '

db_host     = "localhost"
db_user     = os.environ.get('db_user',     'x6-mac')
db_password = os.environ.get('db_password', '')
db_database = "instockdb"
db_port     = 5432          # PostgreSQL 默认端口

# 支持 Docker 环境变量覆盖（db_user / db_password 已在上方读取）
for _var, _attr in [('db_host', 'db_host'), ('db_database', 'db_database'), ('db_port', 'db_port')]:
    _val = os.environ.get(_var)
    if _val is not None:
        if _var == 'db_port':
            db_port = int(_val)
        else:
            locals()[_attr] = _val

# ── 连接字符串（供 SQLAlchemy 使用）────────────────────────
MYSQL_CONN_URL = "postgresql+psycopg2://%s:%s@%s:%s/%s" % (
    db_user, db_password, db_host, db_port, db_database)

# 别名，部分代码仍用旧名
PG_CONN_URL = MYSQL_CONN_URL

logging.info(f"数据库链接信息：{MYSQL_CONN_URL}")

# torndb 连接参数（host:port 格式，torndb.py 会解析）
MYSQL_CONN_TORNDB = {
    'host':         f'{db_host}:{db_port}',
    'user':         db_user,
    'password':     db_password,
    'database':     db_database,
    'max_idle_time': 3600,
    'connect_timeout': 1000,
}

# psycopg2 原生连接参数
_PG_CONN_ARGS = dict(
    host=db_host, port=db_port,
    user=db_user, password=db_password,
    dbname=db_database,
)


# ── SQLAlchemy engine ─────────────────────────────────────
def engine():
    return create_engine(MYSQL_CONN_URL)


def engine_to_db(to_db):
    url = "postgresql+psycopg2://%s:%s@%s:%s/%s" % (
        db_user, db_password, db_host, db_port, to_db)
    return create_engine(url)


# ── 原生 psycopg2 连接 ────────────────────────────────────
def get_connection():
    try:
        conn = psycopg2.connect(**_PG_CONN_ARGS)
        conn.autocommit = True
        return conn
    except Exception as e:
        logging.error(f"database.get_connection处理异常：{e}")
    return None


# ── 主键 / 索引辅助 ──────────────────────────────────────
def _pg_primary_keys(pk_str: str) -> str:
    """
    把带反引号的主键字符串转为 PostgreSQL 双引号格式。
    例："`date`,`code`"  →  '"date","code"'
    """
    return pk_str.replace('`', '"')


def _pg_index_col(col_str: str) -> str:
    """把带反引号的索引列字符串转为双引号格式。"""
    return col_str.replace('`', '"')


# ── DataFrame → 数据库 ────────────────────────────────────
def insert_db_from_df(data, table_name, cols_type, write_index, primary_keys, indexs=None):
    insert_other_db_from_df(None, data, table_name, cols_type, write_index, primary_keys, indexs)


def insert_other_db_from_df(to_db, data, table_name, cols_type, write_index, primary_keys, indexs=None):
    if to_db is None:
        _engine = engine()
    else:
        _engine = engine_to_db(to_db)

    ipt = inspect(_engine)
    col_name_list = data.columns.tolist()
    if write_index:
        col_name_list.insert(0, data.index.name)

    try:
        if cols_type is None:
            data.to_sql(name=table_name, con=_engine, if_exists='append', index=write_index)
        elif not cols_type:
            data.to_sql(name=table_name, con=_engine, if_exists='append',
                        dtype={c: NVARCHAR(255) for c in col_name_list}, index=write_index)
        else:
            data.to_sql(name=table_name, con=_engine, if_exists='append',
                        dtype=cols_type, index=write_index)
    except Exception as e:
        logging.error(f"database.insert_other_db_from_df处理异常：{table_name}表 {e}")

    # 如果还没有主键，则添加
    try:
        pk_cols = ipt.get_pk_constraint(table_name).get('constrained_columns', [])
    except Exception:
        pk_cols = []

    if not pk_cols and primary_keys:
        try:
            pg_pk = _pg_primary_keys(primary_keys)
            with get_connection() as conn:
                with conn.cursor() as db:
                    db.execute(f'ALTER TABLE "{table_name}" ADD PRIMARY KEY ({pg_pk});')
                    if indexs is not None:
                        for k in indexs:
                            pg_idx_col = _pg_index_col(indexs[k])
                            db.execute(
                                f'CREATE INDEX IF NOT EXISTS "IN{k}" ON "{table_name}" ({pg_idx_col});'
                            )
        except Exception as e:
            logging.error(f"database.insert_other_db_from_df ADD PRIMARY KEY异常：{table_name} {e}")


# ── 行级更新 ─────────────────────────────────────────────
def update_db_from_df(data, table_name, where):
    data = data.where(data.notnull(), None)
    update_string = f'UPDATE "{table_name}" SET '
    where_string  = ' WHERE '
    cols = tuple(data.columns)
    with get_connection() as conn:
        with conn.cursor() as db:
            try:
                for row in data.values:
                    sql       = update_string
                    sql_where = where_string
                    params = []
                    for index, col in enumerate(cols):
                        val = row[index]
                        if col in where:
                            sep = '' if len(sql_where) == len(where_string) else ' AND '
                            sql_where += f'{sep}"{col}" = %s '
                            params.append(val)
                        else:
                            if val is None or (val != val):   # None 或 NaN
                                sql += f'"{col}" = NULL, '
                            else:
                                sql += f'"{col}" = %s, '
                                params.append(val)
                    full_sql = f'{sql[:-2]}{sql_where}'
                    db.execute(full_sql, params)
            except Exception as e:
                logging.error(f"database.update_db_from_df处理异常：{e}")


# ── 表是否存在 ───────────────────────────────────────────
def checkTableIsExist(tableName):
    with get_connection() as conn:
        with conn.cursor() as db:
            db.execute("""
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema IN ('public', 'instockdb')
                  AND table_name   = %s
            """, (tableName,))
            row = db.fetchone()
            return row is not None and row[0] == 1


# ── 通用 SQL 执行 ────────────────────────────────────────
def executeSql(sql, params=()):
    with get_connection() as conn:
        with conn.cursor() as db:
            try:
                db.execute(sql, params or None)
            except Exception as e:
                logging.error(f"database.executeSql处理异常：{sql} {e}")


def executeSqlFetch(sql, params=()):
    with get_connection() as conn:
        with conn.cursor() as db:
            try:
                db.execute(sql, params or None)
                return db.fetchall()
            except Exception as e:
                logging.error(f"database.executeSqlFetch处理异常：{sql} {e}")
    return None


def executeSqlCount(sql, params=()):
    with get_connection() as conn:
        with conn.cursor() as db:
            try:
                db.execute(sql, params or None)
                result = db.fetchall()
                if result and len(result) == 1:
                    return int(result[0][0])
                return 0
            except Exception as e:
                logging.error(f"database.executeSqlCount处理异常：{e}")
    return 0
