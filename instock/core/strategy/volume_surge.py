#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
爆量股票策略
条件：
  1. 今日成交量 >= 昨日成交量 × 1.9
  2. 今日换手率 >= 5%
  3. 今日上涨（收盘价 > 开盘价，且涨跌幅 > 0）
  4. 排除ST股（名称含 ST）
"""


def check(code_name, data, date=None, threshold=3):
    stock_name = code_name[1] if len(code_name) > 1 else ''

    # 排除 ST
    if 'ST' in str(stock_name).upper():
        return False

    if date is None:
        end_date = code_name[0]
    else:
        end_date = date.strftime("%Y-%m-%d")

    if end_date is not None:
        mask = data['date'] <= end_date
        data = data.loc[mask].copy()

    # 至少需要2条记录（今日+昨日）
    if len(data) < 2:
        return False

    today = data.iloc[-1]
    yesterday = data.iloc[-2]

    vol_today = float(today.get('volume', 0) or 0)
    vol_yesterday = float(yesterday.get('volume', 0) or 0)
    turnover = float(today.get('turnover', 0) or 0)
    p_change = float(today.get('quote_change', 0) or 0)
    close = float(today.get('close', 0) or 0)
    open_ = float(today.get('open', 0) or 0)

    # 昨日成交量必须有效
    if vol_yesterday <= 0:
        return False

    # 条件1：成交量爆量 >= 1.9倍
    if vol_today < vol_yesterday * 1.9:
        return False

    # 条件2：换手率 >= 5%
    if turnover < 5.0:
        return False

    # 条件3：上涨（涨跌幅>0 且 收盘>开盘）
    if p_change <= 0 or close <= open_:
        return False

    return True
