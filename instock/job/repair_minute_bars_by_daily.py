#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按日K一致性修复指定日期的分钟K线。

用途：
  当历史分钟线被错误写入其它日期时，使用 XTick 历史分钟接口重拉并覆盖
  cn_stock_minute_bar 与 Redis minute_bar:{date}:{code}。
"""
import argparse
import json
import logging
import os
import sys
import time

cpath = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, cpath)

import instock.lib.database as mdb
from instock.core.minute_bar_collector import (
    MinuteBarFetcher, REDIS_TTL, get_redis, redis_key, save_to_pg,
)

log = logging.getLogger(__name__)


def _trade_bars(bars: list[dict]) -> list[dict]:
    return [
        b for b in bars
        if ('09:30' <= b.get('time', '') <= '11:30')
        or ('13:00' <= b.get('time', '') <= '15:00')
    ]


def find_suspects(date: str,
                  high_ratio: float = 1.03,
                  low_ratio: float = 0.97,
                  volume_ratio: float = 1.5) -> list[dict]:
    rows = mdb.executeSqlFetch(
        '''
        WITH m AS (
            SELECT code,
                   MAX(high) FILTER (WHERE volume > 0) AS minute_high,
                   MIN(low) FILTER (WHERE volume > 0) AS minute_low,
                   SUM(volume) AS minute_volume
            FROM cn_stock_minute_bar
            WHERE date=%s
            GROUP BY code
        ),
        h AS (
            SELECT code, high AS hist_high, low AS hist_low, volume AS hist_volume
            FROM cn_stock_hist_data
            WHERE date=%s
        )
        SELECT h.code, h.hist_high, m.minute_high, h.hist_low, m.minute_low,
               h.hist_volume, m.minute_volume
        FROM h
        JOIN m ON m.code = h.code
        WHERE m.minute_high > h.hist_high * %s
           OR m.minute_low < h.hist_low * %s
           OR m.minute_volume > h.hist_volume * %s
        ORDER BY h.code
        ''',
        (date, date, high_ratio, low_ratio, volume_ratio)
    )
    suspects = []
    for r in rows or []:
        suspects.append({
            'code': r[0],
            'hist_high': float(r[1] or 0),
            'minute_high': float(r[2] or 0),
            'hist_low': float(r[3] or 0),
            'minute_low': float(r[4] or 0),
            'hist_volume': float(r[5] or 0),
            'minute_volume': float(r[6] or 0),
        })
    return suspects


def repair_one(fetcher: MinuteBarFetcher, date: str, code: str,
               dry_run: bool = False) -> tuple[bool, str, int]:
    bars = _trade_bars(fetcher.fetch_history(code, date))
    if len(bars) < 230:
        return False, f'history bars too few: {len(bars)}', len(bars)

    for b in bars:
        b['code'] = code

    if dry_run:
        return True, 'dry-run', len(bars)

    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM cn_stock_minute_bar WHERE date=%s AND code=%s',
                (date, code)
            )
    save_to_pg(bars, date)
    get_redis().set(redis_key(date, code), json.dumps(bars, ensure_ascii=False), ex=REDIS_TTL)
    return True, 'repaired', len(bars)


def clear_signal_caches(date: str):
    r = get_redis()
    patterns = [
        f'leader_strength:{date}:*',
        f'mainline_core:*:{date}:*',
    ]
    for pattern in patterns:
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=1000)
            if keys:
                deleted += r.delete(*keys)
            if cursor == 0:
                break
        log.info("清理缓存 %s: %s", pattern, deleted)


def main():
    parser = argparse.ArgumentParser(description='按日K一致性批量修复分钟K线')
    parser.add_argument('--date', required=True, help='目标日期 YYYY-MM-DD')
    parser.add_argument('--code', action='append', help='只修指定股票，可重复传')
    parser.add_argument('--limit', type=int, default=0, help='最多修复多少只，0=不限制')
    parser.add_argument('--sleep', type=float, default=0.08, help='每只之间暂停秒数')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--high-ratio', type=float, default=1.03)
    parser.add_argument('--low-ratio', type=float, default=0.97)
    parser.add_argument('--volume-ratio', type=float, default=1.3)
    args = parser.parse_args()

    fetcher = MinuteBarFetcher()
    suspects = find_suspects(args.date, args.high_ratio, args.low_ratio, args.volume_ratio)
    if args.code:
        wanted = {str(c).zfill(6) for c in args.code}
        suspects = [s for s in suspects if s['code'] in wanted]
    if args.limit > 0:
        suspects = suspects[:args.limit]

    log.info("[%s] 疑似污染股票: %s", args.date, len(suspects))
    ok = fail = 0
    for i, item in enumerate(suspects, start=1):
        code = item['code']
        try:
            repaired, msg, cnt = repair_one(fetcher, args.date, code, args.dry_run)
            if repaired:
                ok += 1
                if ok <= 10 or ok % 50 == 0:
                    log.info("[%s/%s] %s %s bars=%s", i, len(suspects), code, msg, cnt)
            else:
                fail += 1
                log.warning("[%s/%s] %s 跳过: %s", i, len(suspects), code, msg)
        except Exception as e:
            fail += 1
            log.warning("[%s/%s] %s 修复失败: %s", i, len(suspects), code, e)
        if args.sleep:
            time.sleep(args.sleep)

    if not args.dry_run and ok:
        clear_signal_caches(args.date)
    log.info("完成 date=%s ok=%s fail=%s dry_run=%s", args.date, ok, fail, args.dry_run)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
    )
    main()
