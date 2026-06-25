#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量拉取历史个股资金流向数据
用法: python3 -m instock.job.batch_fetch_fund_flow --start 2025-01-01 --end 2026-06-13
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import argparse
import datetime
import logging
import time
import pandas as pd

import instock.lib.database as mdb
from instock.core.crawling.tushare_data import tushare_data as tsd

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/batch_fund_flow.log', mode='a', encoding='utf-8'),
    ],
    force=True,
)
log = logging.getLogger(__name__)

TABLE = 'cn_stock_fund_flow'


def get_trade_dates(start: str, end: str) -> list[str]:
    """获取指定范围内的交易日（yyyymmdd格式）"""
    cal = tsd.pro.trade_cal(
        exchange='SSE',
        start_date=start.replace('-', ''),
        end_date=end.replace('-', ''),
    )
    dates = cal[cal['is_open'] == 1]['cal_date'].tolist()
    dates.sort()
    return dates


def already_done(date_iso: str) -> bool:
    """该交易日是否已有完整数据（>100条且name非空）"""
    r = mdb.executeSqlFetch(
        'SELECT COUNT(*) cnt FROM "cn_stock_fund_flow" WHERE date=%s AND name != \'\'  AND name IS NOT NULL',
        (date_iso,)
    )
    return bool(r and r[0][0] > 100)


def fetch_one_day(trade_date_8: str, stock_basic: pd.DataFrame) -> pd.DataFrame | None:
    """
    拉取单日资金流向，返回待入库 DataFrame。
    trade_date_8: yyyymmdd
    stock_basic: 股票基础信息（ts_code, name）
    """
    date_iso = f"{trade_date_8[:4]}-{trade_date_8[4:6]}-{trade_date_8[6:]}"

    for attempt in range(3):
        try:
            df = tsd.pro.moneyflow(trade_date=trade_date_8)
            if df is not None and not df.empty:
                break
        except Exception as e:
            if '频率' in str(e) or 'limit' in str(e).lower():
                wait = 20 * (attempt + 1)
                log.warning(f"  {date_iso} 限速，等待 {wait}s...")
                time.sleep(wait)
            else:
                log.error(f"  {date_iso} moneyflow异常: {e}")
                return None
        time.sleep(1)
    else:
        log.warning(f"  {date_iso} moneyflow无数据，跳过")
        return None

    # 合并股票名称
    df = df.merge(stock_basic[['ts_code', 'name']], on='ts_code', how='left')
    df['name'] = df['name'].fillna('')

    # 获取当日收盘价和涨跌幅
    try:
        daily = tsd.pro.daily(trade_date=trade_date_8, fields='ts_code,close,pct_chg')
        if daily is not None and not daily.empty:
            df = df.merge(daily[['ts_code', 'close', 'pct_chg']], on='ts_code', how='left')
        else:
            df['close'] = 0.0
            df['pct_chg'] = 0.0
    except Exception:
        df['close'] = 0.0
        df['pct_chg'] = 0.0

    df['close']   = pd.to_numeric(df.get('close',   0), errors='coerce').fillna(0)
    df['pct_chg'] = pd.to_numeric(df.get('pct_chg', 0), errors='coerce').fillna(0)

    def _amt(col):
        return pd.to_numeric(df[col], errors='coerce').fillna(0) if col in df.columns else pd.Series(0.0, index=df.index)

    # 计算各字段，对应 TABLE_CN_STOCK_FUND_FLOW 列定义
    # 今日列（无后缀）
    total_amt = (_amt('buy_elg_amount') + _amt('sell_elg_amount') +
                 _amt('buy_lg_amount')  + _amt('sell_lg_amount')  +
                 _amt('buy_md_amount')  + _amt('sell_md_amount')  +
                 _amt('buy_sm_amount')  + _amt('sell_sm_amount'))
    net_main  = (_amt('buy_elg_amount') - _amt('sell_elg_amount') +
                 _amt('buy_lg_amount')  - _amt('sell_lg_amount'))
    net_main_pct = (net_main / total_amt.replace(0, float('nan')) * 100).fillna(0).round(2)

    code = df['ts_code'].apply(lambda x: x.split('.')[0])

    result = pd.DataFrame({
        'date':                 date_iso,
        'code':                 code,
        'name':                 df['name'],
        'new_price':            df['close'],
        'change_rate':          df['pct_chg'],
        'fund_amount':          net_main,
        'fund_rate':            net_main_pct,
        'fund_amount_super':    _amt('buy_elg_amount') - _amt('sell_elg_amount'),
        'fund_rate_super':      0.0,
        'fund_amount_large':    _amt('buy_lg_amount')  - _amt('sell_lg_amount'),
        'fund_rate_large':      0.0,
        'fund_amount_medium':   _amt('buy_md_amount')  - _amt('sell_md_amount'),
        'fund_rate_medium':     0.0,
        'fund_amount_small':    _amt('buy_sm_amount')  - _amt('sell_sm_amount'),
        'fund_rate_small':      0.0,
        # 3日/5日/10日字段留0（历史数据无法计算多日累计，不影响当日展示）
        'change_rate_3':        df['pct_chg'],
        'fund_amount_3':        net_main,
        'fund_rate_3':          net_main_pct,
        'fund_amount_super_3':  _amt('buy_elg_amount') - _amt('sell_elg_amount'),
        'fund_rate_super_3':    0.0,
        'fund_amount_large_3':  _amt('buy_lg_amount')  - _amt('sell_lg_amount'),
        'fund_rate_large_3':    0.0,
        'fund_amount_medium_3': _amt('buy_md_amount')  - _amt('sell_md_amount'),
        'fund_rate_medium_3':   0.0,
        'fund_amount_small_3':  _amt('buy_sm_amount')  - _amt('sell_sm_amount'),
        'fund_rate_small_3':    0.0,
        'change_rate_5':        df['pct_chg'],
        'fund_amount_5':        net_main,
        'fund_rate_5':          net_main_pct,
        'fund_amount_super_5':  _amt('buy_elg_amount') - _amt('sell_elg_amount'),
        'fund_rate_super_5':    0.0,
        'fund_amount_large_5':  _amt('buy_lg_amount')  - _amt('sell_lg_amount'),
        'fund_rate_large_5':    0.0,
        'fund_amount_medium_5': _amt('buy_md_amount')  - _amt('sell_md_amount'),
        'fund_rate_medium_5':   0.0,
        'fund_amount_small_5':  _amt('buy_sm_amount')  - _amt('sell_sm_amount'),
        'fund_rate_small_5':    0.0,
        'change_rate_10':       df['pct_chg'],
        'fund_amount_10':       net_main,
        'fund_rate_10':         net_main_pct,
        'fund_amount_super_10': _amt('buy_elg_amount') - _amt('sell_elg_amount'),
        'fund_rate_super_10':   0.0,
        'fund_amount_large_10': _amt('buy_lg_amount')  - _amt('sell_lg_amount'),
        'fund_rate_large_10':   0.0,
        'fund_amount_medium_10':_amt('buy_md_amount')  - _amt('sell_md_amount'),
        'fund_rate_medium_10':  0.0,
        'fund_amount_small_10': _amt('buy_sm_amount')  - _amt('sell_sm_amount'),
        'fund_rate_small_10':   0.0,
    })
    return result


def save_day(df: pd.DataFrame, date_iso: str):
    """删旧数据后 INSERT ON CONFLICT DO NOTHING 写入"""
    mdb.executeSql(f'DELETE FROM "{TABLE}" WHERE "date"=\'{date_iso}\'')
    cols = df.columns.tolist()
    col_names = ', '.join(f'"{c}"' for c in cols)
    placeholders = ', '.join(['%s'] * len(cols))
    sql = f'INSERT INTO "{TABLE}" ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
    rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    return len(rows)


def run(start: str, end: str):
    log.info(f"获取交易日列表 {start} ~ {end} ...")
    trade_dates = get_trade_dates(start, end)
    log.info(f"共 {len(trade_dates)} 个交易日")

    # 预加载 stock_basic（只需一次）
    log.info("加载股票基础信息...")
    stock_basic = tsd.pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
    log.info(f"共 {len(stock_basic)} 只股票")

    total = len(trade_dates)
    ok = skip = err = 0

    for i, td in enumerate(trade_dates, 1):
        date_iso = f"{td[:4]}-{td[4:6]}-{td[6:]}"

        if already_done(date_iso):
            skip += 1
            log.info(f"[{i}/{total}] {date_iso} 已有数据，跳过")
            continue

        log.info(f"[{i}/{total}] 拉取 {date_iso} ...")
        df = fetch_one_day(td, stock_basic)
        if df is None or df.empty:
            err += 1
            continue

        n = save_day(df, date_iso)
        ok += 1
        log.info(f"[{i}/{total}] {date_iso} 写入 {n} 条")

        # 限速：每天约2次API调用（moneyflow + daily），间隔0.5s
        time.sleep(0.5)

    log.info(f"完成！ok={ok} skip={skip} err={err} / total={total}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2025-01-01')
    parser.add_argument('--end',   default=datetime.date.today().strftime('%Y-%m-%d'))
    args = parser.parse_args()
    run(args.start, args.end)
