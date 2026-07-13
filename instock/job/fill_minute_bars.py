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
from typing import Callable

cpath = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, cpath)

from instock.lib.trade_hours import expected_minutes_until, is_trade_day, today_beijing, now_hhmm
import instock.lib.database as mdb
from instock.core.minute_bar_collector import (
    MinuteBarFetcher, save_to_redis, save_to_pg,
    get_redis, redis_key, REDIS_TTL, _is_a_stock_code
)
import json

log = logging.getLogger(__name__)

_fetcher = MinuteBarFetcher()

ProgressCallback = Callable[[dict], None]


def _emit_progress(progress_callback: ProgressCallback | None, **payload):
    if not progress_callback:
        return
    try:
        progress_callback(payload)
    except Exception:
        log.debug("分钟K线补全进度回调失败", exc_info=True)


def _required_trade_minutes(until_hhmm: str) -> list[str]:
    """用于完整性判定的稳定交易分钟，排除午/收盘边界分钟。"""
    return [
        m for m in expected_minutes_until(until_hhmm)
        if m not in ('11:30', '15:00')
    ]


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
            return [r[0] for r in rows if _is_a_stock_code(r[0])]
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
               batch_sleep: float = 0.05,
               progress_callback: ProgressCallback | None = None) -> tuple[int, int, int]:
    """
    补全指定日期的分钟K线。
    策略：
      1. 先批量从 PG 读取该日全量数据写入 Redis（高效）
      2. 对 PG 中仍缺数据的股票，逐只调 XTick API 补全

    until_hhmm: 截止时间，例如 '15:00'（昨日全量）或当前时间（今日）
    返回 (ok, skip, fail)
    """
    from collections import defaultdict
    expected_minutes = _required_trade_minutes(until_hhmm)
    expected_set = set(expected_minutes)
    if len(expected_set) < 1:
        _emit_progress(
            progress_callback,
            stage='skip',
            progress=100,
            message=f"[{date}] {until_hhmm} 前无应补分钟，跳过",
        )
        return 0, 0, 0

    r = get_redis()

    # ── Step 1：批量从 PG 读取并写入 Redis ───────────────────────────────
    log.info(f"[{date}] 批量从PG读取 time<={until_hhmm} ...")
    _emit_progress(
        progress_callback,
        stage='pg_read',
        progress=8,
        message=f"[{date}] 批量读取 PG 分钟线，截止 {until_hhmm}，股票 {len(codes)} 只",
        stats={'date': date, 'until': until_hhmm, 'codes': len(codes), 'expected_count': len(expected_set)},
    )
    try:
        pg_rows = mdb.executeSqlFetch(
            'SELECT code,time,open,close,high,low,volume,amount,pre_close '
            'FROM cn_stock_minute_bar WHERE date=%s AND time<=%s ORDER BY code,time',
            (date, until_hhmm)
        )
    except Exception as e:
        log.error(f"PG批量查询失败: {e}")
        _emit_progress(
            progress_callback,
            stage='pg_read_error',
            progress=8,
            level='warning',
            message=f"[{date}] PG 批量查询失败：{e}",
        )
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
    _emit_progress(
        progress_callback,
        stage='pg_group',
        progress=18,
        message=f"[{date}] PG 读取完成：{len(pg_rows or []):,} 条，覆盖 {len(by_code)} 只股票",
        stats={'pg_rows': len(pg_rows or []), 'pg_codes': len(by_code)},
    )

    # 写入 Redis（pipeline批量）
    pipe = r.pipeline(transaction=False)
    pg_ok = 0
    for code, bars in by_code.items():
        times = {b.get('time') for b in bars}
        if expected_set.issubset(times):
            pipe.set(redis_key(date, code), json.dumps(bars, ensure_ascii=False), ex=REDIS_TTL)
            pg_ok += 1
            if pg_ok % 500 == 0:
                pipe.execute()
                pipe = r.pipeline(transaction=False)
    pipe.execute()
    log.info(f"[{date}] PG→Redis: {pg_ok} 只完整写入，PG共 {len(by_code)} 只有数据")
    _emit_progress(
        progress_callback,
        stage='redis_write',
        progress=30,
        message=f"[{date}] PG→Redis 完成：{pg_ok} 只完整写入，PG 共 {len(by_code)} 只有数据",
        stats={'pg_ok': pg_ok, 'pg_codes': len(by_code)},
    )

    # ── Step 2：PG 中缺数据的股票，调 XTick API ─────────────────────────
    missing = []
    for code in codes:
        times = {b.get('time') for b in by_code.get(code, [])}
        if not expected_set.issubset(times):
            missing.append(code)

    ok = pg_ok
    skip = max(0, len(codes) - len(missing) - pg_ok)
    fail = 0

    today = today_beijing().strftime('%Y-%m-%d')

    if missing:
        log.info(f"[{date}] XTick补全 {len(missing)} 只缺数据股票...")
        _emit_progress(
            progress_callback,
            stage='xtick_start',
            progress=35,
            message=f"[{date}] 还有 {len(missing)} 只股票需要调用 XTick 单股接口补全",
            stats={'missing': len(missing), 'ok': ok, 'skip': skip, 'fail': fail},
        )
        for i, code in enumerate(missing):
            try:
                if date == today:
                    bars = _fetcher.fetch_single_realtime(code)
                else:
                    bars = _fetcher.fetch_history(code, date)
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
                valid_times = {b.get('time') for b in valid}
                if expected_set.issubset(valid_times):
                    ok += 1
                else:
                    still_missing = sorted(expected_set - valid_times)
                    fail += 1
                    if fail <= 5:
                        _emit_progress(
                            progress_callback,
                            stage='xtick_incomplete',
                            progress=min(98, 35 + int((i + 1) * 60 / max(1, len(missing)))),
                            level='warning',
                            message=f"[{date}] XTick 返回 {code} 仍缺 {','.join(still_missing[:5])}",
                            stats={'index': i + 1, 'total': len(missing), 'code': code, 'missing_times': still_missing[:20]},
                        )
            except Exception as e:
                fail += 1
                if fail <= 5:
                    log.warning(f"XTick补全 {code}/{date} 失败: {e}")
                    _emit_progress(
                        progress_callback,
                        stage='xtick_warning',
                        progress=min(98, 35 + int((i + 1) * 60 / max(1, len(missing)))),
                        level='warning',
                        message=f"[{date}] XTick 补全 {code} 失败：{e}",
                        stats={'index': i + 1, 'total': len(missing), 'ok': ok, 'skip': skip, 'fail': fail},
                    )
            if (i + 1) == 1 or (i + 1) % 20 == 0 or (i + 1) == len(missing):
                _emit_progress(
                    progress_callback,
                    stage='xtick_progress',
                    progress=min(98, 35 + int((i + 1) * 60 / max(1, len(missing)))),
                    message=(
                        f"[{date}] XTick 补全进度 {i + 1}/{len(missing)}，"
                        f"当前 {code}，ok={ok} skip={skip} fail={fail}"
                    ),
                    stats={'index': i + 1, 'total': len(missing), 'code': code, 'ok': ok, 'skip': skip, 'fail': fail},
                )
            if batch_sleep and (i + 1) % 10 == 0:
                time.sleep(batch_sleep)
    else:
        _emit_progress(
            progress_callback,
            stage='no_missing',
            progress=95,
            message=f"[{date}] PG/Redis 已覆盖期望分钟，无需调用 XTick 单股补全",
            stats={'ok': ok, 'skip': skip, 'fail': fail},
        )

    return ok, skip, fail


def fill_yesterday(codes: list[str] = None,
                   progress_callback: ProgressCallback | None = None) -> None:
    """补充昨日全量分钟K线（242根）"""
    today = today_beijing().strftime('%Y-%m-%d')
    yest  = _get_prev_trade_date(today)
    if not yest:
        log.warning("无法确定昨日交易日")
        _emit_progress(progress_callback, stage='skip', progress=100, level='warning', message="无法确定昨日交易日")
        return

    if codes is None:
        codes = _get_all_codes()
    if not codes:
        log.warning("股票列表为空，跳过昨日补全")
        _emit_progress(progress_callback, stage='skip', progress=100, level='warning', message="股票列表为空，跳过昨日补全")
        return

    log.info(f"补充昨日({yest})分钟K线，共 {len(codes)} 只...")
    _emit_progress(progress_callback, stage='start_yesterday', progress=1, message=f"补充昨日({yest})分钟K线，共 {len(codes)} 只")
    ok, skip, fail = _fill_date(yest, '15:00', codes, progress_callback=progress_callback)
    log.info(f"昨日补全完成: ok={ok} skip={skip} fail={fail}")
    _emit_progress(progress_callback, stage='done_yesterday', progress=100, message=f"昨日补全完成：ok={ok} skip={skip} fail={fail}")


def fill_today(codes: list[str] = None,
               progress_callback: ProgressCallback | None = None) -> None:
    """补充今日 09:30 到当前北京时间的分钟K线"""
    today = today_beijing().strftime('%Y-%m-%d')
    until = now_hhmm()   # 北京时间 HH:MM

    if until < '09:30':
        log.info(f"北京时间 {until}，未到开盘，跳过今日补全")
        _emit_progress(progress_callback, stage='skip', progress=100, message=f"北京时间 {until}，未到开盘，跳过今日补全")
        return

    # 午休期间截止上午收盘
    if '11:30' < until < '13:00':
        until = '11:30'

    if codes is None:
        _emit_progress(progress_callback, stage='load_codes', progress=2, message="读取待补全股票列表")
        codes = _get_all_codes()
    if not codes:
        log.warning("股票列表为空，跳过今日补全")
        _emit_progress(progress_callback, stage='skip', progress=100, level='warning', message="股票列表为空，跳过今日补全")
        return

    expected = len(expected_minutes_until(until))
    log.info(f"补充今日({today})分钟K线到 {until}（期望{expected}根），共 {len(codes)} 只...")
    _emit_progress(
        progress_callback,
        stage='start_today',
        progress=5,
        message=f"补充今日({today})分钟K线到 {until}（期望{expected}根），共 {len(codes)} 只",
        stats={'date': today, 'until': until, 'expected': expected, 'codes': len(codes)},
    )
    ok, skip, fail = _fill_date(today, until, codes, progress_callback=progress_callback)
    log.info(f"今日补全完成: ok={ok} skip={skip} fail={fail}")
    _emit_progress(
        progress_callback,
        stage='done_today',
        progress=100,
        message=f"今日补全完成：ok={ok} skip={skip} fail={fail}",
        stats={'ok': ok, 'skip': skip, 'fail': fail},
    )


def fill_both(progress_callback: ProgressCallback | None = None) -> None:
    """同时补充昨日全量 + 今日到当前时间"""
    codes = _get_all_codes()
    fill_yesterday(codes, progress_callback=progress_callback)
    fill_today(codes, progress_callback=progress_callback)


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
