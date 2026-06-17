#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import datetime
from datetime import timedelta

# 添加项目路径
project_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, project_path)

from instock.core.stockfetch import fetch_stocks, fetch_stock_hist
from instock.core.crawling.tushare_data import tushare_data
from instock.lib.database import executeSql, insert_db_from_df, db_database

def get_trading_dates(start_date, end_date):
    """获取交易日期列表"""
    # 由于Tushare的trade_cal接口有频率限制，直接生成日期范围
    from datetime import datetime, timedelta
    
    start_dt = datetime.strptime(start_date, '%Y%m%d')
    end_dt = datetime.strptime(end_date, '%Y%m%d')
    
    dates = []
    current_dt = start_dt
    
    while current_dt <= end_dt:
        # 跳过周末
        if current_dt.weekday() < 5:
            dates.append(current_dt.strftime('%Y%m%d'))
        current_dt += timedelta(days=1)
    
    return dates

def fetch_and_save_data(start_date, end_date):
    """抓取并保存数据"""
    print(f"开始抓取 {start_date} 至 {end_date} 的数据...")
    
    # 获取交易日期
    trade_dates = get_trading_dates(start_date, end_date)
    print(f"共有 {len(trade_dates)} 个交易日")
    
    if not trade_dates:
        print("没有交易日期")
        return
    
    # 获取股票列表
    stock_list = tushare_data.get_stock_list()
    if stock_list is None or stock_list.empty:
        print("获取股票列表失败")
        return
    
    print(f"共有 {len(stock_list)} 只股票")
    
    # 分批处理日期
    batch_size = 10
    total_batches = (len(trade_dates) + batch_size - 1) // batch_size
    
    for batch_idx in range(total_batches):
        start_idx = batch_idx * batch_size
        end_idx = min((batch_idx + 1) * batch_size, len(trade_dates))
        batch_dates = trade_dates[start_idx:end_idx]
        
        print(f"\n处理批次 {batch_idx + 1}/{total_batches}: {batch_dates[0]} - {batch_dates[-1]}")
        
        for date_str in batch_dates:
            print(f"\n处理日期: {date_str}")
            
            # 转换日期格式
            date = datetime.datetime.strptime(date_str, '%Y%m%d')
            
            # 获取当日行情
            try:
                spot_data = fetch_stocks(date)
                if spot_data is not None and len(spot_data) > 0:
                    print(f"  获取当日行情: {len(spot_data)} 条")
                    # 入库
                    insert_db_from_df('cn_stock_spot', spot_data)
                else:
                    print(f"  获取当日行情失败")
            except Exception as e:
                print(f"  获取当日行情异常: {e}")
            
            # 获取历史K线
            try:
                stock_codes = stock_list['交易代码'].tolist()[:50]  # 测试用，只取50只
                for code in stock_codes:
                    try:
                        hist_data = fetch_stock_hist((date, code))
                        if hist_data is not None and len(hist_data) > 0:
                            insert_db_from_df('cn_stock_hist_data', hist_data)
                    except Exception as e:
                        print(f"    获取股票 {code} K线数据失败: {e}")
                print(f"  处理 {len(stock_codes)} 只股票K线数据")
            except Exception as e:
                print(f"  获取历史K线异常: {e}")
    
    print("\n数据抓取完成!")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("使用方法: python3 fetch_historical_data.py <开始日期> <结束日期>")
        print("日期格式: YYYY-MM-DD")
        sys.exit(1)
    
    start_date_str = sys.argv[1]
    end_date_str = sys.argv[2]
    
    # 转换日期格式
    try:
        start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').strftime('%Y%m%d')
        end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').strftime('%Y%m%d')
    except ValueError:
        print("日期格式错误，请使用 YYYY-MM-DD")
        sys.exit(1)
    
    fetch_and_save_data(start_date, end_date)
