#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
涨停原因数据（已重构为Tushare API，彻底移除同花顺爬虫）
Tushare接口: kpl_list（需要一定积分）
"""
import logging
import pandas as pd
import numpy as np

__author__ = 'myh '
__date__ = '2025/12/31 '


def stock_limitup_reason(date: str = "2025-02-27") -> pd.DataFrame:
    """
    涨停原因（Tushare kpl_list 接口）
    :param date: 交易日 YYYY-MM-DD 格式
    :return: 涨停原因 DataFrame
    列: 日期, 代码, 名称, 原因, 详因, 最新价, 涨跌幅, 涨跌额, 换手率, 成交量, 成交额, DDE
    """
    try:
        from instock.core.crawling.tushare_data import tushare_data
        if not tushare_data.is_available():
            return pd.DataFrame()

        trade_dt = date.replace('-', '')
        df = tushare_data.pro.kpl_list(trade_date=trade_dt, tag='涨停')
        if df is None or df.empty:
            return pd.DataFrame()

        def _extract_symbol(ts_code):
            return ts_code.split('.')[0] if '.' in str(ts_code) else str(ts_code)

        def _safe(col, default=0):
            if col in df.columns:
                return pd.to_numeric(df[col], errors='coerce').fillna(default)
            return pd.Series([default] * len(df), index=df.index)

        # 原因优先用 theme，详因用 lu_desc
        reason = df['theme'] if 'theme' in df.columns else pd.Series([''] * len(df), index=df.index)
        detail = df['lu_desc'] if 'lu_desc' in df.columns else pd.Series([''] * len(df), index=df.index)

        result = pd.DataFrame({
            '日期':   pd.to_datetime(df['trade_date']).dt.date,
            '代码':   df['ts_code'].apply(_extract_symbol),
            '名称':   df['name'] if 'name' in df.columns else '',
            '原因':   reason,
            '详因':   detail,
            '最新价': _safe('pct_chg', 0) * 0,   # kpl_list 无单独收盘价字段，填0
            '涨跌幅': _safe('pct_chg'),
            '涨跌额': np.nan,
            '换手率': _safe('turnover_rate'),
            '成交量': np.nan,
            '成交额': _safe('amount'),
            'DDE':   np.nan,
        })
        return result
    except Exception as e:
        logging.error(f"stock_limitup_reason处理异常: {e}")
        return pd.DataFrame()


if __name__ == "__main__":
    df = stock_limitup_reason(date="2026-06-16")
    print(df)
