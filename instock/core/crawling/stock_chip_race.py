#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
竞价抢筹数据（通达信接口已移除，Tushare无对应接口，返回空DataFrame）
原通达信接口：http://excalc.icfqs.com:7616/TQLEX?Entry=HQServ.hq_nlp
"""
import logging
import pandas as pd

__author__ = 'myh '
__date__ = '2025/12/31 '


def stock_chip_race_open(date: str = "") -> pd.DataFrame:
    """
    早盘竞价抢筹（通达信接口已移除，返回空DataFrame）
    :param date: 日期字符串（不使用）
    :return: 空DataFrame
    """
    logging.warning("stock_chip_race_open: 通达信爬虫已移除，Tushare无对应接口，返回空DataFrame")
    return pd.DataFrame()


def stock_chip_race_end(date: str = "") -> pd.DataFrame:
    """
    尾盘竞价抢筹（通达信接口已移除，返回空DataFrame）
    :param date: 日期字符串（不使用）
    :return: 空DataFrame
    """
    logging.warning("stock_chip_race_end: 通达信爬虫已移除，Tushare无对应接口，返回空DataFrame")
    return pd.DataFrame()


if __name__ == "__main__":
    print(stock_chip_race_open())
    print(stock_chip_race_end())
