#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开盘前预计算服务（每日09:25执行一次）
计算全市场120日均线、昨日换手率，并缓存到 Redis
供盘中评分引擎使用

Redis Key:
  pre_calc:{date}:ma120    → {code: {"ma120": x, "high120": x, "position": "low/break/high/other",
                                     "turnover_y": x, "change_y": x, "close_y": x}}
  pre_calc:{date}:sectors  → {code: [sector1, sector2, ...]}
"""
import json
import logging
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import instock.lib.database as mdb
from instock.core.minute_bar_collector import get_redis

log = logging.getLogger(__name__)

REDIS_TTL_PRECALC = 2 * 24 * 3600   # 预计算缓存2天


def _redis_key_precalc(date: str) -> str:
    return f'pre_calc:{date}:ma120'


def _redis_key_sectors() -> str:
    return 'pre_calc:sectors'


# ── 位置判断 ──────────────────────────────────────────────────────────────

def _judge_position(close: float, high1: float, high2: float) -> str:
    """
    新版位置判断（基于昨日收盘价与120日高点比较）：
      score2 : High1 > close×1.2 且 High2 > close×1.2  （价格远低于两个高点，深度低位）
      score1 : High1 > close×1.2 且 High2 ≤ close×1.2 （最高价高但收盘高点不高，相对低位）
      score0 : 其他（价格接近或超过高点）
      other  : 数据不足
    """
    if not close or not high1 or not high2:
        return 'other'
    h1_above = high1 > close * 1.2
    h2_above = high2 > close * 1.2
    if h1_above and h2_above:
        return 'score2'
    if h1_above and not h2_above:
        return 'score1'
    return 'score0'


# ── 主计算函数 ────────────────────────────────────────────────────────────

def run_pre_calc(date: str = None):
    """
    计算全市场120日均线和位置分类，缓存到 Redis
    date: 计算日期（默认今日），格式 YYYY-MM-DD
    """
    if date is None:
        date = datetime.date.today().strftime('%Y-%m-%d')

    log.info(f"开始预计算 date={date}")

    # ── 1. 取全市场近120个交易日的日K数据 ─────────────────────────────
    # 只取 code, date, close, high, turnover, quote_change
    sql = """
        SELECT code, date, close, high,
               COALESCE(turnover, 0)      AS turnover,
               COALESCE(quote_change, 0)  AS quote_change
        FROM cn_stock_hist_data
        WHERE date <= %s
        ORDER BY code, date
    """
    rows = mdb.executeSqlFetch(sql, (date,))
    if not rows:
        log.warning("无历史K线数据，预计算退出")
        return

    # ── 2. 按股票分组，计算120日均线、120日最高价 ──────────────────────
    from collections import defaultdict
    stock_data: dict[str, list] = defaultdict(list)
    for code, dt, close, high, turnover, quote_change in rows:
        stock_data[str(code)].append({
            'date': str(dt), 'close': float(close or 0),
            'high': float(high or 0), 'turnover': float(turnover or 0),
            'quote_change': float(quote_change or 0),
        })

    result = {}
    for code, bars in stock_data.items():
        # 取最近120条
        recent = bars[-120:]
        if len(recent) < 20:    # 数据不足20日，跳过
            continue

        closes   = [b['close'] for b in recent if b['close'] > 0]
        highs    = [b['high']  for b in recent if b['high']  > 0]
        ma120    = sum(closes) / len(closes) if closes else 0
        high1    = max(highs)   if highs  else 0   # 120日最高价（最高bar的high）
        high2    = max(closes)  if closes else 0   # 120日最高收盘价

        # 昨日（最后一根）数据
        last       = bars[-1]
        close_y    = last['close']
        turnover_y = last['turnover']
        change_y   = last['quote_change']

        # 前日数据（用于效率因子连续2日判断）
        prev       = bars[-2] if len(bars) >= 2 else last
        change_prev   = prev['quote_change']
        turnover_prev = prev['turnover']

        position = _judge_position(close_y, high1, high2)

        result[code] = {
            'ma120':        round(ma120,  4),
            'high1':        round(high1,  4),   # 120日最高价
            'high2':        round(high2,  4),   # 120日最高收盘价
            'high120':      round(high1,  4),   # 兼容旧字段
            'position':     position,
            'close_y':      close_y,
            'turnover_y':   turnover_y,
            'change_y':     change_y,
            'turnover_prev': turnover_prev,
            'change_prev':   change_prev,
        }

    # ── 3. 写入 Redis ──────────────────────────────────────────────────
    r = get_redis()
    key = _redis_key_precalc(date)
    r.set(key, json.dumps(result, ensure_ascii=False), ex=REDIS_TTL_PRECALC)
    log.info(f"预计算完成: {len(result)} 只股票写入Redis key={key}")

    # ── 4. 同步板块映射到 Redis ────────────────────────────────────────
    _cache_sectors()


def _cache_sectors():
    """从数据库加载板块映射，缓存到Redis（长期有效，编辑后调用刷新）"""
    rows = mdb.executeSqlFetch('SELECT code, sector FROM cn_stock_sector_map')
    if not rows:
        log.info("板块映射表为空，跳过缓存")
        return
    sectors: dict[str, list] = {}
    for code, sector in rows:
        sectors.setdefault(str(code), []).append(sector)
    r = get_redis()
    r.set(_redis_key_sectors(), json.dumps(sectors, ensure_ascii=False))
    log.info(f"板块映射缓存完成: {len(sectors)} 只股票")


# ── 读取工具函数（供评分引擎使用）────────────────────────────────────────

def get_pre_calc(date: str) -> dict:
    """
    获取预计算结果
    返回: {code: {ma120, high120, position, close_y, turnover_y, change_y, ...}}
    """
    try:
        r = get_redis()
        raw = r.get(_redis_key_precalc(date))
        return json.loads(raw) if raw else {}
    except Exception as e:
        log.error(f"读取预计算缓存失败: {e}")
        return {}


def get_sectors() -> dict:
    """
    获取板块映射
    返回: {code: [sector1, sector2, ...]}
    """
    try:
        r = get_redis()
        raw = r.get(_redis_key_sectors())
        return json.loads(raw) if raw else {}
    except Exception as e:
        log.error(f"读取板块缓存失败: {e}")
        return {}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--date', default=None, help='计算日期 YYYY-MM-DD，默认今日')
    args = p.parse_args()
    run_pre_calc(args.date)
