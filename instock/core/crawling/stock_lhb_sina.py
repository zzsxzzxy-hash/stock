#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
龙虎榜数据（已重构为Tushare API，彻底移除新浪财经爬虫）
Tushare接口: top_list
"""
import logging
import pandas as pd
from instock.core.crawling.tushare_data import tushare_data

__author__ = 'myh '
__date__ = '2025/12/31 '


def stock_lhb_detail_daily_sina(date: str = "20240222") -> pd.DataFrame:
    """
    龙虎榜每日详情（Tushare API）
    :param date: 交易日 YYYYMMDD 格式
    :return: 龙虎榜每日详情
    """
    try:
        d = "-".join([date[:4], date[4:6], date[6:]])
        return tushare_data.get_lhb_detail(d, d)
    except Exception as e:
        logging.error(f"stock_lhb_detail_daily_sina处理异常: {e}")
        return pd.DataFrame()


def stock_lhb_ggtj_sina(symbol: str = "5") -> pd.DataFrame:
    """
    龙虎榜个股上榜统计（Tushare API，替代新浪近N天聚合）
    :param symbol: choice of {'5','10','30','60'} 最近N天
    :return: 个股上榜统计
    """
    try:
        import datetime
        days = int(symbol)
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        return tushare_data.get_lhb_ggtj(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    except Exception as e:
        logging.error(f"stock_lhb_ggtj_sina处理异常: {e}")
        return pd.DataFrame()


def stock_lhb_yytj_sina(symbol: str = "5") -> pd.DataFrame:
    """
    龙虎榜营业部上榜统计（Tushare无对应接口，返回空DataFrame）
    :param symbol: choice of {'5','10','30','60'}
    """
    logging.warning("stock_lhb_yytj_sina: Tushare无营业部上榜统计接口，返回空DataFrame")
    return pd.DataFrame()


def stock_lhb_jgzz_sina(symbol: str = "5") -> pd.DataFrame:
    """
    龙虎榜机构席位追踪（Tushare无对应接口，返回空DataFrame）
    :param symbol: choice of {'5','10','30','60'}
    """
    logging.warning("stock_lhb_jgzz_sina: Tushare无机构席位追踪接口，返回空DataFrame")
    return pd.DataFrame()


def stock_lhb_jgmx_sina() -> pd.DataFrame:
    """
    龙虎榜机构席位成交明细（Tushare无对应接口，返回空DataFrame）
    """
    logging.warning("stock_lhb_jgmx_sina: Tushare无机构席位成交明细接口，返回空DataFrame")
    return pd.DataFrame()


if __name__ == "__main__":
    df = stock_lhb_detail_daily_sina(date="20250101")
    print(df)

    df2 = stock_lhb_ggtj_sina(symbol="5")
    print(df2)
