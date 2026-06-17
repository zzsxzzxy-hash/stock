# -*- coding:utf-8 -*-
# !/usr/bin/env python
"""
龙虎榜数据（已重构为Tushare API）
Tushare接口: top_list
"""
import logging
import pandas as pd
from instock.core.crawling.tushare_data import tushare_data

__author__ = 'myh '
__date__ = '2025/12/31 '


def stock_lhb_detail_em(start_date: str = "20230403", end_date: str = "20230417") -> pd.DataFrame:
    """
    龙虎榜详情（Tushare API）
    :param start_date: 开始日期 YYYYMMDD格式
    :type start_date: str
    :param end_date: 结束日期 YYYYMMDD格式
    :type end_date: str
    :return: 龙虎榜详情
    :rtype: pandas.DataFrame
    """
    try:
        sd = "-".join([start_date[:4], start_date[4:6], start_date[6:]])
        ed = "-".join([end_date[:4], end_date[4:6], end_date[6:]])
        return tushare_data.get_lhb_detail(sd, ed)
    except Exception as e:
        logging.error(f"stock_lhb_detail_em处理异常: {e}")
        return pd.DataFrame()


def stock_lhb_stock_statistic_em(symbol: str = "近一月") -> pd.DataFrame:
    """
    个股上榜统计（Tushare无直接对应接口，返回空DataFrame）
    :param symbol: choice of {"近一月", "近三月", "近六月", "近一年"}
    :type symbol: str
    :return: 个股上榜统计
    :rtype: pandas.DataFrame
    """
    logging.warning("stock_lhb_stock_statistic_em: Tushare无直接对应接口，返回空DataFrame")
    return pd.DataFrame()


def stock_lhb_jgmmtj_em(start_date: str = "20220906", end_date: str = "20220906") -> pd.DataFrame:
    """
    机构买卖每日统计（Tushare API）
    :param start_date: 开始日期 YYYYMMDD格式
    :type start_date: str
    :param end_date: 结束日期 YYYYMMDD格式
    :type end_date: str
    :return: 机构买卖每日统计
    :rtype: pandas.DataFrame
    """
    try:
        sd = "-".join([start_date[:4], start_date[4:6], start_date[6:]])
        ed = "-".join([end_date[:4], end_date[4:6], end_date[6:]])
        return tushare_data.get_lhb_jgmmtj(sd, ed)
    except Exception as e:
        logging.error(f"stock_lhb_jgmmtj_em处理异常: {e}")
        return pd.DataFrame()


def stock_lhb_jgstatistic_em(symbol: str = "近一月") -> pd.DataFrame:
    """
    机构席位追踪（Tushare无直接对应接口，返回空DataFrame）
    :param symbol: choice of {"近一月", "近三月", "近六月", "近一年"}
    :type symbol: str
    :return: 机构席位追踪
    :rtype: pandas.DataFrame
    """
    logging.warning("stock_lhb_jgstatistic_em: Tushare无直接对应接口，返回空DataFrame")
    return pd.DataFrame()


def stock_lhb_hyyyb_em(start_date: str = "20220324", end_date: str = "20220324") -> pd.DataFrame:
    """
    每日活跃营业部（Tushare无直接对应接口，返回空DataFrame）
    :param start_date: 开始日期 YYYYMMDD格式
    :type start_date: str
    :param end_date: 结束日期 YYYYMMDD格式
    :type end_date: str
    :return: 每日活跃营业部
    :rtype: pandas.DataFrame
    """
    logging.warning("stock_lhb_hyyyb_em: Tushare无直接对应接口，返回空DataFrame")
    return pd.DataFrame()


def stock_lhb_yybph_em(symbol: str = "近一月") -> pd.DataFrame:
    """
    营业部排行（Tushare无直接对应接口，返回空DataFrame）
    :param symbol: choice of {"近一月", "近三月", "近六月", "近一年"}
    :type symbol: str
    :return: 营业部排行
    :rtype: pandas.DataFrame
    """
    logging.warning("stock_lhb_yybph_em: Tushare无直接对应接口，返回空DataFrame")
    return pd.DataFrame()


if __name__ == "__main__":
    stock_lhb_detail_em_df = stock_lhb_detail_em(start_date="20250101", end_date="20250110")
    print(stock_lhb_detail_em_df)

    stock_lhb_jgmmtj_em_df = stock_lhb_jgmmtj_em(start_date="20250101", end_date="20250110")
    print(stock_lhb_jgmmtj_em_df)
