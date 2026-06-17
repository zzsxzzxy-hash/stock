#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
交易日历（已重构为Tushare API，彻底移除新浪财经爬虫）
Tushare接口: trade_cal
"""
import datetime
import logging
import pandas as pd

__author__ = 'myh '
__date__ = '2025/12/31 '


def tool_trade_date_hist_sina() -> pd.DataFrame:
    """
    交易日历（Tushare trade_cal 接口，替代新浪财经JS爬虫）
    :return: DataFrame，含 trade_date 列（datetime.date），仅交易日
    :rtype: pandas.DataFrame
    """
    try:
        from instock.core.crawling.tushare_data import tushare_data
        if not tushare_data.is_available():
            return pd.DataFrame()

        end_date = (datetime.datetime.now() + datetime.timedelta(days=400)).strftime('%Y%m%d')
        df = tushare_data.pro.trade_cal(
            exchange='SSE',
            start_date='19901219',
            end_date=end_date,
            is_open='1'
        )
        if df is None or df.empty:
            return pd.DataFrame()

        result = pd.DataFrame({
            'trade_date': pd.to_datetime(df['cal_date']).dt.date
        })
        result = result.sort_values('trade_date').reset_index(drop=True)
        return result
    except Exception as e:
        logging.error(f"tool_trade_date_hist_sina处理异常: {e}")
        return pd.DataFrame()


if __name__ == "__main__":
    df = tool_trade_date_hist_sina()
    print(df)
    print(f"共 {len(df)} 个交易日")
