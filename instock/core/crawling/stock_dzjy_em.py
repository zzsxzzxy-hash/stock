#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
大宗交易数据（已重构为Tushare API）
Tushare接口: block_trade
"""
import logging
import pandas as pd
from instock.core.crawling.tushare_data import tushare_data

__author__ = 'myh '
__date__ = '2025/12/31 '


def stock_dzjy_sctj() -> pd.DataFrame:
    """
    大宗交易市场统计（Tushare无直接对应接口，返回空DataFrame）
    :return: 市场统计表
    :rtype: pandas.DataFrame
    """
    logging.warning("stock_dzjy_sctj: Tushare无直接对应接口，返回空DataFrame")
    return pd.DataFrame()


def stock_dzjy_mrmx(symbol: str = '基金', start_date: str = '20220104', end_date: str = '20220104') -> pd.DataFrame:
    """
    大宗交易每日明细（Tushare API）
    :param symbol: choice of {'A股', 'B股', '基金', '债券'}（Tushare不区分，统一返回A股大宗交易）
    :type symbol: str
    :param start_date: 开始日期 YYYYMMDD格式
    :type start_date: str
    :param end_date: 结束日期 YYYYMMDD格式
    :type end_date: str
    :return: 每日明细
    :rtype: pandas.DataFrame
    """
    try:
        sd = "-".join([start_date[:4], start_date[4:6], start_date[6:]])
        ed = "-".join([end_date[:4], end_date[4:6], end_date[6:]])
        return tushare_data.get_block_trade(sd, ed)
    except Exception as e:
        logging.error(f"stock_dzjy_mrmx处理异常: {e}")
        return pd.DataFrame()


def stock_dzjy_mrtj(start_date: str = '20220105', end_date: str = '20220105') -> pd.DataFrame:
    """
    大宗交易每日统计（Tushare API）
    :param start_date: 开始日期 YYYYMMDD格式
    :type start_date: str
    :param end_date: 结束日期 YYYYMMDD格式
    :type end_date: str
    :return: 每日统计
    :rtype: pandas.DataFrame
    """
    try:
        sd = "-".join([start_date[:4], start_date[4:6], start_date[6:]])
        ed = "-".join([end_date[:4], end_date[4:6], end_date[6:]])
        return tushare_data.get_block_trade_daily_stat(sd, ed)
    except Exception as e:
        logging.error(f"stock_dzjy_mrtj处理异常: {e}")
        return pd.DataFrame()


def stock_dzjy_hygtj(symbol: str = '近三月') -> pd.DataFrame:
    """
    活跃A股统计（Tushare无直接对应接口，返回空DataFrame）
    :param symbol: choice of {'近一月', '近三月', '近六月', '近一年'}
    :type symbol: str
    :return: 活跃A股统计
    :rtype: pandas.DataFrame
    """
    logging.warning("stock_dzjy_hygtj: Tushare无直接对应接口，返回空DataFrame")
    return pd.DataFrame()


def stock_dzjy_hyyybtj(symbol: str = '近3日') -> pd.DataFrame:
    """
    活跃营业部统计（Tushare无直接对应接口，返回空DataFrame）
    :param symbol: choice of {'当前交易日', '近3日', '近5日', '近10日', '近30日'}
    :type symbol: str
    :return: 活跃营业部统计
    :rtype: pandas.DataFrame
    """
    logging.warning("stock_dzjy_hyyybtj: Tushare无直接对应接口，返回空DataFrame")
    return pd.DataFrame()


def stock_dzjy_yybph(symbol: str = '近三月') -> pd.DataFrame:
    """
    营业部排行（Tushare无直接对应接口，返回空DataFrame）
    :param symbol: choice of {'近一月', '近三月', '近六月', '近一年'}
    :type symbol: str
    :return: 营业部排行
    :rtype: pandas.DataFrame
    """
    logging.warning("stock_dzjy_yybph: Tushare无直接对应接口，返回空DataFrame")
    return pd.DataFrame()


if __name__ == "__main__":
    stock_dzjy_mrmx_df = stock_dzjy_mrmx(symbol='A股', start_date='20250101', end_date='20250110')
    print(stock_dzjy_mrmx_df)

    stock_dzjy_mrtj_df = stock_dzjy_mrtj(start_date='20250101', end_date='20250110')
    print(stock_dzjy_mrtj_df)
