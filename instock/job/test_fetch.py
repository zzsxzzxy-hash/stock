#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from instock.core.crawling.tushare_data import tushare_data
from instock.core.stockfetch import stock_hist_cache, fetch_stock_hist
from instock.lib.database import insert_db_from_df
from instock.core import tablestructure as tbs

def main():
    print('=' * 60)
    print('抓取2026年1月1日至2026年6月10日数据')
    print('=' * 60)
    
    # 测试股票列表
    test_stocks = ['000001', '000002', '600000']
    test_dates = ['20260101', '20260610']
    
    print(f'\n测试股票: {test_stocks}')
    print(f'测试日期: {test_dates}')
    
    # 获取表结构
    cols_type = tbs.get_field_types(tbs.CN_STOCK_HIST_DATA['columns'])
    
    # 抓取数据
    total_records = 0
    
    for code in test_stocks:
        print(f'\n处理股票: {code}')
        try:
            data = stock_hist_cache(code, test_dates[0], test_dates[-1], is_cache=False)
            if data is not None and len(data) > 0:
                print(f'  获取 {len(data)} 条数据')
                print(data)
                
                # 添加code字段
                data['code'] = code
                
                # 入库
                try:
                    insert_db_from_df(data, 'cn_stock_hist_data', cols_type, False, "`date`,`code`")
                    print(f'  成功入库')
                    total_records += len(data)
                except Exception as e:
                    print(f'  入库失败: {e}')
            else:
                print(f'  无数据')
        except Exception as e:
            print(f'  获取数据失败: {e}')
    
    print(f'\n总计成功获取 {total_records} 条数据')
    print('=' * 60)

if __name__ == '__main__':
    main()
