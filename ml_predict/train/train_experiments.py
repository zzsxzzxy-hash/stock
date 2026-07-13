#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练实验脚本 - 多组实验对比
测试不同的特征组合、模型参数，找出最优配置
"""

import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score, confusion_matrix
import xgboost as xgb
import pickle
import json


class ExperimentRunner:
    """实验运行器"""

    def __init__(self, data_path):
        """
        初始化
        Args:
            data_path: 数据集路径
        """
        self.data_path = data_path
        self.df = None
        self.results = []

    def load_data(self):
        """加载数据集"""
        print('='*80)
        print('加载数据集')
        print('='*80)

        self.df = pd.read_parquet(self.data_path)
        print(f'✓ 数据集大小: {len(self.df)} 条')
        print(f'✓ 特征数量: {len(self.df.columns)} 个')
        print(f'✓ 当日胜率: {(self.df["label_intraday"] == 1).sum() / len(self.df) * 100:.1f}%')
        print()

    def define_feature_sets(self):
        """定义特征集"""
        # 基础特征（不含早盘细节）
        base_features = [
            'turnoverrate', 'daily_volume_ratio', 'change_rate', 'amplitude', 'deal_amount',
            'snapshot_volume_ratio', 'snapshot_vs_daily_volume',
            'prev_1d_change_rate', 'prev_3d_return', 'prev_5d_return',
            'prev_1d_volume_ratio', 'prev_3d_volume_ratio', 'prev_5d_volume_ratio',
            'prev_1d_turnover', 'prev_3d_turnover', 'prev_5d_turnover',
            'volume_change_ratio', 'price_change_delta', 'shrink_volume_rise',
            'has_theme', 'theme_confidence',
        ]

        # 早盘涨幅特征
        early_change_features = [
            'max_change_0935_0955', 'min_change_0935_0955',
            'final_change_0955', 'open_change_0935',
        ]

        # 早盘回撤特征
        early_pullback_features = [
            'pullback_ratio', 'pullback_abs',
        ]

        # 早盘斜率特征
        early_slope_features = [
            'max_slope', 'avg_slope', 'slope_std', 'red_minutes_ratio',
        ]

        # 市场情绪特征
        market_sentiment_features = [
            'red_ratio', 'limit_up_ratio', 'limit_down_ratio', 'total_amount_billions',
        ]

        # 板块联动特征
        sector_features = [
            'theme_avg_change', 'theme_median_change',
            'theme_stock_count', 'theme_avg_volume_ratio',
        ]

        # 市场类型
        market_type = ['market']

        feature_sets = {
            'base': base_features + market_type,
            'base+early_change': base_features + early_change_features + market_type,
            'base+early_pullback': base_features + early_change_features + early_pullback_features + market_type,
            'base+early_slope': base_features + early_change_features + early_pullback_features + early_slope_features + market_type,
            'base+market': base_features + market_sentiment_features + market_type,
            'base+sector': base_features + sector_features + market_type,
            'all': base_features + early_change_features + early_pullback_features + early_slope_features + market_sentiment_features + sector_features + market_type,
        }

        return feature_sets

    def prepare_features(self, feature_list):
        """准备特征"""
        # 过滤掉数据集中不存在的特征
        available_features = [f for f in feature_list if f in self.df.columns]

        X = self.df[available_features].copy()
        y = self.df['label_intraday'].copy()

        # 处理市场类型（类别特征）
        if 'market' in X.columns:
            market_dummies = pd.get_dummies(X['market'], prefix='market')
            X = pd.concat([X.drop('market', axis=1), market_dummies], axis=1)

        # 填充缺失值
        X = X.fillna(0)

        # 替换无穷大值
        X = X.replace([np.inf, -np.inf], 0)

        return X, y, list(X.columns)

    def split_data(self, X, y, test_size=0.2, val_size=0.2):
        """
        分割数据集
        Args:
            test_size: 测试集比例
            val_size: 验证集比例（从训练集中分出）
        Returns:
            X_train, X_val, X_test, y_train, y_val, y_test
        """
        # 先分出测试集
        X_train_val, X_test, y_train_val, y_test = train_test_split(
            X, y, test_size=test_size, random_state=42, stratify=y
        )

        # 再从训练集中分出验证集
        X_train, X_val, y_train, y_val = train_test_split(
            X_train_val, y_train_val, test_size=val_size, random_state=42, stratify=y_train_val
        )

        return X_train, X_val, X_test, y_train, y_val, y_test

    def train_model(self, X_train, y_train, X_val, y_val, params):
        """
        训练 XGBoost 模型
        Args:
            params: 模型参数
        Returns:
            model, best_iteration
        """
        # 计算样本权重（处理类别不平衡）
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

        # 训练模型
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
        """
        评估模型
        Args:
            threshold: 分类阈值
        Returns:
            metrics dict
        """
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

        # 混淆矩阵
        tn, fp, fn, tp = confusion_matrix(y, y_pred).ravel()
        metrics['confusion_matrix'] = {'tn': int(tn), 'fp': int(fp), 'fn': int(fn), 'tp': int(tp)}

        return metrics, y_prob

    def run_experiment(self, exp_name, feature_set, model_params, description=""):
        """
        运行单个实验
        Args:
            exp_name: 实验名称
            feature_set: 特征列表
            model_params: 模型参数
            description: 实验描述
        """
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

        # 分割数据
        print('分割数据...')
        X_train, X_val, X_test, y_train, y_val, y_test = self.split_data(X, y)
        print(f'  ✓ 训练集: {len(X_train)} ({len(X_train)/len(X)*100:.1f}%)')
        print(f'  ✓ 验证集: {len(X_val)} ({len(X_val)/len(X)*100:.1f}%)')
        print(f'  ✓ 测试集: {len(X_test)} ({len(X_test)/len(X)*100:.1f}%)')

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

        # 记录结果
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
        print('开始训练实验')
        print('='*80)
        print()

        # 定义特征集
        feature_sets = self.define_feature_sets()

        # 定义模型参数组合
        param_sets = {
            'shallow': {'max_depth': 3, 'learning_rate': 0.05, 'min_child_weight': 5},
            'medium': {'max_depth': 6, 'learning_rate': 0.05, 'min_child_weight': 3},
            'deep': {'max_depth': 10, 'learning_rate': 0.03, 'min_child_weight': 1},
        }

        # 实验 1: 不同特征组合（中等深度模型）
        print('\n' + '='*80)
        print('实验组 1: 特征组合对比（中等深度模型）')
        print('='*80)

        for feat_name, feat_list in feature_sets.items():
            self.run_experiment(
                exp_name=f'feat_{feat_name}_medium',
                feature_set=feat_list,
                model_params=param_sets['medium'],
                description=f'特征集: {feat_name}, 模型: medium'
            )

        # 实验 2: 不同模型深度（全特征）
        print('\n' + '='*80)
        print('实验组 2: 模型深度对比（全特征）')
        print('='*80)

        for param_name, params in param_sets.items():
            self.run_experiment(
                exp_name=f'feat_all_{param_name}',
                feature_set=feature_sets['all'],
                model_params=params,
                description=f'特征集: all, 模型: {param_name}'
            )

        # 保存结果
        self.save_results()

        # 输出对比报告
        self.print_comparison_report()

    def save_results(self):
        """保存实验结果"""
        output_dir = Path(__file__).parent / 'experiment_results'
        output_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = output_dir / f'experiments_{timestamp}.json'

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)

        print(f'\n✓ 实验结果已保存: {output_file}')

    def print_comparison_report(self):
        """打印对比报告"""
        print('\n' + '='*80)
        print('实验对比报告')
        print('='*80)

        # 按测试集准确率排序
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

        # 最佳实验
        best = sorted_results[0]
        print(f'\n🏆 最佳实验: {best["exp_name"]}')
        print(f'   测试准确率: {best["test_metrics"]["accuracy"]*100:.2f}%')
        print(f'   测试精确率: {best["test_metrics"]["precision"]*100:.2f}%')
        print(f'   测试召回率: {best["test_metrics"]["recall"]*100:.2f}%')
        print(f'   测试AUC: {best["test_metrics"]["auc"]:.3f}')


def main():
    # 数据集路径
    data_path = Path(__file__).parent.parent / 'data' / 'dataset_cache' / 'training_data_202606_202607.parquet'

    # 创建实验运行器
    runner = ExperimentRunner(data_path)

    # 加载数据
    runner.load_data()

    # 运行所有实验
    runner.run_all_experiments()

    print('\n' + '='*80)
    print('所有实验完成！')
    print('='*80)


if __name__ == '__main__':
    main()
