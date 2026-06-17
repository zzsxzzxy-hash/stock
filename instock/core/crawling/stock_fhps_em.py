#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
分红送配数据（已重构为Tushare API）
Tushare接口: dividend
"""
import logging
import pandas as pd
from instock.core.crawling.tushare_data import tushare_data

__author__ = 'myh '
__date__ = '2025/12/31 '


def stock_fhps_em(date: str = "20231231") -> pd.DataFrame:
    """
    分红送配（Tushare API）
    :param date: 分红送配报告期 YYYYMMDD格式
    :type date: str
    :return: 分红送配
    :rtype: pandas.DataFrame
    """
    try:
        return tushare_data.get_dividend(report_date=date)
    except Exception as e:
        logging.error(f"stock_fhps_em处理异常: {e}")
        return pd.DataFrame()


if __name__ == "__main__":
    stock_fhps_em_df = stock_fhps_em(date="20231231")
    print(stock_fhps_em_df)
