#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量拉取全市场历史日K数据入库 cn_stock_hist_data
策略：用 Tushare daily + daily_basic 按天拉全市场，一天一次接口调用，速度快。
用法：
  python3 instock/job/batch_fetch_hist.py --start 2026-06-13 --end 2026-06-16
"""
import sys
import os
import datetime
import logging
import argparse
import time

project_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, project_path)

import pandas as pd
import instock.lib.database as mdb
from instock.core.crawling.tushare_data import tushare_data as tsd

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    force=True,
)
log = logging.getLogger(__name__)

TABLE = 'cn_stock_hist_data'


def _get_trade_days(start_date: str, end_date: str) -> list:
    """从 Tushare 获取区间交易日列表"""
    sd = start_date.replace('-', '')
    ed = end_date.replace('-', '')
    df = tsd.pro.trade_cal(exchange='SSE', start_date=sd, end_date=ed, is_open='1')
    if df is None or df.empty:
        return []
    return sorted([
        datetime.datetime.strptime(str(d), '%Y%m%d').date()
        for d in df['cal_date'].tolist()
    ])


def _sync_one_day(trade_date: datetime.date) -> int:
    """
    拉取指定交易日全市场数据，写入 cn_stock_hist_data。
    返回写入行数。
    """
    date_str  = trade_date.strftime('%Y%m%d')
    date_disp = trade_date.strftime('%Y-%m-%d')

    # ── 拉取 daily（价格、量）
    daily = tsd.pro.daily(trade_date=date_str)
    if daily is None or daily.empty:
        log.warning(f"  {date_disp}: daily 返回空，跳过")
        return 0

    # ── 拉取 daily_basic（换手率、量比等）
    try:
        basic = tsd.pro.daily_basic(trade_date=date_str,
                                    fields='ts_code,trade_date,turnover_rate,volume_ratio')
    except Exception as e:
        log.warning(f"  {date_disp}: daily_basic 失败({e})，换手率置0")
        basic = pd.DataFrame()

    # ── 合并
    merged = pd.merge(daily, basic, on=['ts_code', 'trade_date'], how='left') \
        if (basic is not None and not basic.empty) else daily.copy()

    def _col(name, default=0.0):
        return merged[name].fillna(default) if name in merged.columns \
            else pd.Series([default] * len(merged), index=merged.index)

    # ── 提取6位code
    merged['code'] = merged['ts_code'].str.split('.').str[0]

    # ── 计算幅度
    pre_close = _col('pre_close', 0.0)
    high      = _col('high',      0.0)
    low       = _col('low',       0.0)
    amplitude = ((high - low) / pre_close.replace(0, float('nan')) * 100).round(2).fillna(0.0)

    out = pd.DataFrame({
        'date':         date_disp,
        'code':         merged['code'],
        'open':         _col('open'),
        'close':        _col('close'),
        'high':         high,
        'low':          low,
        'volume':       _col('vol'),
        'amount':       _col('amount'),
        'amplitude':    amplitude,
        'quote_change': _col('pct_chg'),
        'ups_downs':    _col('change'),
        'turnover':     _col('turnover_rate'),   # 换手率%
    })

    # 过滤无效行
    out = out[out['close'] > 0].copy()

    if out.empty:
        log.warning(f"  {date_disp}: 过滤后为空，跳过")
        return 0

    # ── REPLACE INTO 写库（覆盖旧数据，修复换手率等字段）
    cols  = ['date', 'code', 'open', 'close', 'high', 'low',
             'volume', 'amount', 'amplitude', 'quote_change', 'ups_downs', 'turnover']
    ph    = ', '.join(['%s'] * len(cols))
    cnames = ', '.join(f'`{c}`' for c in cols)
    sql   = f"REPLACE INTO `{TABLE}` ({cnames}) VALUES ({ph})"
    rows  = [tuple(row) for row in out[cols].itertuples(index=False, name=None)]

    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)

    return len(rows)


def run(start_date: str, end_date: str):
    log.info(f"开始同步 cn_stock_hist_data: {start_date} ~ {end_date}")

    trade_days = _get_trade_days(start_date, end_date)
    if not trade_days:
        log.warning("区间内无交易日，退出")
        return

    log.info(f"共 {len(trade_days)} 个交易日")

    for i, td in enumerate(trade_days):
        log.info(f"[{i+1}/{len(trade_days)}] 同步 {td} ...")
        cnt = _sync_one_day(td)
        log.info(f"  写入 {cnt} 条")
        time.sleep(0.3)   # 避免触发 Tushare 频率限制

    log.info("全部完成")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='批量拉取历史日K数据（Tushare版）')
    parser.add_argument('--start', required=True, help='开始日期 YYYY-MM-DD')
    parser.add_argument('--end',   required=True, help='结束日期 YYYY-MM-DD')
    args = parser.parse_args()
    run(args.start, args.end)
