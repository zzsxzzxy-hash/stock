#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练实验脚本（修复数据泄露版本）
- 移除泄露特征
- 改为时间序列分割
"""

import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score, confusion_matrix
import xgboost as xgb
import json


class ExperimentRunner:
    """实验运行器（修复版）"""

    def __init__(self, data_path):
        self.data_path = data_path
        self.df = None
        self.results = []

        # 定义需要移除的泄露特征
        self.leakage_features = [
            'change_rate',           # 全天涨跌幅
            'amplitude',             # 全天振幅
            'deal_amount',           # 全天成交额
            'close_price',           # 收盘价
            'turnoverrate',          # 全天换手率
            'daily_volume_ratio',    # 全天量比
            'intraday_return',       # 标签计算字段
            'overnight_return',      # 标签计算字段
            'total_return',          # 标签计算字段
            'theme_avg_change',      # 板块当日平均涨幅（包含收盘）
            'theme_median_change',   # 板块当日中位数涨幅（包含收盘）
            'theme_avg_volume_ratio',# 板块平均量比（包含全天）
            'price_change_delta',    # 当日涨幅 - 前日涨幅（包含收盘）
        ]

    def load_data(self):
        """加载数据集"""
        print('='*80)
        print('加载数据集（修复版）')
        print('='*80)

        self.df = pd.read_parquet(self.data_path)
        print(f'✓ 原始数据: {len(self.df)} 条, {len(self.df.columns)} 个特征')

        # 移除泄露特征
        removed = [f for f in self.leakage_features if f in self.df.columns]
        self.df = self.df.drop(columns=removed, errors='ignore')

        print(f'✓ 移除泄露特征: {len(removed)} 个')
        print(f'  {", ".join(removed[:5])}...')
        print(f'✓ 剩余特征: {len(self.df.columns)} 个')
        print(f'✓ 当日胜率: {(self.df["label_intraday"] == 1).sum() / len(self.df) * 100:.1f}%')
        print()

    def define_feature_sets(self):
        """定义特征集（只保留安全特征）"""
        # 基础特征（移除泄露后）
        base_features = [
            'snapshot_volume_ratio',           # 快照量比（09:30-09:55）
            'snapshot_vs_daily_volume',        # 快照与全天量比对比（需检查）
            'prev_1d_change_rate',             # 前1日涨跌幅
            'prev_3d_return',                  # 前3日涨跌幅
            'prev_5d_return',                  # 前5日涨跌幅
            'prev_1d_volume_ratio',            # 前1日量比
            'prev_3d_volume_ratio',            # 前3日量比
            'prev_5d_volume_ratio',            # 前5日量比
            'prev_1d_turnover',                # 前1日换手率
            'prev_3d_turnover',                # 前3日换手率
            'prev_5d_turnover',                # 前5日换手率
            'volume_change_ratio',             # 量比变化率
            'shrink_volume_rise',              # 缩量上涨特征
            'has_theme',                       # 是否有主线
            'theme_confidence',                # 主线置信度
        ]

        # 早盘特征（09:35-09:55）
        early_features = [
            'max_change_0935_0955',            # 最高涨幅
            'min_change_0935_0955',            # 最低涨幅
            'final_change_0955',               # 最终涨幅
            'open_change_0935',                # 开盘涨幅
            'pullback_ratio',                  # 回撤比例
            'pullback_abs',                    # 回撤绝对值
            'max_slope',                       # 最大斜率
            'avg_slope',                       # 平均斜率
            'slope_std',                       # 斜率标准差
            'red_minutes_ratio',               # 红盘分钟占比
        ]

        # 市场情绪特征
        market_features = [
            'red_ratio',                       # 市场红盘占比
            'limit_up_ratio',                  # 涨停占比
            'limit_down_ratio',                # 跌停占比
            'total_amount_billions',           # 市场总成交额
        ]

        # 板块特征（只保留股票数量）
        sector_features = [
            'theme_stock_count',               # 主线股票数量
        ]

        # 市场类型
        market_type = ['market']

        feature_sets = {
            'base': base_features + market_type,
            'base+early': base_features + early_features + market_type,
            'base+market': base_features + market_features + market_type,
            'all': base_features + early_features + market_features + sector_features + market_type,
        }

        return feature_sets

    def prepare_features(self, feature_list):
        """准备特征"""
        available_features = [f for f in feature_list if f in self.df.columns]

        X = self.df[available_features].copy()
        y = self.df['label_intraday'].copy()

        # 处理市场类型
        if 'market' in X.columns:
            market_dummies = pd.get_dummies(X['market'], prefix='market')
            X = pd.concat([X.drop('market', axis=1), market_dummies], axis=1)

        # 填充缺失值并替换无穷大
        X = X.fillna(0)
        X = X.replace([np.inf, -np.inf], 0)

        return X, y, list(X.columns)

    def split_data_by_time(self, X, y):
        """
        按时间序列分割数据
        训练集：60%（前12天）
        验证集：20%（中4天）
        测试集：20%（后5天）
        """
        if 'date' not in self.df.columns:
            raise ValueError("数据集中没有 date 字段")

        dates = sorted(self.df['date'].unique())
        n_dates = len(dates)

        train_end = int(n_dates * 0.6)
        val_end = int(n_dates * 0.8)

        train_dates = dates[:train_end]
        val_dates = dates[train_end:val_end]
        test_dates = dates[val_end:]

        # 获取索引
        train_idx = self.df['date'].isin(train_dates)
        val_idx = self.df['date'].isin(val_dates)
        test_idx = self.df['date'].isin(test_dates)

        X_train = X[train_idx].reset_index(drop=True)
        y_train = y[train_idx].reset_index(drop=True)

        X_val = X[val_idx].reset_index(drop=True)
        y_val = y[val_idx].reset_index(drop=True)

        X_test = X[test_idx].reset_index(drop=True)
        y_test = y[test_idx].reset_index(drop=True)

        print(f'  训练集: {train_dates[0]} ~ {train_dates[-1]} ({len(X_train)} 条, {len(X_train)/len(X)*100:.1f}%)')
        print(f'  验证集: {val_dates[0]} ~ {val_dates[-1]} ({len(X_val)} 条, {len(X_val)/len(X)*100:.1f}%)')
        print(f'  测试集: {test_dates[0]} ~ {test_dates[-1]} ({len(X_test)} 条, {len(X_test)/len(X)*100:.1f}%)')

        return X_train, X_val, X_test, y_train, y_val, y_test

    def train_model(self, X_train, y_train, X_val, y_val, params):
        """训练 XGBoost 模型"""
        scale_pos_weight = (y_train == 0).sum() / (y_train == 1).sum()

        model_params = {
            'objective': 'binary:logistic',
            'eval_metric': 'logloss',
            'scale_pos_weight': scale_pos_weight,
            'random_state': 42,
            **params
        }

        dtrain = xgb.DMatrix(X_train, label=y_train)
        dval = xgb.DMatrix(X_val, label=y_val)

        evals = [(dtrain, 'train'), (dval, 'val')]

        model = xgb.train(
            model_params,
            dtrain,
            num_boost_round=500,
            evals=evals,
            early_stopping_rounds=20,
            verbose_eval=False
        )

        return model, model.best_iteration

    def evaluate_model(self, model, X, y, threshold=0.5):
        """评估模型"""
        dtest = xgb.DMatrix(X)
        y_prob = model.predict(dtest)
        y_pred = (y_prob >= threshold).astype(int)

        metrics = {
            'accuracy': accuracy_score(y, y_pred),
            'precision': precision_score(y, y_pred, zero_division=0),
            'recall': recall_score(y, y_pred, zero_division=0),
            'f1': f1_score(y, y_pred, zero_division=0),
            'auc': roc_auc_score(y, y_prob),
        }

        tn, fp, fn, tp = confusion_matrix(y, y_pred).ravel()
        metrics['confusion_matrix'] = {'tn': int(tn), 'fp': int(fp), 'fn': int(fn), 'tp': int(tp)}

        return metrics, y_prob

    def run_experiment(self, exp_name, feature_set, model_params, description=""):
        """运行单个实验"""
        print(f'\n{"="*80}')
        print(f'实验: {exp_name}')
        print(f'描述: {description}')
        print(f'{"="*80}')

        start_time = datetime.now()

        # 准备特征
        print('准备特征...')
        X, y, final_features = self.prepare_features(feature_set)
        print(f'  ✓ 特征数: {len(final_features)}')
        print(f'  ✓ 样本数: {len(X)}')

        # 按时间分割数据
        print('按时间序列分割数据...')
        X_train, X_val, X_test, y_train, y_val, y_test = self.split_data_by_time(X, y)

        # 训练模型
        print('训练模型...')
        model, best_iter = self.train_model(X_train, y_train, X_val, y_val, model_params)
        print(f'  ✓ 最佳迭代: {best_iter}')

        # 评估
        print('评估模型...')
        val_metrics, _ = self.evaluate_model(model, X_val, y_val, threshold=0.5)
        test_metrics, _ = self.evaluate_model(model, X_test, y_test, threshold=0.5)

        print(f'\n验证集表现:')
        print(f'  准确率: {val_metrics["accuracy"]*100:.2f}%')
        print(f'  精确率: {val_metrics["precision"]*100:.2f}%')
        print(f'  召回率: {val_metrics["recall"]*100:.2f}%')
        print(f'  F1-Score: {val_metrics["f1"]:.3f}')
        print(f'  AUC: {val_metrics["auc"]:.3f}')

        print(f'\n测试集表现:')
        print(f'  准确率: {test_metrics["accuracy"]*100:.2f}%')
        print(f'  精确率: {test_metrics["precision"]*100:.2f}%')
        print(f'  召回率: {test_metrics["recall"]*100:.2f}%')
        print(f'  F1-Score: {test_metrics["f1"]:.3f}')
        print(f'  AUC: {test_metrics["auc"]:.3f}')

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f'\n耗时: {elapsed:.1f}s')

        result = {
            'exp_name': exp_name,
            'description': description,
            'feature_count': len(final_features),
            'features': final_features,
            'model_params': model_params,
            'best_iteration': best_iter,
            'val_metrics': val_metrics,
            'test_metrics': test_metrics,
            'elapsed_seconds': elapsed,
            'timestamp': datetime.now().isoformat(),
        }

        self.results.append(result)

        return model, result

    def run_all_experiments(self):
        """运行所有实验"""
        print('='*80)
        print('开始训练实验（修复版）')
        print('='*80)
        print()

        feature_sets = self.define_feature_sets()

        param_sets = {
            'shallow': {'max_depth': 3, 'learning_rate': 0.05, 'min_child_weight': 5},
            'medium': {'max_depth': 6, 'learning_rate': 0.05, 'min_child_weight': 3},
            'deep': {'max_depth': 10, 'learning_rate': 0.03, 'min_child_weight': 1},
        }

        # 实验：不同特征组合
        for feat_name, feat_list in feature_sets.items():
            self.run_experiment(
                exp_name=f'fixed_{feat_name}_medium',
                feature_set=feat_list,
                model_params=param_sets['medium'],
                description=f'特征集: {feat_name}, 模型: medium, 时间序列分割'
            )

        # 最佳特征集 + 不同深度
        for param_name, params in param_sets.items():
            if param_name == 'medium':
                continue  # 已经跑过了
            self.run_experiment(
                exp_name=f'fixed_all_{param_name}',
                feature_set=feature_sets['all'],
                model_params=params,
                description=f'特征集: all, 模型: {param_name}, 时间序列分割'
            )

        self.save_results()
        self.print_comparison_report()

    def save_results(self):
        """保存实验结果"""
        output_dir = Path(__file__).parent / 'experiment_results'
        output_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = output_dir / f'experiments_fixed_{timestamp}.json'

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)

        print(f'\n✓ 实验结果已保存: {output_file}')

    def print_comparison_report(self):
        """打印对比报告"""
        print('\n' + '='*80)
        print('实验对比报告（修复版）')
        print('='*80)

        sorted_results = sorted(self.results, key=lambda x: x['test_metrics']['accuracy'], reverse=True)

        print(f'\n{"实验名称":<30} {"特征数":<8} {"验证准确率":<12} {"测试准确率":<12} {"测试精确率":<12} {"测试召回率":<12} {"测试F1":<10}')
        print('-' * 110)

        for r in sorted_results:
            print(f'{r["exp_name"]:<30} '
                  f'{r["feature_count"]:<8} '
                  f'{r["val_metrics"]["accuracy"]*100:>10.2f}%  '
                  f'{r["test_metrics"]["accuracy"]*100:>10.2f}%  '
                  f'{r["test_metrics"]["precision"]*100:>10.2f}%  '
                  f'{r["test_metrics"]["recall"]*100:>10.2f}%  '
                  f'{r["test_metrics"]["f1"]:>8.3f}')

        best = sorted_results[0]
        print(f'\n🏆 最佳实验: {best["exp_name"]}')
        print(f'   测试准确率: {best["test_metrics"]["accuracy"]*100:.2f}%')
        print(f'   测试精确率: {best["test_metrics"]["precision"]*100:.2f}%')
        print(f'   测试召回率: {best["test_metrics"]["recall"]*100:.2f}%')
        print(f'   测试AUC: {best["test_metrics"]["auc"]:.3f}')


def main():
    data_path = Path(__file__).parent.parent / 'data' / 'dataset_cache' / 'training_data_202606_202607.parquet'

    runner = ExperimentRunner(data_path)
    runner.load_data()
    runner.run_all_experiments()

    print('\n' + '='*80)
    print('所有实验完成！')
    print('='*80)


if __name__ == '__main__':
    main()
