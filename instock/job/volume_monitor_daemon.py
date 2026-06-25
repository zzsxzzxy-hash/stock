#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量能监控后台调度服务（重构版）

调度规则：
  09:25          预计算（120日均线 + 位置分类），每天只做一次
  09:15~11:35    每分钟采集一次全市场分钟K线（XTick /doc/order/minute?code=all）
  12:59~15:05    每分钟采集一次全市场分钟K线
  采集后刷新评分排行缓存

启动时检查：
  - 若今日/昨日 Redis 分钟数据不完整，自动触发 fill_minute_bars 补全

独立进程运行：python3 -m instock.job.volume_monitor_daemon
"""
import logging
import datetime
import time
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from instock.lib.trade_hours import (
    is_trade_day, is_collect_window, is_trade_time,
    PRE_CALC_TIME, now_hhmm
)

log = logging.getLogger(__name__)

_running = True
_pre_calc_done_today = ''  # 已完成预计算的日期
_fill_done_today     = ''  # 已完成补全检查的日期


# ── 预计算 ─────────────────────────────────────────────────────────────────

def _do_pre_calc(date_str: str):
    """09:25 执行预计算，当天只跑一次"""
    global _pre_calc_done_today
    if _pre_calc_done_today == date_str:
        return
    try:
        log.info(f"[{date_str}] 开始预计算...")
        from instock.core.volume_pre_calc import run_pre_calc
        run_pre_calc(date_str)
        _pre_calc_done_today = date_str
        log.info(f"[{date_str}] 预计算完成")
    except Exception as e:
        log.error(f"预计算失败: {e}", exc_info=True)


# ── 启动时检查并补全 ───────────────────────────────────────────────────────

def _check_and_fill_on_startup(date_str: str):
    """
    启动时检查 Redis 中今日和昨日分钟数据是否完整。
    不完整则调用 fill_minute_bars 补全。
    """
    global _fill_done_today
    if _fill_done_today == date_str:
        return
    _fill_done_today = date_str

    try:
        log.info("启动检查：验证 Redis 分钟数据完整性...")
        from instock.job.fill_minute_bars import fill_both
        fill_both()
        log.info("启动检查完成")
    except Exception as e:
        log.error(f"启动补全失败: {e}", exc_info=True)


# ── 每分钟采集 ─────────────────────────────────────────────────────────────

def _do_collect(date_str: str, hhmm: str):
    """
    调用 /doc/order/minute?code=all 采集全市场最新一分钟 → Redis + PG
    """
    try:
        from instock.core.minute_bar_collector import collect_once
        n = collect_once()
        log.info(f"[{hhmm}] 采集完成: {n}条")
    except Exception as e:
        log.error(f"[{hhmm}] 采集失败: {e}")


# ── 评分排行刷新 ───────────────────────────────────────────────────────────

def _do_refresh_rank(date_str: str, hhmm: str):
    """刷新量能评分排行缓存（all/low/break 三组）"""
    try:
        from instock.core.volume_rank_engine import refresh_rank_cache
        for flt in ('all', 'low', 'break'):
            refresh_rank_cache(date_str, hhmm, flt)
        log.debug(f"[{hhmm}] 排行刷新完成")
    except Exception as e:
        log.error(f"[{hhmm}] 排行刷新失败: {e}")


# ── 主循环 ─────────────────────────────────────────────────────────────────

def run():
    log.info("量能监控调度服务启动")

    last_collect_minute = ''   # 上次采集的分钟（每分钟只采集一次）
    startup_fill_done   = False

    while _running:
        now      = datetime.datetime.now()
        today    = now.date()
        t        = now.time()
        date_str = today.strftime('%Y-%m-%d')
        hhmm     = now.strftime('%H:%M')

        # 非交易日跳过
        if not is_trade_day(today):
            time.sleep(60)
            continue

        # 不在任何活跃窗口，低频轮询
        if not (is_collect_window(t) or t >= PRE_CALC_TIME):
            time.sleep(10)
            continue

        # ── 09:25 预计算 ─────────────────────────────────────────────────
        if t >= PRE_CALC_TIME and t.hour == 9 and t.minute == 25 and now.second < 5:
            threading.Thread(
                target=_do_pre_calc,
                args=(date_str,),
                daemon=True
            ).start()

        # ── 进入采集窗口后，首次启动补全检查 ─────────────────────────────
        if is_collect_window(t) and hhmm >= '09:32' and not startup_fill_done:
            startup_fill_done = True
            log.info("进入采集窗口，触发启动补全检查...")
            threading.Thread(
                target=_check_and_fill_on_startup,
                args=(date_str,),
                daemon=True
            ).start()

        # ── 每分钟整点后第 5 秒采集一次 ──────────────────────────────────
        # 等待第5秒确保接口数据已准备好（XTick通常在整分后1-3秒更新）
        if is_collect_window(t) and hhmm != last_collect_minute and now.second >= 5:
            last_collect_minute = hhmm
            log.info(f"[{now.strftime('%H:%M:%S')}] 触发分钟采集")

            # 采集
            threading.Thread(
                target=_do_collect,
                args=(date_str, hhmm),
                daemon=True
            ).start()

            # 交易时段内刷新评分
            if is_trade_time(t):
                threading.Thread(
                    target=_do_refresh_rank,
                    args=(date_str, hhmm),
                    daemon=True
                ).start()

        time.sleep(1)

    log.info("量能监控调度服务停止")


def stop():
    global _running
    _running = False


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('/tmp/volume_monitor.log', encoding='utf-8'),
        ]
    )
    run()
