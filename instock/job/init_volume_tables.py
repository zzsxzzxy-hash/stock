#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初始化量能监控所需的数据库表：
  - cn_stock_minute_bar   分钟K线（永久存储，用于回测）
  - cn_stock_sector_map   股票-板块映射（一股多板块）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import logging
import instock.lib.database as mdb

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)


def init_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS cn_stock_minute_bar (
        date        DATE           NOT NULL,
        time        VARCHAR(5)     NOT NULL,
        code        VARCHAR(6)     NOT NULL,
        open        NUMERIC(12,4)  DEFAULT NULL,
        close       NUMERIC(12,4)  DEFAULT NULL,
        high        NUMERIC(12,4)  DEFAULT NULL,
        low         NUMERIC(12,4)  DEFAULT NULL,
        volume      NUMERIC(18,2)  DEFAULT NULL,
        amount      NUMERIC(20,2)  DEFAULT NULL,
        pre_close   NUMERIC(12,4)  DEFAULT NULL,
        PRIMARY KEY (date, time, code)
    );
    CREATE INDEX IF NOT EXISTS idx_minute_bar_code_date
        ON cn_stock_minute_bar (code, date);
    CREATE INDEX IF NOT EXISTS idx_minute_bar_date_time
        ON cn_stock_minute_bar (date, time);

    CREATE TABLE IF NOT EXISTS cn_stock_sector_map (
        code        VARCHAR(6)     NOT NULL,
        sector      VARCHAR(50)    NOT NULL,
        created_at  TIMESTAMP      DEFAULT NOW(),
        PRIMARY KEY (code, sector)
    );
    CREATE INDEX IF NOT EXISTS idx_sector_map_sector
        ON cn_stock_sector_map (sector);

    CREATE TABLE IF NOT EXISTS cn_stock_trade_theme (
        code             VARCHAR(6)   PRIMARY KEY,
        name             VARCHAR(80),
        trade_theme  VARCHAR(80)  NOT NULL,
        confidence       NUMERIC(8,2) DEFAULT 0,
        source           VARCHAR(30)  DEFAULT 'algorithm',
        reason           TEXT,
        candidate_count  INTEGER      DEFAULT 0,
        updated_at       TIMESTAMP    DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_trade_theme_name
        ON cn_stock_trade_theme (trade_theme);
    """
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    log.info("表初始化完成：cn_stock_minute_bar, cn_stock_sector_map, cn_stock_trade_theme")


if __name__ == '__main__':
    init_tables()
