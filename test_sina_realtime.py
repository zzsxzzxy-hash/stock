#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试新浪财经实时行情接口
http://hq.sinajs.cn/list=s_sh688802,s_sz300750
"""
import requests

url = "http://hq.sinajs.cn/list=s_sh688802,s_sz300750"

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

resp = requests.get(url, headers=headers, timeout=10)
resp.encoding = "gbk"

print("HTTP状态码:", resp.status_code)
print("原始返回:")
print(resp.text)
print()

# 解析返回值
# 格式: var hq_str_s_sh688802="股票名称,当前价,涨跌额,涨跌幅,成交量(手),成交额(万元)";
for line in resp.text.strip().splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        # 提取变量名和数据
        var_part, data_part = line.split('=', 1)
        code_key = var_part.replace('var hq_str_', '').strip()
        data = data_part.strip().strip('";').strip('"')
        fields = data.split(',')
        print(f"代码标识: {code_key}")
        if len(fields) >= 6:
            print(f"  股票名称: {fields[0]}")
            print(f"  当前价格: {fields[1]}")
            print(f"  涨跌额:   {fields[2]}")
            print(f"  涨跌幅:   {fields[3]}%")
            print(f"  成交量:   {fields[4]} 手")
            print(f"  成交额:   {fields[5]} 万元")
        else:
            print(f"  原始字段: {fields}")
    except Exception as e:
        print(f"解析失败: {e} | 原始: {line}")
    print()
