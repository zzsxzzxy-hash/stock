#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
资金流向数据（已重构为Tushare API）
Tushare接口: moneyflow
"""
import logging
import pandas as pd
from instock.core.crawling.tushare_data import tushare_data

__author__ = 'myh '
__date__ = '2025/12/31 '


def stock_individual_fund_flow_rank(indicator: str = "5日") -> pd.DataFrame:
    """
    个股资金流向排名（Tushare API）
    :param indicator: choice of {"今日", "3日", "5日", "10日"}
    :type indicator: str
    :return: 指定 indicator 资金流向排行
    :rtype: pandas.DataFrame
    """
    try:
        return tushare_data.get_individual_fund_flow_rank(indicator=indicator)
    except Exception as e:
        logging.error(f"stock_individual_fund_flow_rank处理异常: {e}")
        return pd.DataFrame()


def stock_sector_fund_flow_rank(indicator: str = "10日", sector_type: str = "行业资金流") -> pd.DataFrame:
    """
    板块资金流向排名（Tushare API，通过行业分类聚合个股资金流）
    :param indicator: choice of {"今日", "5日", "10日"}
    :type indicator: str
    :param sector_type: choice of {"行业资金流", "概念资金流", "地域资金流"}
    :type sector_type: str
    :return: 指定参数的资金流排名数据
    :rtype: pandas.DataFrame
    """
    try:
        return tushare_data.get_sector_fund_flow_rank(indicator=indicator, sector_type=sector_type)
    except Exception as e:
        logging.error(f"stock_sector_fund_flow_rank处理异常: {e}")
        return pd.DataFrame()


if __name__ == "__main__":
    stock_individual_fund_flow_rank_df = stock_individual_fund_flow_rank(indicator="今日")
    print(stock_individual_fund_flow_rank_df)

    stock_sector_fund_flow_rank_df = stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
    print(stock_sector_fund_flow_rank_df)
