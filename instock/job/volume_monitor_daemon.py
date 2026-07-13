#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量能监控后台调度服务（重构版）

调度规则：
  09:20以后       兜底检查上一交易日 cn_stock_hist_data / cn_stock_spot
  09:25          预计算（120日均线 + 位置分类），每天只做一次
  09:15~11:35    每分钟采集一次全市场分钟K线（XTick /doc/order/minute?code=all）
  12:59~15:05    每分钟采集一次全市场分钟K线
  15:35以后       自动同步当天 cn_stock_hist_data / cn_stock_spot
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
_daily_precheck_done_today = ''  # 已完成开盘前兜底检查的日期
_daily_after_close_done_today = ''  # 已完成收盘后每日表同步的日期
_daily_sync_running = False

DAILY_PRECHECK_TIME = datetime.time(9, 20)
DAILY_AFTER_CLOSE_SYNC_TIME = datetime.time(15, 35)


# ── 每日行情表同步 ─────────────────────────────────────────────────────────

def _previous_market_date(date_str: str) -> str:
    """
    获取上一交易日日期。优先从分钟K线找真实有数据的上一天，
    兜底用工作日回退，避免节假日/补班时完全不可用。
    """
    try:
        import instock.lib.database as mdb
        rows = mdb.executeSqlFetch(
            'SELECT MAX("date") FROM cn_stock_minute_bar WHERE "date" < %s',
            (date_str,)
        )
        if rows and rows[0][0]:
            return str(rows[0][0])
    except Exception as e:
        log.warning("查询上一分钟K交易日失败，使用工作日兜底: %s", e)

    d = datetime.date.fromisoformat(date_str) - datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def _ensure_daily_tables(date_str: str, reason: str):
    """确保指定日期 cn_stock_hist_data / cn_stock_spot 不缺。"""
    global _daily_sync_running
    if _daily_sync_running:
        log.info("每日行情表同步已在运行，跳过本次触发: %s %s", reason, date_str)
        return

    _daily_sync_running = True
    try:
        from instock.job.daily_market_sync import ensure_daily_tables
        log.info("[%s] 开始每日行情表同步检查: %s", reason, date_str)
        result = ensure_daily_tables(date_str)
        log.info(
            "[%s] 每日行情表同步完成: %s hist %s→%s, spot %s→%s",
            reason,
            date_str,
            result['before']['hist'],
            result['after']['hist'],
            result['before']['spot'],
            result['after']['spot'],
        )
    except Exception as e:
        log.error("[%s] 每日行情表同步失败(%s): %s", reason, date_str, e, exc_info=True)
    finally:
        _daily_sync_running = False


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
    global _daily_precheck_done_today, _daily_after_close_done_today

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

        # ── 09:20 以后兜底检查上一交易日每日表 ───────────────────────────────
        # 主路径在当天收盘后同步；这里仅防止昨日收盘后机器未运行或接口失败。
        if (
            DAILY_PRECHECK_TIME <= t < datetime.time(10, 0)
            and _daily_precheck_done_today != date_str
            and not _daily_sync_running
        ):
            _daily_precheck_done_today = date_str
            prev_date = _previous_market_date(date_str)
            log.info("[%s] 触发上一交易日每日表兜底检查: %s", hhmm, prev_date)
            threading.Thread(
                target=_ensure_daily_tables,
                args=(prev_date, 'pre_open_fallback'),
                daemon=True
            ).start()

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

        # ── 15:35 收盘后同步当天日线/现货表 ───────────────────────────────
        if (
            t >= DAILY_AFTER_CLOSE_SYNC_TIME
            and _daily_after_close_done_today != date_str
            and not _daily_sync_running
        ):
            _daily_after_close_done_today = date_str
            log.info("[%s] 触发收盘后每日表同步: %s", hhmm, date_str)
            threading.Thread(
                target=_ensure_daily_tables,
                args=(date_str, 'after_close'),
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
