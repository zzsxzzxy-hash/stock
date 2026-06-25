#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补K线脚本 fill_minute_bars.py
功能：
  1. 补充昨日全量分钟K线（240根）
  2. 补充今日 09:30 到当前时间的分钟K线
  数据来源：http://api.xtick.top/doc/kline/minute?type=1&code=股票代码

调用时机：
  - daemon 启动时检测 Redis 中今日/昨日数据不完整时触发
  - 也可手动运行：python3 -m instock.job.fill_minute_bars

注意：只在交易时间内（或手动运行时）补充今日数据；
      昨日数据随时可以补。
"""
import logging
import datetime
import time
import os
import sys

cpath = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, cpath)

from instock.lib.trade_hours import expected_minutes_until, is_trade_day, today_beijing, now_hhmm
import instock.lib.database as mdb
from instock.core.minute_bar_collector import (
    MinuteBarFetcher, save_to_redis, save_to_pg,
    get_redis, redis_key, REDIS_TTL
)
import json

log = logging.getLogger(__name__)

_fetcher = MinuteBarFetcher()


def _get_prev_trade_date(date_str: str) -> str | None:
    """从 cn_stock_minute_bar 查真实前一交易日"""
    try:
        rows = mdb.executeSqlFetch(
            'SELECT DISTINCT date FROM cn_stock_minute_bar WHERE date < %s ORDER BY date DESC LIMIT 1',
            (date_str,)
        )
        if rows and rows[0][0]:
            d = rows[0][0]
            return d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
    except Exception:
        pass
    # 回退：跳过周末
    d = datetime.date.fromisoformat(date_str)
    for delta in range(1, 8):
        prev = d - datetime.timedelta(days=delta)
        if prev.weekday() < 5:
            return prev.strftime('%Y-%m-%d')
    return None


def _get_all_codes() -> list[str]:
    """获取全市场股票代码（从最近一日分钟数据）"""
    try:
        rows = mdb.executeSqlFetch(
            'SELECT DISTINCT code FROM cn_stock_minute_bar ORDER BY code'
        )
        if rows:
            return [r[0] for r in rows]
    except Exception as e:
        log.error(f"获取股票列表失败: {e}")
    return []


def _redis_bar_count(date: str, code: str) -> int:
    """查询 Redis 中某股某日已有的分钟K线条数"""
    try:
        r = get_redis()
        raw = r.get(redis_key(date, code))
        if raw:
            return len(json.loads(raw))
    except Exception:
        pass
    return 0


def _pg_bar_count(date: str, code: str) -> int:
    """查询 PG 中某股某日已有的分钟K线条数"""
    try:
        rows = mdb.executeSqlFetch(
            'SELECT COUNT(*) FROM cn_stock_minute_bar WHERE date=%s AND code=%s',
            (date, code)
        )
        return int(rows[0][0]) if rows else 0
    except Exception:
        return 0


def _fill_date(date: str, until_hhmm: str, codes: list[str],
               batch_sleep: float = 0.05) -> tuple[int, int, int]:
    """
    补全指定日期的分钟K线。
    策略：
      1. 先批量从 PG 读取该日全量数据写入 Redis（高效）
      2. 对 PG 中仍缺数据的股票，逐只调 XTick API 补全

    until_hhmm: 截止时间，例如 '15:00'（昨日全量）或当前时间（今日）
    返回 (ok, skip, fail)
    """
    from collections import defaultdict
    expected_count = len(expected_minutes_until(until_hhmm))
    if expected_count < 1:
        return 0, 0, 0

    r = get_redis()

    # ── Step 1：批量从 PG 读取并写入 Redis ───────────────────────────────
    log.info(f"[{date}] 批量从PG读取 time<={until_hhmm} ...")
    try:
        pg_rows = mdb.executeSqlFetch(
            'SELECT code,time,open,close,high,low,volume,amount,pre_close '
            'FROM cn_stock_minute_bar WHERE date=%s AND time<=%s ORDER BY code,time',
            (date, until_hhmm)
        )
    except Exception as e:
        log.error(f"PG批量查询失败: {e}")
        pg_rows = []

    by_code = defaultdict(list)
    for row in (pg_rows or []):
        c, t, o, cl, h, l, v, a, pc = row
        by_code[c].append({
            'time': str(t), 'open': float(o or 0), 'close': float(cl or 0),
            'high': float(h or 0), 'low': float(l or 0),
            'volume': float(v or 0), 'amount': float(a or 0),
            'pre_close': float(pc or 0)
        })

    # 写入 Redis（pipeline批量）
    pipe = r.pipeline(transaction=False)
    pg_ok = 0
    for code, bars in by_code.items():
        if len(bars) >= expected_count - 2:
            pipe.set(redis_key(date, code), json.dumps(bars, ensure_ascii=False), ex=REDIS_TTL)
            pg_ok += 1
            if pg_ok % 500 == 0:
                pipe.execute()
                pipe = r.pipeline(transaction=False)
    pipe.execute()
    log.info(f"[{date}] PG→Redis: {pg_ok} 只完整写入，PG共 {len(by_code)} 只有数据")

    # ── Step 2：PG 中缺数据的股票，调 XTick API ─────────────────────────
    codes_set = set(codes)
    pg_codes  = set(by_code.keys())
    missing   = [c for c in codes if c not in pg_codes or
                 len(by_code.get(c, [])) < expected_count - 2]

    ok = pg_ok
    skip = len(codes) - len(missing) - pg_ok
    fail = 0

    if missing:
        log.info(f"[{date}] XTick补全 {len(missing)} 只缺数据股票...")
        for i, code in enumerate(missing):
            try:
                bars = _fetcher.fetch_single_realtime(code)
                if not bars:
                    fail += 1
                    continue
                valid = [b for b in bars
                         if b['time'] <= until_hhmm and
                            (('09:30' <= b['time'] <= '11:30') or
                             ('13:00' <= b['time'] <= '15:00'))]
                if not valid:
                    skip += 1
                    continue
                for b in valid:
                    b['code'] = code
                save_to_redis({code: valid}, date)
                save_to_pg(valid, date)
                ok += 1
            except Exception as e:
                fail += 1
                if fail <= 5:
                    log.warning(f"XTick补全 {code}/{date} 失败: {e}")
            if batch_sleep and (i + 1) % 10 == 0:
                time.sleep(batch_sleep)

    return ok, skip, fail


def fill_yesterday(codes: list[str] = None) -> None:
    """补充昨日全量分钟K线（242根）"""
    today = today_beijing().strftime('%Y-%m-%d')
    yest  = _get_prev_trade_date(today)
    if not yest:
        log.warning("无法确定昨日交易日")
        return

    if codes is None:
        codes = _get_all_codes()
    if not codes:
        log.warning("股票列表为空，跳过昨日补全")
        return

    log.info(f"补充昨日({yest})分钟K线，共 {len(codes)} 只...")
    ok, skip, fail = _fill_date(yest, '15:00', codes)
    log.info(f"昨日补全完成: ok={ok} skip={skip} fail={fail}")


def fill_today(codes: list[str] = None) -> None:
    """补充今日 09:30 到当前北京时间的分钟K线"""
    today = today_beijing().strftime('%Y-%m-%d')
    until = now_hhmm()   # 北京时间 HH:MM

    if until < '09:30':
        log.info(f"北京时间 {until}，未到开盘，跳过今日补全")
        return

    # 午休期间截止上午收盘
    if '11:30' < until < '13:00':
        until = '11:30'

    if codes is None:
        codes = _get_all_codes()
    if not codes:
        log.warning("股票列表为空，跳过今日补全")
        return

    expected = len(expected_minutes_until(until))
    log.info(f"补充今日({today})分钟K线到 {until}（期望{expected}根），共 {len(codes)} 只...")
    ok, skip, fail = _fill_date(today, until, codes)
    log.info(f"今日补全完成: ok={ok} skip={skip} fail={fail}")


def fill_both() -> None:
    """同时补充昨日全量 + 今日到当前时间"""
    codes = _get_all_codes()
    fill_yesterday(codes)
    fill_today(codes)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S'
    )
    import argparse
    parser = argparse.ArgumentParser(description='补充分钟K线数据到Redis+PG')
    parser.add_argument('--mode', choices=['today', 'yesterday', 'both'],
                        default='both', help='补充模式')
    args = parser.parse_args()

    if args.mode == 'today':
        fill_today()
    elif args.mode == 'yesterday':
        fill_yesterday()
    else:
        fill_both()
