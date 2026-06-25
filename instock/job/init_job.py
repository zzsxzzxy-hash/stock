#!/usr/local/bin/python3
# -*- coding: utf-8 -*-
"""
初始化数据库（PostgreSQL 版本）
"""

import logging
import psycopg2
import os.path
import sys

cpath_current = os.path.dirname(os.path.dirname(__file__))
cpath = os.path.abspath(os.path.join(cpath_current, os.pardir))
sys.path.append(cpath)
import instock.lib.database as mdb

__author__ = 'myh '
__date__ = '2025/12/31 '


def create_new_database():
    """连接 postgres 系统库，创建 instockdb 数据库"""
    try:
        conn = psycopg2.connect(
            host=mdb.db_host, port=mdb.db_port,
            user=mdb.db_user, password=mdb.db_password,
            dbname='postgres'
        )
        conn.autocommit = True
        with conn.cursor() as db:
            db.execute(f"SELECT 1 FROM pg_database WHERE datname='{mdb.db_database}'")
            if not db.fetchone():
                db.execute(f"CREATE DATABASE {mdb.db_database} ENCODING 'UTF8'")
                logging.info(f"数据库 {mdb.db_database} 创建成功")
        conn.close()
        create_new_base_table()
    except Exception as e:
        logging.error(f"init_job.create_new_database处理异常：{e}")


def create_new_base_table():
    """创建 cn_stock_attention 基础表"""
    with mdb.get_connection() as conn:
        with conn.cursor() as db:
            create_table_sql = """
                CREATE TABLE IF NOT EXISTS cn_stock_attention (
                    datetime TIMESTAMP NULL DEFAULT NULL,
                    code     VARCHAR(6) NOT NULL,
                    PRIMARY KEY (code)
                );
                CREATE INDEX IF NOT EXISTS inix_datetime ON cn_stock_attention (datetime);
            """
            db.execute(create_table_sql)


def check_database():
    with mdb.get_connection() as conn:
        with conn.cursor() as db:
            db.execute("SELECT 1")


def main():
    try:
        check_database()
    except Exception as e:
        logging.error("执行信息：数据库不存在，将创建。")
        create_new_database()


if __name__ == '__main__':
    main()
