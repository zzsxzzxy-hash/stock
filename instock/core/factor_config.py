#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量能因子配置模块
- 定义默认配置（与现有算法完全等价）
- 从 Redis 读写用户自定义配置
- 提供 apply_factor_config() 供评分引擎调用

配置结构（JSON）：
{
  "A": {                          # 因子A：位置因子
    "enabled": true,
    "desc": "...",
    "formula": "...",
    "thresholds": {               # 判断参数
      "low_ma_ratio":  1.05,      # close <= ma120 × low_ma_ratio → 低位
      "break_ratio":   0.98,      # close >= high120 × break_ratio → 突破
      "high_ma_ratio": 1.2        # close > ma120 × high_ma_ratio → 高位
    },
    "veto": {                     # 一票否决参数
      "high120_ratio":  0.95,     # 高位滞涨判断阈值
      "vol_ratio_min":  2.0,      # 触发否决的最低量比
      "change_max":     1.0       # 触发否决的最高涨幅%
    },
    "scores": {
      "low":   {"type": "fixed",  "value": 4, "formula": ""},
      "break": {"type": "fixed",  "value": 3, "formula": ""},
      "other": {"type": "fixed",  "value": 0, "formula": ""},
      "veto":  {"type": "fixed",  "value": -99, "formula": ""}
    }
  },
  "B": {                          # 因子B：效率因子  E=涨跌幅/换手率
    "enabled": true,
    "desc": "...",
    "formula": "E = 涨跌幅 / 换手率",
    "scores": {
      "continuous": {             # 连续2日递增
        "type": "fixed", "value": 2, "formula": ""
      },
      "single": {                 # 单日递增
        "type": "fixed", "value": 1, "formula": ""
      },
      "none": {                   # 不递增
        "type": "fixed", "value": 0, "formula": ""
      }
    }
  },
  "C": {                          # 因子C：量能因子
    "enabled": true,
    "desc": "...",
    "formula": "vol_ratio = (今日累计量 + 昨日同期累计量) / 昨日全天量",
    "thresholds": {
      "vol_ratio_th":     1.5,    # 量比基准阈值
      "high_ratio":       2.0,    # 高量比阈值
    },
    "scores": {
      "high_good":  {"type": "fixed", "value": 2,  "formula": ""},
      "high_bad":   {"type": "fixed", "value": -1, "formula": ""},
      "normal":     {"type": "fixed", "value": 1,  "formula": ""},
      "none":       {"type": "fixed", "value": 0,  "formula": ""}
    }
  },
  "D": {                          # 因子D：板块因子
    "enabled": true,
    "desc": "...",
    "formula": "统计同板块内异动股数量和平均涨幅",
    "thresholds": {
      "signal_count_strong": 3,   # 强板块效应：同板块信号股 >= N 只
      "avg_change_weak":     1.0  # 弱板块效应：板块平均涨幅 > X%
    },
    "scores": {
      "strong": {"type": "fixed", "value": 2, "formula": ""},
      "weak":   {"type": "fixed", "value": 1, "formula": ""},
      "none":   {"type": "fixed", "value": 0, "formula": ""}
    }
  }
}

score item 的 type:
  "fixed"    → 直接用 value 作为得分
  "calc"     → 用 formula 表达式计算得分，支持变量：
               因子A: close, ma120, high120
               因子B: e_today, e_y, e_prev
               因子C: vol_ratio, avg_up, avg_down
               因子D: signal_count, avg_change
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

log = logging.getLogger(__name__)

_REDIS_KEY   = 'factor_config:v1'
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'factor_config.json')


# ── 默认配置（与现有算法完全等价）────────────────────────────────────────

DEFAULT_CONFIG = {
    "A": {
        "enabled": True,
        "name": "位置因子",
        "desc": "用120日最高价(High1)和120日最高收盘价(High2)与昨日收盘价比较，判断股票所处深度低位。",
        "formula": "High1=120日最高价, High2=120日最高收盘价; 比较 High1/High2 是否 > 昨日收盘×1.2",
        "thresholds": {
            "ratio": {"value": 1.2, "label": "低位判断倍数：High > 昨收 × 此值 视为高点远离"}
        },
        "veto": {
            "enabled": False,
            "high120_ratio": {"value": 0.95, "label": "滞涨判断：收盘价 > High1 × 此值"},
            "vol_ratio_min": {"value": 2.0,  "label": "触发否决的最低量比"},
            "change_max":    {"value": 1.0,  "label": "触发否决的最高涨幅(%)"}
        },
        "scores": {
            "score2": {"type": "fixed", "value": 2,   "formula": "", "label": "深度低位：High1 > 昨收×ratio 且 High2 > 昨收×ratio"},
            "score1": {"type": "fixed", "value": 1,   "formula": "", "label": "相对低位：High1 > 昨收×ratio 但 High2 ≤ 昨收×ratio"},
            "score0": {"type": "fixed", "value": 0,   "formula": "", "label": "其他（接近或超过高点）"},
            "other":  {"type": "fixed", "value": 0,   "formula": "", "label": "数据不足"},
            "veto":   {"type": "fixed", "value": -99, "formula": "", "label": "一票否决（高位巨量滞涨）"}
        }
    },
    "B": {
        "enabled": True,
        "name": "买力因子",
        "desc": "统计盘中每分钟涨跌方向，以涨分钟累计量占全部量的比例衡量主动买入强度，预判后续走势。",
        "formula": "买力强度 S = 涨分钟累计量 ÷ 全部累计量；分钟收盘>上分钟收盘算涨分钟",
        "thresholds": {
            "strong":  {"value": 0.62, "label": "强势买入阈值：S ≥ 此值 → +2分"},
            "normal":  {"value": 0.55, "label": "温和买入阈值：S ≥ 此值 → +1分"},
            "neutral": {"value": 0.48, "label": "均衡阈值：S ≥ 此值 → 0分，否则 -1分"}
        },
        "scores": {
            "strong":  {"type": "fixed", "value": 2,  "formula": "", "label": "强势买入（S ≥ strong）"},
            "normal":  {"type": "fixed", "value": 1,  "formula": "", "label": "温和买入（normal ≤ S < strong）"},
            "neutral": {"type": "fixed", "value": 0,  "formula": "", "label": "均衡观望（neutral ≤ S < normal）"},
            "weak":    {"type": "fixed", "value": -1, "formula": "", "label": "卖压主导（S < neutral）"}
        }
    },
    "C": {
        "enabled": True,
        "name": "量能因子",
        "desc": "通过虚拟全日量比衡量当前量能爆发程度，并结合拉升质量（涨时放量/跌时缩量）评分。",
        "formula": "vol_ratio = (今日[09:31..T]累计量 + 昨日[T+1..15:00]剩余量) ÷ 昨日全天量；全程买力强度 S = 今日涨分钟量 ÷ 今日总量",
        "thresholds": {
            "vol_ratio_th":    {"value": 1.5,  "label": "量比基准阈值（低于此值不计分）"},
            "high_ratio":      {"value": 2.0,  "label": "高量比阈值（超过此值触发买力验证）"},
            "buy_strength_th": {"value": 0.55, "label": "高量比时：买力强度 ≥ 此值为良性放量"}
        },
        "scores": {
            "high_good": {
                "type": "fixed", "value": 2, "formula": "",
                "label": "高量比 + 买力充足（vol_ratio ≥ high_ratio 且全程S ≥ buy_strength_th）"
            },
            "high_bad": {
                "type": "fixed", "value": -1, "formula": "",
                "label": "高量比 + 买力不足（vol_ratio ≥ high_ratio 但全程S < 0.45）"
            },
            "normal": {
                "type": "fixed", "value": 1, "formula": "",
                "label": "正常放量（vol_ratio_th ≤ vol_ratio < high_ratio）"
            },
            "none": {
                "type": "fixed", "value": 0, "formula": "",
                "label": "量能不足（vol_ratio < vol_ratio_th）"
            }
        }
    },
    "D": {
        "enabled": True,
        "name": "板块因子",
        "desc": "统计同板块内其他股票的异动情况，板块共振说明资金在集体流入某赛道。",
        "formula": "统计同板块内「总分>2的信号股数量」和「板块平均涨幅」",
        "thresholds": {
            "signal_count_strong": {"value": 3,   "label": "强板块：同板块信号股数量 ≥ 此值"},
            "avg_change_weak":     {"value": 1.0, "label": "弱板块：板块平均涨幅 > 此值(%)"}
        },
        "scores": {
            "strong": {
                "type": "fixed", "value": 2, "formula": "",
                "label": "强板块共振（信号股数 ≥ signal_count_strong）"
            },
            "weak": {
                "type": "fixed", "value": 1, "formula": "",
                "label": "弱板块共振（板块均涨 > avg_change_weak%）"
            },
            "none": {
                "type": "fixed", "value": 0, "formula": "",
                "label": "无板块共振"
            }
        }
    }
}


# ── Redis 读写 ────────────────────────────────────────────────────────────

def load_config() -> dict:
    """
    加载因子配置，优先级：文件 > Redis > DEFAULT_CONFIG
    文件是持久存储，Redis 是运行时热缓存（服务重启或 Redis 清空不会丢失配置）
    """
    # 1. 优先读文件（持久存储）
    saved = _load_from_file()
    if saved is None:
        # 2. 文件不存在时尝试 Redis（兼容旧数据）
        try:
            from instock.core.minute_bar_collector import get_redis
            r = get_redis()
            raw = r.get(_REDIS_KEY)
            if raw:
                saved = json.loads(raw)
        except Exception:
            pass
    if saved:
        return _deep_merge(DEFAULT_CONFIG, saved)
    return _deep_copy(DEFAULT_CONFIG)


def save_config(config: dict) -> bool:
    """保存配置：同时写文件（持久）和 Redis（热缓存）"""
    ok_file = _save_to_file(config)
    try:
        from instock.core.minute_bar_collector import get_redis
        r = get_redis()
        r.set(_REDIS_KEY, json.dumps(config, ensure_ascii=False))
    except Exception as e:
        log.warning(f"写入 Redis 缓存失败（不影响持久化）: {e}")
    _invalidate_rank_cache()
    if ok_file:
        log.info("因子配置已保存（文件+Redis）")
    return ok_file


def _load_from_file() -> dict | None:
    """从 JSON 文件读取配置，不存在返回 None"""
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"读取因子配置文件失败: {e}")
    return None


def _save_to_file(config: dict) -> bool:
    """持久化配置到 JSON 文件"""
    try:
        os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
        with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log.error(f"保存因子配置文件失败: {e}")
        return False


def reset_config() -> dict:
    """重置为默认配置（删除文件和 Redis）"""
    try:
        if os.path.exists(_CONFIG_FILE):
            os.remove(_CONFIG_FILE)
    except Exception as e:
        log.warning(f"删除配置文件失败: {e}")
    try:
        from instock.core.minute_bar_collector import get_redis
        get_redis().delete(_REDIS_KEY)
    except Exception as e:
        log.warning(f"删除 Redis 配置失败: {e}")
    _invalidate_rank_cache()
    return _deep_copy(DEFAULT_CONFIG)


def _invalidate_rank_cache():
    """删除所有 volume_rank 缓存键，强制重新计算"""
    try:
        from instock.core.minute_bar_collector import get_redis
        import datetime
        r = get_redis()
        today = datetime.date.today().strftime('%Y-%m-%d')
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match=f'volume_rank:{today}:*', count=200)
            if keys:
                r.delete(*keys)
            if cursor == 0:
                break
    except Exception:
        pass


def _deep_copy(d):
    return json.loads(json.dumps(d))


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 优先，base 补全缺失字段"""
    result = _deep_copy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ── 计算公式求值 ──────────────────────────────────────────────────────────

def _eval_formula(formula: str, variables: dict) -> float:
    """
    安全求值公式字符串
    支持: + - * / () 以及变量名
    例: "e_today / e_y"，变量 {"e_today": 3.0, "e_y": 2.0} → 1.5
    """
    if not formula or not formula.strip():
        return 0.0
    try:
        # 只允许安全的字符
        import re
        safe = re.sub(r'[^0-9a-zA-Z_\+\-\*\/\(\)\.\s]', '', formula)
        return float(eval(safe, {"__builtins__": {}}, variables))  # noqa: S307
    except Exception as e:
        log.warning(f"公式求值失败 formula={formula!r} vars={variables}: {e}")
        return 0.0


def _score_item(score_def: dict, variables: dict) -> float:
    """根据 score_def 的 type 计算得分"""
    if score_def.get('type') == 'calc':
        return _eval_formula(score_def.get('formula', ''), variables)
    return float(score_def.get('value', 0))


# ── 带配置的因子计算函数 ──────────────────────────────────────────────────

def apply_factor_a(cfg: dict, pre: dict, current_close: float,
                   rt_vol_ratio: float, today_change: float) -> tuple[float, bool]:
    """
    返回 (score, is_veto)
    is_veto=True 时调用方应跳过该股票
    """
    a_cfg = cfg.get('A', {})
    if not a_cfg.get('enabled', True):
        return 0.0, False

    th     = a_cfg.get('thresholds', {})
    veto_c = a_cfg.get('veto', {})
    scores = a_cfg.get('scores', {})

    high1   = pre.get('high1',   pre.get('high120', 0))
    high2   = pre.get('high2',   0)
    close_y = pre.get('close_y', 0)
    pos     = pre.get('position', 'other')

    # 一票否决（可选，默认关闭）
    if veto_c.get('enabled', False):
        h_ratio    = veto_c.get('high120_ratio', {}).get('value', 0.95)
        vol_min    = veto_c.get('vol_ratio_min', {}).get('value', 2.0)
        change_max = veto_c.get('change_max',    {}).get('value', 1.0)
        if (high1 > 0 and current_close > high1 * h_ratio and
                rt_vol_ratio > vol_min and today_change < change_max):
            return _score_item(scores.get('veto', {'type': 'fixed', 'value': -99}),
                               {'close': current_close, 'high1': high1, 'high2': high2}), True

    # 位置评分（用预计算好的 position 字段）
    variables = {'close': close_y, 'high1': high1, 'high2': high2}
    score_key = pos if pos in scores else 'other'
    return _score_item(scores.get(score_key, {'type': 'fixed', 'value': 0}), variables), False


def apply_factor_b(cfg: dict, today_bars: list) -> float:
    """
    买力因子：涨分钟累计量 / 全部累计量
    today_bars: 今日截止当前时刻的分钟巴列表
    """
    b_cfg = cfg.get('B', {})
    if not b_cfg.get('enabled', True):
        return 0.0

    th     = b_cfg.get('thresholds', {})
    scores = b_cfg.get('scores', {})

    strong_th  = th.get('strong',  {}).get('value', 0.62)
    normal_th  = th.get('normal',  {}).get('value', 0.55)
    neutral_th = th.get('neutral', {}).get('value', 0.48)

    total_vol = sum(b.get('volume', 0) for b in today_bars)
    if total_vol <= 0:
        return 0.0

    # 涨分钟：本分钟收盘 > 上分钟收盘
    up_vol = 0.0
    for i, b in enumerate(today_bars):
        prev_close = today_bars[i-1]['close'] if i > 0 else b.get('pre_close', b['close'])
        if b.get('close', 0) > prev_close:
            up_vol += b.get('volume', 0)

    S = up_vol / total_vol
    variables = {'S': S, 'up_vol': up_vol, 'total_vol': total_vol}

    if S >= strong_th:
        return _score_item(scores.get('strong',  {'type': 'fixed', 'value': 2}),  variables)
    if S >= normal_th:
        return _score_item(scores.get('normal',  {'type': 'fixed', 'value': 1}),  variables)
    if S >= neutral_th:
        return _score_item(scores.get('neutral', {'type': 'fixed', 'value': 0}),  variables)
    return _score_item(scores.get('weak',     {'type': 'fixed', 'value': -1}), variables)


def apply_factor_c(cfg: dict, today_bars: list, yesterday_remain_bars: list,
                   yesterday_total_vol: float, current_time: str) -> float:
    c_cfg = cfg.get('C', {})
    if not c_cfg.get('enabled', True):
        return 0.0

    th     = c_cfg.get('thresholds', {})
    scores = c_cfg.get('scores', {})

    vol_ratio_th    = th.get('vol_ratio_th',    {}).get('value', 1.5)
    high_ratio      = th.get('high_ratio',      {}).get('value', 2.0)
    buy_strength_th = th.get('buy_strength_th', {}).get('value', 0.55)

    today_vol_accum = sum(b.get('volume', 0) for b in today_bars if b.get('volume'))
    yest_remain_vol = sum(b.get('volume', 0) for b in yesterday_remain_bars if b.get('volume'))
    virtual_total   = today_vol_accum + yest_remain_vol

    if yesterday_total_vol <= 0:
        return _score_item(scores.get('none', {'type': 'fixed', 'value': 0}), {})

    vol_ratio = virtual_total / yesterday_total_vol

    if vol_ratio < vol_ratio_th:
        return _score_item(scores.get('none', {'type': 'fixed', 'value': 0}),
                           {'vol_ratio': vol_ratio})

    # 全程买力强度（今日涨分钟量 / 今日总量）
    up_vol = 0.0
    for i, b in enumerate(today_bars):
        prev_close = today_bars[i-1]['close'] if i > 0 else b.get('pre_close', b.get('close', 0))
        if b.get('close', 0) > prev_close:
            up_vol += b.get('volume', 0)
    buy_strength = up_vol / today_vol_accum if today_vol_accum > 0 else 0.0

    variables = {'vol_ratio': vol_ratio, 'buy_strength': buy_strength}

    if vol_ratio >= high_ratio:
        if buy_strength >= buy_strength_th:
            return _score_item(scores.get('high_good', {'type': 'fixed', 'value': 2}), variables)
        if buy_strength < 0.45:
            return _score_item(scores.get('high_bad',  {'type': 'fixed', 'value': -1}), variables)
        return _score_item(scores.get('normal', {'type': 'fixed', 'value': 1}), variables)

    return _score_item(scores.get('normal', {'type': 'fixed', 'value': 1}), variables)


def _get_pull_quality_avgs(bars: list) -> tuple[float, float]:
    """返回 (avg_up, avg_down)"""
    if len(bars) < 6:
        return 0.0, 0.0
    up_vols, down_vols = [], []
    i = len(bars) - 1
    while i >= 2:
        b0, b1, b2 = bars[i-2], bars[i-1], bars[i]
        if b0.get('close', 0) < b1.get('close', 0) < b2.get('close', 0) > 0:
            up_vols = [b0.get('volume', 0), b1.get('volume', 0), b2.get('volume', 0)]
            break
        i -= 1
    j = i - 3 if i >= 3 else 0
    while j >= 2:
        b0, b1, b2 = bars[j-2], bars[j-1], bars[j]
        if b0.get('close', 0) > b1.get('close', 0) > b2.get('close', 0) > 0:
            down_vols = [b0.get('volume', 0), b1.get('volume', 0), b2.get('volume', 0)]
            break
        j -= 1
    avg_up   = sum(up_vols)   / len(up_vols)   if up_vols   else 0.0
    avg_down = sum(down_vols) / len(down_vols) if down_vols else 0.0
    return avg_up, avg_down


def apply_factor_d(cfg: dict, code: str, sectors: dict,
                   sector_scores: dict) -> float:
    d_cfg = cfg.get('D', {})
    if not d_cfg.get('enabled', True):
        return 0.0

    th     = d_cfg.get('thresholds', {})
    scores = d_cfg.get('scores', {})

    strong_n    = th.get('signal_count_strong', {}).get('value', 3)
    weak_change = th.get('avg_change_weak',     {}).get('value', 1.0)

    my_sectors = sectors.get(code, [])
    if not my_sectors:
        return 0.0

    max_score = 0.0
    for s in my_sectors:
        info         = sector_scores.get(s, {})
        signal_count = info.get('signal_count', 0)
        avg_change   = info.get('avg_change', 0)
        variables    = {'signal_count': signal_count, 'avg_change': avg_change}

        if signal_count >= strong_n:
            sc = _score_item(scores.get('strong', {'type': 'fixed', 'value': 2}), variables)
            max_score = max(max_score, sc)
        elif avg_change > weak_change:
            sc = _score_item(scores.get('weak', {'type': 'fixed', 'value': 1}), variables)
            max_score = max(max_score, sc)

    return max_score
