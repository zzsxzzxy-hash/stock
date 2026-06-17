#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
ETF行情数据（已重构为Tushare API）
Tushare接口: fund_basic + fund_daily
"""
import logging
import pandas as pd
from instock.core.crawling.tushare_data import tushare_data

__author__ = 'myh '
__date__ = '2025/12/31 '


def fund_etf_spot_em() -> pd.DataFrame:
    """
    ETF实时行情（Tushare API）
    :return: ETF 实时行情
    :rtype: pandas.DataFrame
    """
    try:
        return tushare_data.get_etf_spot()
    except Exception as e:
        logging.error(f"fund_etf_spot_em处理异常: {e}")
        return pd.DataFrame()


def fund_etf_hist_em(
    symbol: str = "159707",
    period: str = "daily",
    start_date: str = "19700101",
    end_date: str = "20500101",
    adjust: str = "",
) -> pd.DataFrame:
    """
    ETF历史行情（Tushare API）
    :param symbol: ETF 代码
    :type symbol: str
    :param period: choice of {'daily', 'weekly', 'monthly'}
    :type period: str
    :param start_date: 开始日期
    :type start_date: str
    :param end_date: 结束日期
    :type end_date: str
    :param adjust: choice of {"qfq": "前复权", "hfq": "后复权", "": "不复权"}
    :type adjust: str
    :return: 每日行情
    :rtype: pandas.DataFrame
    """
    try:
        adj = adjust if adjust else "qfq"
        return tushare_data.get_etf_hist(symbol=symbol, period=period,
                                         start_date=start_date, end_date=end_date, adjust=adj)
    except Exception as e:
        logging.error(f"fund_etf_hist_em处理异常: {e}")
        return pd.DataFrame()


def fund_etf_hist_min_em(
    symbol: str = "159707",
    start_date: str = "1979-09-01 09:32:00",
    end_date: str = "2222-01-01 09:32:00",
    period: str = "5",
    adjust: str = "",
) -> pd.DataFrame:
    """
    ETF分时行情（Tushare无对应接口，返回空DataFrame）
    :param symbol: ETF 代码
    :type symbol: str
    :param start_date: 开始日期
    :type start_date: str
    :param end_date: 结束日期
    :type end_date: str
    :param period: choice of {'1', '5', '15', '30', '60'}
    :type period: str
    :param adjust: choice of {'', 'qfq', 'hfq'}
    :type adjust: str
    :return: 每日分时行情
    :rtype: pandas.DataFrame
    """
    logging.warning("fund_etf_hist_min_em: Tushare无分时行情接口，返回空DataFrame")
    return pd.DataFrame()


if __name__ == "__main__":
    fund_etf_spot_em_df = fund_etf_spot_em()
    print(fund_etf_spot_em_df)

    fund_etf_hist_em_df = fund_etf_hist_em(
        symbol="513500",
        period="daily",
        start_date="20250101",
        end_date="20250201",
        adjust="qfq",
    )
    print(fund_etf_hist_em_df)
