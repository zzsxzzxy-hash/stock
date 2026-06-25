#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局交易时间工具
所有涉及"是否在交易时间内"的判断统一从这里调用
时间均以北京时间（Asia/Shanghai）为准。

交易时段：
  上午  09:30 ~ 11:30
  下午  13:00 ~ 15:00

采集窗口（比交易时段略宽）：
  上午  09:15 ~ 11:35   （集合竞价 + 缓冲）
  下午  12:59 ~ 15:05
"""
import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

TZ_BEIJING = ZoneInfo('Asia/Shanghai')

# ── 交易时段 ──────────────────────────────────────────────────────────────
MORNING_OPEN    = datetime.time(9,  30)
MORNING_CLOSE   = datetime.time(11, 30)
AFTERNOON_OPEN  = datetime.time(13,  0)
AFTERNOON_CLOSE = datetime.time(15,  0)

# ── 采集窗口（含集合竞价缓冲） ─────────────────────────────────────────────
COLLECT_MORNING_START   = datetime.time(9,  15)
COLLECT_MORNING_END     = datetime.time(11, 35)
COLLECT_AFTERNOON_START = datetime.time(12, 59)
COLLECT_AFTERNOON_END   = datetime.time(15,  5)

# ── 预计算触发时间 ─────────────────────────────────────────────────────────
PRE_CALC_TIME = datetime.time(9, 25)


def now_beijing() -> datetime.datetime:
    """返回当前北京时间"""
    return datetime.datetime.now(TZ_BEIJING)


def now_time() -> datetime.time:
    """返回当前北京时间的 time 部分"""
    return now_beijing().time().replace(tzinfo=None)


def now_hhmm() -> str:
    """返回当前北京时间字符串 HH:MM"""
    return now_beijing().strftime('%H:%M')


def today_beijing() -> datetime.date:
    """返回今天的北京日期"""
    return now_beijing().date()


def is_trade_time(t: datetime.time = None) -> bool:
    """是否在交易时段内（09:30~11:30 或 13:00~15:00），基于北京时间"""
    t = t or now_time()
    return (MORNING_OPEN <= t <= MORNING_CLOSE) or \
           (AFTERNOON_OPEN <= t <= AFTERNOON_CLOSE)


def is_collect_window(t: datetime.time = None) -> bool:
    """是否在采集窗口内（比交易时段略宽），基于北京时间"""
    t = t or now_time()
    return (COLLECT_MORNING_START <= t <= COLLECT_MORNING_END) or \
           (COLLECT_AFTERNOON_START <= t <= COLLECT_AFTERNOON_END)


def is_trade_day(d: datetime.date = None) -> bool:
    """
    简单判断是否为交易日（排除周末）
    节假日不剔除，实际使用时 collect_window 内无数据即自然跳过
    """
    d = d or today_beijing()
    return d.weekday() < 5  # 0=Mon ... 4=Fri


def expected_minutes_until(until_hhmm: str) -> list[str]:
    """
    返回 09:30 到 until_hhmm（含）之间所有交易分钟列表
    例：expected_minutes_until('10:02') → ['09:30','09:31',...,'10:02']
    全天（15:00）共 242 根：上午 121 根 + 下午 121 根
    """
    mins = []
    for h in range(9, 16):
        for m in range(60):
            t = f'{h:02d}:{m:02d}'
            if t > until_hhmm:
                return mins
            if ('09:30' <= t <= '11:30') or ('13:00' <= t <= '15:00'):
                mins.append(t)
    return mins


def current_expected_minutes() -> list[str]:
    """返回截至当前北京时间应有的交易分钟列表"""
    return expected_minutes_until(now_hhmm())
