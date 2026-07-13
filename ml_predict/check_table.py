#!/usr/bin/env python3
import sys
from pathlib import Path

# 添加项目根目录到路径
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import instock.lib.database as mdb

# 查询 cn_stock_spot 表结构
result = mdb.executeSqlFetch("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'cn_stock_spot'
    ORDER BY ordinal_position
""")

print("cn_stock_spot 表字段:")
for r in result:
    print(f"  {r[0]:<30} {r[1]}")
