# ML预测系统

独立的机器学习预测系统，用于预测隔夜股票收益。

## 目标
- 预测隔夜胜率≥80%的股票
- 只做科创板、北交所、创业板（20%涨跌幅）
- 提供可视化界面展示推荐原因

## 系统架构

```
ml_predict/
├── data/                   # 数据处理
│   ├── build_dataset.py    # 构建训练数据集
│   ├── feature_engineering.py  # 特征工程
│   └── dataset_cache/      # 缓存的数据集
├── models/                 # 模型训练
│   ├── train.py            # 训练脚本
│   ├── evaluate.py         # 回测评估
│   ├── saved_models/       # 保存的模型
│   └── config.py           # 配置
├── predict/                # 预测
│   ├── daily_predict.py    # 每日预测
│   └── prediction_api.py   # API服务
├── web/                    # 可视化界面
│   ├── app.py              # Flask应用
│   ├── static/             # 静态文件
│   └── templates/          # 前端模板
└── notebooks/              # 数据探索
```

## 数据依赖

共享现有系统的数据表：
- `cn_stock_minute_bar` - 分钟K线
- `cn_stock_spot` - 日线数据
- `cn_stock_trade_theme` - 主线映射

## 安装

```bash
cd ml_predict
pip install -r requirements.txt
```

## 使用

### 1. 构建训练数据集
```bash
python data/build_dataset.py
```

### 2. 训练模型
```bash
python models/train.py
```

### 3. 回测评估
```bash
python models/evaluate.py
```

### 4. 启动可视化界面
```bash
python web/app.py
```
访问 http://localhost:5000

## 开发状态

- [ ] 数据集构建
- [ ] 特征工程
- [ ] 模型训练
- [ ] 回测评估
- [ ] 可视化界面
- [ ] 部署上线
