#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练实验脚本 - 优化版
方案2: 特征选择（只用 base+early）
方案3: 融入用户操作记录（样本加权）
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


# 用户6月买入记录（日期-代码对）
USER_TRADES = [
    ('2026-06-01', '300620'), ('2026-06-01', '301171'), ('2026-06-02', '300620'),
    ('2026-06-02', '688802'), ('2026-06-03', '688802'), ('2026-06-04', '688347'),
    ('2026-06-04', '688347'), ('2026-06-04', '688347'), ('2026-06-05', '688347'),
    ('2026-06-09', '920046'), ('2026-06-09', '688126'), ('2026-06-09', '920046'),
    ('2026-06-10', '688820'), ('2026-06-15', '300620'), ('2026-06-16', '301306'),
    ('2026-06-16', '301306'), ('2026-06-16', '301306'), ('2026-06-16', '301306'),
    ('2026-06-17', '300136'), ('2026-06-17', '600176'), ('2026-06-18', '688195'),
    ('2026-06-18', '688195'), ('2026-06-22', '688268'), ('2026-06-23', '301222'),
    ('2026-06-23', '301222'), ('2026-06-24', '920706'), ('2026-06-24', '688183'),
    ('2026-06-25', '300179'), ('2026-06-25', '600601'), ('2026-06-26', '920438'),
    ('2026-06-26', '688126'), ('2026-06-29', '600396'), ('2026-06-29', '688802'),
    ('2026-06-30', '688676'), ('2026-06-30', '300503'),
]


class OptimizedExperimentRunner:
    """优化版实验运行器"""

    def __init__(self, data_path):
        self.data_path = data_path
        self.df = None
        self.results = []

        # 移除泄露特征
        self.leakage_features = [
            'change_rate', 'amplitude', 'deal_amount', 'close_price', 'turnoverrate',
            'daily_volume_ratio', 'intraday_return', 'overnight_return', 'total_return',
            'theme_avg_change', 'theme_median_change', 'theme_avg_volume_ratio',
            'price_change_delta',
        ]

    def load_data(self):
        """加载数据集"""
        print('='*80)
        print('加载数据集（优化版）')
        print('='*80)

        self.df = pd.read_parquet(self.data_path)
        print(f'✓ 原始数据: {len(self.df)} 条, {len(self.df.columns)} 个特征')

        # 移除泄露特征
        removed = [f for f in self.leakage_features if f in self.df.columns]
        self.df = self.df.drop(columns=removed, errors='ignore')

        print(f'✓ 移除泄露特征: {len(removed)} 个')
        print(f'✓ 剩余特征: {len(self.df.columns)} 个')

        # 标记用户操作
        self.df['is_user_trade'] = self.df.apply(
            lambda row: (row['date'], row['code']) in USER_TRADES, axis=1
        )

        user_count = self.df['is_user_trade'].sum()
        print(f'✓ 用户操作记录: {user_count} 条（匹配到数据集）')
        print(f'  用户操作胜率: {(self.df[self.df["is_user_trade"]]["label_intraday"]==1).sum() / user_count * 100:.1f}%')
        print()

    def define_feature_sets(self):
        """定义特征集"""
        # 基础特征
        base_features = [
            'snapshot_volume_ratio', 'snapshot_vs_daily_volume',
            'prev_1d_change_rate', 'prev_3d_return', 'prev_5d_return',
            'prev_1d_volume_ratio', 'prev_3d_volume_ratio', 'prev_5d_volume_ratio',
            'prev_1d_turnover', 'prev_3d_turnover', 'prev_5d_turnover',
            'volume_change_ratio', 'shrink_volume_rise',
            'has_theme', 'theme_confidence',
        ]

        # 早盘特征
        early_features = [
            'max_change_0935_0955', 'min_change_0935_0955',
            'final_change_0955', 'open_change_0935',
            'pullback_ratio', 'pullback_abs',
            'max_slope', 'avg_slope', 'slope_std', 'red_minutes_ratio',
        ]

        market_type = ['market']

        feature_sets = {
            'base': base_features + market_type,
            'base+early': base_features + early_features + market_type,
        }

        return feature_sets

    def prepare_features(self, feature_list, use_weights=False):
        """准备特征和样本权重"""
        available_features = [f for f in feature_list if f in self.df.columns]

        X = self.df[available_features].copy()
        y = self.df['label_intraday'].copy()

        # 样本权重
        if use_weights:
            weights = np.where(self.df['is_user_trade'], 5.0, 1.0)
        else:
            weights = None

        # 处理市场类型
        if 'market' in X.columns:
            market_dummies = pd.get_dummies(X['market'], prefix='market')
            X = pd.concat([X.drop('market', axis=1), market_dummies], axis=1)

        X = X.fillna(0).replace([np.inf, -np.inf], 0)

        return X, y, weights, list(X.columns)

    def split_data_by_time(self, X, y, weights=None):
        """按时间序列分割"""
        dates = sorted(self.df['date'].unique())
        n_dates = len(dates)

        train_end = int(n_dates * 0.6)
        val_end = int(n_dates * 0.8)

        train_dates = dates[:train_end]
        val_dates = dates[train_end:val_end]
        test_dates = dates[val_end:]

        train_idx = self.df['date'].isin(train_dates)
        val_idx = self.df['date'].isin(val_dates)
        test_idx = self.df['date'].isin(test_dates)

        X_train = X[train_idx].reset_index(drop=True)
        y_train = y[train_idx].reset_index(drop=True)
        w_train = weights[train_idx] if weights is not None else None

        X_val = X[val_idx].reset_index(drop=True)
        y_val = y[val_idx].reset_index(drop=True)

        X_test = X[test_idx].reset_index(drop=True)
        y_test = y[test_idx].reset_index(drop=True)

        print(f'  训练集: {train_dates[0]} ~ {train_dates[-1]} ({len(X_train)} 条)')
        print(f'  验证集: {val_dates[0]} ~ {val_dates[-1]} ({len(X_val)} 条)')
        print(f'  测试集: {test_dates[0]} ~ {test_dates[-1]} ({len(X_test)} 条)')

        if w_train is not None:
            weighted_count = (w_train > 1).sum()
            print(f'  加权样本: {weighted_count} 条（权重×5）')

        return X_train, X_val, X_test, y_train, y_val, y_test, w_train

    def train_model(self, X_train, y_train, X_val, y_val, params, weights=None):
        """训练模型"""
        scale_pos_weight = (y_train == 0).sum() / (y_train == 1).sum()

        model_params = {
            'objective': 'binary:logistic',
            'eval_metric': 'logloss',
            'scale_pos_weight': scale_pos_weight,
            'random_state': 42,
            **params
        }

        dtrain = xgb.DMatrix(X_train, label=y_train, weight=weights)
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

    def run_experiment(self, exp_name, feature_set, model_params, use_weights=False, description=""):
        """运行实验"""
        print(f'\n{"="*80}')
        print(f'实验: {exp_name}')
        print(f'描述: {description}')
        print(f'{"="*80}')

        start_time = datetime.now()

        # 准备特征
        print('准备特征...')
        X, y, weights, final_features = self.prepare_features(feature_set, use_weights)
        print(f'  ✓ 特征数: {len(final_features)}')
        print(f'  ✓ 样本数: {len(X)}')

        # 分割数据
        print('按时间序列分割数据...')
        X_train, X_val, X_test, y_train, y_val, y_test, w_train = self.split_data_by_time(X, y, weights)

        # 训练
        print('训练模型...')
        model, best_iter = self.train_model(X_train, y_train, X_val, y_val, model_params, w_train)
        print(f'  ✓ 最佳迭代: {best_iter}')

        # 评估
        print('评估模型...')
        val_metrics, _ = self.evaluate_model(model, X_val, y_val)
        test_metrics, _ = self.evaluate_model(model, X_test, y_test)

        print(f'\n验证集: 准确率 {val_metrics["accuracy"]*100:.2f}%, '
              f'精确率 {val_metrics["precision"]*100:.2f}%, '
              f'召回率 {val_metrics["recall"]*100:.2f}%, '
              f'F1 {val_metrics["f1"]:.3f}')

        print(f'测试集: 准确率 {test_metrics["accuracy"]*100:.2f}%, '
              f'精确率 {test_metrics["precision"]*100:.2f}%, '
              f'召回率 {test_metrics["recall"]*100:.2f}%, '
              f'F1 {test_metrics["f1"]:.3f}')

        elapsed = (datetime.now() - start_time).total_seconds()

        result = {
            'exp_name': exp_name,
            'description': description,
            'feature_count': len(final_features),
            'use_weights': use_weights,
            'val_metrics': val_metrics,
            'test_metrics': test_metrics,
            'elapsed_seconds': elapsed,
        }

        self.results.append(result)
        return model, result

    def run_all_experiments(self):
        """运行所有实验"""
        print('='*80)
        print('开始训练实验（优化版）')
        print('='*80)
        print()

        feature_sets = self.define_feature_sets()
        params = {'max_depth': 6, 'learning_rate': 0.05, 'min_child_weight': 3}

        # 实验1: base特征，无加权
        self.run_experiment(
            exp_name='opt_base_noweight',
            feature_set=feature_sets['base'],
            model_params=params,
            use_weights=False,
            description='基础特征，无样本加权'
        )

        # 实验2: base+early特征，无加权
        self.run_experiment(
            exp_name='opt_base+early_noweight',
            feature_set=feature_sets['base+early'],
            model_params=params,
            use_weights=False,
            description='基础+早盘特征，无样本加权'
        )

        # 实验3: base+early特征，用户操作加权
        self.run_experiment(
            exp_name='opt_base+early_weighted',
            feature_set=feature_sets['base+early'],
            model_params=params,
            use_weights=True,
            description='基础+早盘特征，用户操作×5加权'
        )

        self.save_results()
        self.print_comparison_report()

    def save_results(self):
        """保存结果"""
        output_dir = Path(__file__).parent / 'experiment_results'
        output_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = output_dir / f'experiments_optimized_{timestamp}.json'

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)

        print(f'\n✓ 实验结果已保存: {output_file}')

    def print_comparison_report(self):
        """打印对比报告"""
        print('\n' + '='*80)
        print('实验对比报告（优化版）')
        print('='*80)

        sorted_results = sorted(self.results, key=lambda x: x['test_metrics']['f1'], reverse=True)

        print(f'\n{"实验名称":<30} {"特征数":<8} {"加权":<6} {"测试准确率":<12} {"测试精确率":<12} {"测试召回率":<12} {"测试F1":<10}')
        print('-' * 110)

        for r in sorted_results:
            print(f'{r["exp_name"]:<30} '
                  f'{r["feature_count"]:<8} '
                  f'{"是" if r["use_weights"] else "否":<6} '
                  f'{r["test_metrics"]["accuracy"]*100:>10.2f}%  '
                  f'{r["test_metrics"]["precision"]*100:>10.2f}%  '
                  f'{r["test_metrics"]["recall"]*100:>10.2f}%  '
                  f'{r["test_metrics"]["f1"]:>8.3f}')

        best = sorted_results[0]
        print(f'\n🏆 最佳实验（按F1排序）: {best["exp_name"]}')
        print(f'   测试准确率: {best["test_metrics"]["accuracy"]*100:.2f}%')
        print(f'   测试精确率: {best["test_metrics"]["precision"]*100:.2f}%')
        print(f'   测试召回率: {best["test_metrics"]["recall"]*100:.2f}%')
        print(f'   测试F1: {best["test_metrics"]["f1"]:.3f}')
        print(f'   测试AUC: {best["test_metrics"]["auc"]:.3f}')


def main():
    data_path = Path(__file__).parent.parent / 'data' / 'dataset_cache' / 'training_data_202606_202607.parquet'

    runner = OptimizedExperimentRunner(data_path)
    runner.load_data()
    runner.run_all_experiments()

    print('\n' + '='*80)
    print('所有实验完成！')
    print('='*80)


if __name__ == '__main__':
    main()
