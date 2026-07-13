#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收盘后同步每日行情表。

同步目标：
  - cn_stock_hist_data：日K/换手等策略基础数据
  - cn_stock_spot：页面与策略使用的当日行情快照
"""
import datetime
import logging
import time

import numpy as np
import pandas as pd

import instock.core.tablestructure as tbs
import instock.lib.database as mdb
from instock.core.crawling.stock_hist_tushare import fetcher

log = logging.getLogger(__name__)


def count_rows(table_name: str, date_str: str) -> int:
    """返回指定日期的数据行数。"""
    return mdb.executeSqlCount(
        f'SELECT COUNT(*) FROM "{table_name}" WHERE "date" = %s',
        (date_str,)
    )


def latest_date(table_name: str) -> str:
    rows = mdb.executeSqlFetch(f'SELECT MAX("date") FROM "{table_name}"')
    return str(rows[0][0]) if rows and rows[0][0] else ''


def sync_hist_data(start_date: str, end_date: str):
    """同步 cn_stock_hist_data。"""
    from instock.job.batch_fetch_hist import run as hist_run
    hist_run(start_date, end_date)


def sync_stock_spot(start_date: str, end_date: str) -> bool:
    """
    用 Tushare daily + daily_basic + stock_basic 按日重建 cn_stock_spot。
    这比实时行情源更适合补历史交易日。
    """
    if not fetcher.is_available():
        raise RuntimeError('Tushare 不可用，请检查 token 配置')

    stock_basic = fetcher.pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,symbol,name,industry,list_date,market'
    )
    if stock_basic is None or stock_basic.empty:
        raise RuntimeError('stock_basic 返回空')

    trade_days = _get_trade_days(start_date, end_date)
    if not trade_days:
        log.warning("cn_stock_spot: %s ~ %s 无交易日，跳过", start_date, end_date)
        return True

    success_days = 0
    for trade_date in trade_days:
        cnt = _sync_stock_spot_one_day(trade_date, stock_basic)
        log.info("cn_stock_spot %s 写入 %s 条", trade_date, cnt)
        if cnt > 0:
            success_days += 1
        time.sleep(0.5)

    return success_days == len(trade_days)


def sync_daily_tables(start_date: str, end_date: str):
    """同步每日核心行情表，先日K后现货。"""
    log.info("开始同步每日行情表: %s ~ %s", start_date, end_date)
    sync_hist_data(start_date, end_date)
    sync_stock_spot(start_date, end_date)
    log.info("每日行情表同步完成: %s ~ %s", start_date, end_date)


def ensure_daily_tables(date_str: str, min_rows: int = 100) -> dict:
    """
    确保指定交易日两个每日表都有数据。
    返回同步前后的计数，方便日志/页面展示。
    """
    before = {
        'hist': count_rows('cn_stock_hist_data', date_str),
        'spot': count_rows('cn_stock_spot', date_str),
    }

    if before['hist'] < min_rows:
        sync_hist_data(date_str, date_str)
    if before['spot'] < min_rows:
        sync_stock_spot(date_str, date_str)

    after = {
        'hist': count_rows('cn_stock_hist_data', date_str),
        'spot': count_rows('cn_stock_spot', date_str),
    }
    return {'date': date_str, 'before': before, 'after': after}


def _get_trade_days(start_date: str, end_date: str) -> list[datetime.date]:
    sd = start_date.replace('-', '')
    ed = end_date.replace('-', '')
    df = fetcher.pro.trade_cal(exchange='SSE', start_date=sd, end_date=ed, is_open='1')
    if df is None or df.empty:
        return []
    return sorted([
        datetime.datetime.strptime(str(d), '%Y%m%d').date()
        for d in df['cal_date'].tolist()
    ])


def _sync_stock_spot_one_day(trade_date: datetime.date, stock_basic: pd.DataFrame) -> int:
    date_str = trade_date.strftime('%Y%m%d')

    daily = fetcher.pro.daily(trade_date=date_str)
    if daily is None or daily.empty:
        log.warning("cn_stock_spot %s daily 返回空，跳过", trade_date)
        return 0

    try:
        daily_basic = fetcher.pro.daily_basic(trade_date=date_str)
        daily_basic = daily_basic.drop(columns=['close'], errors='ignore')
    except Exception as e:
        log.warning("cn_stock_spot %s daily_basic 失败: %s", trade_date, e)
        daily_basic = pd.DataFrame()

    result = pd.merge(stock_basic, daily, on='ts_code', how='inner')
    if not daily_basic.empty:
        result = pd.merge(result, daily_basic, on=['ts_code', 'trade_date'], how='left')
    result = result.reset_index(drop=True)

    def _col(name, default=np.nan):
        if name in result.columns:
            return result[name]
        return pd.Series([default] * len(result), index=result.index)

    out = pd.DataFrame(index=result.index)
    out['date']               = trade_date.strftime('%Y-%m-%d')
    out['code']               = result['symbol']
    out['name']               = result['name']
    out['new_price']          = _col('close')
    out['change_rate']        = _col('pct_chg')
    out['ups_downs']          = _col('change')
    out['volume']             = _col('vol')
    out['deal_amount']        = _col('amount')
    out['amplitude']          = ((_col('high') - _col('low')) / _col('pre_close') * 100).round(2)
    out['turnoverrate']       = _col('turnover_rate')
    out['volume_ratio']       = _col('volume_ratio')
    out['open_price']         = _col('open')
    out['high_price']         = _col('high')
    out['low_price']          = _col('low')
    out['pre_close_price']    = _col('pre_close')
    out['speed_increase']     = np.nan
    out['speed_increase_5']   = np.nan
    out['speed_increase_60']  = np.nan
    out['speed_increase_all'] = np.nan
    out['dtsyl']              = _col('pe')
    out['pe9']                = _col('pe')
    out['pe']                 = _col('pe_ttm')
    out['pbnewmrq']           = _col('pb')
    out['basic_eps']          = np.nan
    out['bvps']               = np.nan
    out['per_capital_reserve'] = np.nan
    out['per_unassign_profit'] = np.nan
    out['roe_weight']          = np.nan
    out['sale_gpr']            = np.nan
    out['debt_asset_ratio']    = np.nan
    out['total_operate_income'] = np.nan
    out['toi_yoy_ratio']        = np.nan
    out['parent_netprofit']     = np.nan
    out['netprofit_yoy_ratio']  = np.nan
    out['report_date']          = np.nan
    out['total_shares']         = _col('total_share')
    out['free_shares']          = _col('free_share')
    out['total_market_cap']     = _col('total_mv')
    out['free_cap']             = _col('circ_mv')
    out['industry']             = result['industry']
    out['listing_date']         = result['list_date']

    out = out[out['new_price'].notna() & (out['new_price'] > 0)].copy()
    if out.empty:
        return 0

    table_name = tbs.TABLE_CN_STOCK_SPOT['name']
    if mdb.checkTableIsExist(table_name):
        mdb.executeSql('DELETE FROM "cn_stock_spot" WHERE "date" = %s', (trade_date.strftime('%Y-%m-%d'),))
        cols_type = None
    else:
        cols_type = tbs.get_field_types(tbs.TABLE_CN_STOCK_SPOT['columns'])

    mdb.insert_db_from_df(out, table_name, cols_type, False, '"date","code"')
    return len(out)
