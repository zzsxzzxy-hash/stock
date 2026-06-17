#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自有策略计算入库 — 全部从数据库读取，不调任何外部接口。
每日收盘后运行，结果直接写库，页面只做 SELECT。
"""
import logging
import datetime

import pandas as pd

import instock.lib.database as mdb
import instock.core.tablestructure as tbs
import instock.lib.trade_time as trd

log = logging.getLogger(__name__)


def get_prev_trade_date(date_str: str) -> str | None:
    """从 cn_stock_hist_data 取该股之前最近一个有数据的交易日"""
    r = mdb.executeSqlFetch(
        "SELECT DISTINCT date FROM `cn_stock_hist_data` WHERE date < %s ORDER BY date DESC LIMIT 1",
        (date_str,)
    )
    if r and r[0][0]:
        d = r[0][0]
        return d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
    return None


def prepare_volume_surge(date: datetime.date):
    """
    爆量股票策略（直接从 cn_stock_hist_data 数据库计算）
    条件：
      1. 今日成交量 >= 昨日成交量 × 1.9
      2. 今日换手率 >= 5%
      3. 今日上涨（quote_change > 0 且 close > open）
      4. 排除 ST（name 含 ST）
    """
    table_name = 'cn_stock_strategy_volume_surge'
    date_str   = date.strftime('%Y-%m-%d')

    prev_date_str = get_prev_trade_date(date_str)
    if not prev_date_str:
        log.warning(f"[volume_surge] {date_str} 找不到前一交易日，跳过")
        return

    log.info(f"[volume_surge] 计算 {date_str}，前一交易日={prev_date_str}")

    # 一次 SQL 完成所有筛选：今日 JOIN 昨日，直接过滤条件
    sql = """
        SELECT
            t.date,
            t.code,
            t.close          AS signal_close,
            t.volume         AS signal_vol,
            y.volume         AS prev_vol,
            ROUND(t.volume / y.volume, 4)  AS vol_ratio,
            t.turnover,
            t.quote_change   AS p_change
        FROM `cn_stock_hist_data` t
        JOIN `cn_stock_hist_data` y
            ON y.code = t.code AND y.date = %s
        WHERE t.date = %s
          AND t.volume >= y.volume * 1.9
          AND t.turnover >= 5.0
          AND t.quote_change > 0
          AND t.close > t.open
          AND y.volume > 0
        ORDER BY vol_ratio DESC
    """
    rows = mdb.executeSqlFetch(sql, (prev_date_str, date_str))
    if rows is None:
        rows = []

    log.info(f"[volume_surge] {date_str} 初筛 {len(rows)} 只（含ST）")

    # 排除 ST（join stock_basic 或直接过滤 name）
    # 从 cn_stock_spot 取 name
    codes = tuple(r[1] for r in rows) if rows else ()
    name_map = {}
    if codes:
        placeholders = ','.join(['%s'] * len(codes))
        name_rows = mdb.executeSqlFetch(
            f"SELECT code, name FROM `cn_stock_spot` WHERE date = %s AND code IN ({placeholders})",
            (date_str, *codes)
        )
        if name_rows:
            name_map = {r[0]: r[1] for r in name_rows}

    # 组装结果，过滤 ST
    result = []
    for r in rows:
        code = r[1]
        name = name_map.get(code, '')
        if 'ST' in str(name).upper():
            continue
        result.append({
            'date':         date_str,
            'code':         code,
            'name':         name,
            'signal_close': float(r[2] or 0),
            'signal_vol':   float(r[3] or 0),
            'prev_vol':     float(r[4] or 0),
            'vol_ratio':    float(r[5] or 0),
            'turnover':     float(r[6] or 0),
            'p_change':     float(r[7] or 0),
        })

    log.info(f"[volume_surge] {date_str} 排除ST后 {len(result)} 只")

    # 建表（首次）
    cols_def  = tbs.TABLE_CN_STOCK_CUSTOM_STRATEGIES[0]['columns']
    cols_type = tbs.get_field_types(cols_def)
    if not mdb.checkTableIsExist(table_name):
        empty = pd.DataFrame(columns=list(cols_def.keys()))
        mdb.insert_db_from_df(empty, table_name, cols_type, False, "`date`,`code`")
        cols_type = None

    # 删旧数据
    mdb.executeSql(f"DELETE FROM `{table_name}` WHERE `date` = '{date_str}'")

    if not result:
        log.info(f"[volume_surge] {date_str} 无满足条件股票")
        return

    df = pd.DataFrame(result)
    mdb.insert_db_from_df(df, table_name, None, False, "`date`,`code`")
    log.info(f"[volume_surge] {date_str} 写入 {len(result)} 条")


def prepare_custom(date: datetime.date):
    """入口：运行全部自有策略"""
    prepare_volume_surge(date)
    # 后续新增策略在此追加调用


def main():
    date = trd.get_trade_date()
    prepare_custom(date)


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        datefmt='%H:%M:%S', force=True)
    if len(sys.argv) > 1:
        d = datetime.datetime.strptime(sys.argv[1], '%Y-%m-%d').date()
    else:
        d = trd.get_trade_date()
    prepare_custom(d)
