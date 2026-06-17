#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量拉取历史涨停原因数据
用法: python3 instock/job/batch_fetch_limitup_reason.py --start 2026-01-01 --end 2026-06-13
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import argparse
import datetime
import logging
import time

import instock.lib.database as mdb
import instock.core.tablestructure as tbs
import instock.core.stockfetch as stf
from instock.core.crawling.tushare_data import tushare_data as tsd

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/batch_limitup_reason.log', mode='a', encoding='utf-8'),
    ],
    force=True,
)
log = logging.getLogger(__name__)

TABLE = tbs.TABLE_CN_STOCK_LIMITUP_REASON['name']


def get_trade_dates(start: str, end: str) -> list:
    """获取指定范围内的交易日（yyyy-mm-dd格式）"""
    cal = tsd.pro.trade_cal(
        exchange='SSE',
        start_date=start.replace('-', ''),
        end_date=end.replace('-', ''),
    )
    dates = cal[cal['is_open'] == 1]['cal_date'].tolist()
    dates.sort()
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]


def already_done(date_iso: str) -> bool:
    try:
        r = mdb.executeSqlFetch(
            f"SELECT COUNT(*) FROM `{TABLE}` WHERE `date`=%s", (date_iso,)
        )
        return bool(r and r[0][0] > 0)
    except Exception:
        return False


def save_day(data, date_iso: str) -> int:
    table_name = TABLE
    if mdb.checkTableIsExist(table_name):
        mdb.executeSql(f"DELETE FROM `{table_name}` WHERE `date`='{date_iso}'")
        cols_type = None
    else:
        cols_type = tbs.get_field_types(tbs.TABLE_CN_STOCK_LIMITUP_REASON['columns'])
    mdb.insert_db_from_df(data, table_name, cols_type, False, "`date`,`code`")
    return len(data)


def run(start: str, end: str):
    log.info(f"获取交易日列表 {start} ~ {end} ...")
    trade_dates = get_trade_dates(start, end)
    log.info(f"共 {len(trade_dates)} 个交易日")

    total = len(trade_dates)
    ok = skip = err = 0

    for i, date_iso in enumerate(trade_dates, 1):
        if already_done(date_iso):
            skip += 1
            log.info(f"[{i}/{total}] {date_iso} 已有数据，跳过")
            continue

        log.info(f"[{i}/{total}] 拉取 {date_iso} ...")
        try:
            data = stf.fetch_stock_limitup_reason(date_iso)
            if data is None or data.empty:
                log.info(f"[{i}/{total}] {date_iso} 无数据（非交易日或无涨停）")
                err += 1
                continue
            n = save_day(data, date_iso)
            ok += 1
            log.info(f"[{i}/{total}] {date_iso} 写入 {n} 条")
        except Exception as e:
            log.error(f"[{i}/{total}] {date_iso} 异常: {e}")
            err += 1

        time.sleep(0.3)  # 同花顺限速保护

    log.info(f"完成！ok={ok} skip={skip} err={err} / total={total}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2026-01-01')
    parser.add_argument('--end',   default=datetime.date.today().strftime('%Y-%m-%d'))
    args = parser.parse_args()
    run(args.start, args.end)
