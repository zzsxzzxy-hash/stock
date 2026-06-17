# -*- coding:utf-8 -*-
# !/usr/bin/env python
"""
选股器数据（已重构为Tushare API）
"""
import pandas as pd
import instock.core.tablestructure as tbs
from instock.core.crawling.tushare_data import tushare_data

__author__ = 'myh '
__date__ = '2025/12/31 '


def stock_selection() -> pd.DataFrame:
    """
    综合选股（Tushare API）
    :return: 选股器
    :rtype: pandas.DataFrame
    """
    return tushare_data.get_stock_selection()


def stock_selection_params():
    """
    选股器-选股指标（Tushare API）
    Tushare无直接对应接口，返回空DataFrame
    :return: 选股器-选股指标
    :rtype: pandas.DataFrame
    """
    return pd.DataFrame()


if __name__ == "__main__":
    stock_selection_df = stock_selection()
    print(stock_selection_df)
