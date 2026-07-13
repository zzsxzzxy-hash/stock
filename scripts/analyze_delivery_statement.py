import argparse
import json
import math
import subprocess
from collections import defaultdict, deque
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd


def _num(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    return float(str(value).replace(",", "").strip())


def _date_from_yyyymmdd(value):
    text = str(int(value)) if not isinstance(value, str) else value.strip()
    return pd.to_datetime(text, format="%Y%m%d").date()


def _json_clean(value):
    if isinstance(value, dict):
        return {k: _json_clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, date)):
        return str(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _read_statement(path):
    df = pd.read_excel(path, sheet_name=0, header=1)
    df = df.dropna(how="all")
    df = df[df["日期"].notna()].copy()
    df["日期"] = df["日期"].map(_date_from_yyyymmdd)
    for col in ["成交数量", "成交均价", "佣金", "印花税", "其他费", "发生金额", "资金余额"]:
        df[col] = df[col].map(_num)
    df["证券代码"] = df["证券代码"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    df["证券名称"] = df["证券名称"].astype(str).str.replace("\n", "", regex=False).str.strip()
    df["摘要"] = df["摘要"].astype(str).str.strip()
    df = df[df["摘要"].isin(["证券买入", "证券卖出"])].copy()
    df = df.reset_index(drop=True)
    df["trade_id"] = [f"T{i + 1:04d}" for i in range(len(df))]
    return df


def _query_prices(db, codes, start_date, end_date, prices_csv=None):
    if prices_csv:
        prices = pd.read_csv(prices_csv, parse_dates=["date"])
        if not prices.empty:
            prices["date"] = prices["date"].dt.date
            prices["code"] = prices["code"].astype(str).str.zfill(6)
        return prices

    if not codes:
        return pd.DataFrame(columns=["date", "code", "open", "high", "low", "close"])
    code_sql = ",".join("'" + c.replace("'", "''") + "'" for c in sorted(codes))
    sql = f"""
COPY (
  SELECT date, code, open, high, low, close
  FROM cn_stock_hist_data
  WHERE code IN ({code_sql})
    AND date >= '{start_date}'
    AND date <= '{end_date}'
  ORDER BY code, date
) TO STDOUT WITH CSV HEADER
"""
    result = subprocess.run(
        ["psql", "-d", db, "-c", sql],
        check=True,
        text=True,
        capture_output=True,
    )
    prices = pd.read_csv(StringIO(result.stdout), parse_dates=["date"])
    if not prices.empty:
        prices["date"] = prices["date"].dt.date
        prices["code"] = prices["code"].astype(str).str.zfill(6)
    return prices


def _fifo_actuals(trades):
    lots_by_code = defaultdict(deque)
    buy_stats = {}
    unmatched_sells = []
    allocations = []

    for _, row in trades.sort_values(["日期", "trade_id"]).iterrows():
        code = row["证券代码"]
        qty = row["成交数量"]
        amount = row["发生金额"]

        if row["摘要"] == "证券买入":
            total_cost = -amount
            lot = {
                "trade_id": row["trade_id"],
                "remaining_qty": qty,
                "cost_per_share": total_cost / qty if qty else 0,
            }
            lots_by_code[code].append(lot)
            buy_stats[row["trade_id"]] = {
                "actual_sold_qty": 0.0,
                "actual_cost_matched": 0.0,
                "actual_sell_proceeds": 0.0,
                "actual_pnl": 0.0,
                "actual_sell_dates": [],
            }
        elif row["摘要"] == "证券卖出":
            remaining = qty
            proceeds_per_share = amount / qty if qty else 0
            while remaining > 1e-9 and lots_by_code[code]:
                lot = lots_by_code[code][0]
                matched = min(remaining, lot["remaining_qty"])
                cost = matched * lot["cost_per_share"]
                proceeds = matched * proceeds_per_share
                pnl = proceeds - cost
                stat = buy_stats[lot["trade_id"]]
                stat["actual_sold_qty"] += matched
                stat["actual_cost_matched"] += cost
                stat["actual_sell_proceeds"] += proceeds
                stat["actual_pnl"] += pnl
                stat["actual_sell_dates"].append(str(row["日期"]))
                allocations.append(
                    {
                        "buy_trade_id": lot["trade_id"],
                        "sell_trade_id": row["trade_id"],
                        "code": code,
                        "sell_date": str(row["日期"]),
                        "qty": matched,
                        "sell_net_price": proceeds_per_share,
                        "matched_cost": cost,
                        "sell_proceeds": proceeds,
                        "pnl": pnl,
                    }
                )
                lot["remaining_qty"] -= matched
                remaining -= matched
                if lot["remaining_qty"] <= 1e-9:
                    lots_by_code[code].popleft()
            if remaining > 1e-9:
                unmatched_sells.append(
                    {
                        "trade_id": row["trade_id"],
                        "date": str(row["日期"]),
                        "code": code,
                        "name": row["证券名称"],
                        "qty": remaining,
                        "net_proceeds": remaining * proceeds_per_share,
                        "note": "卖出数量未能在本月买入记录中 FIFO 匹配，可能是月初持仓。",
                    }
                )
    return buy_stats, allocations, unmatched_sells


def _holding_metrics(buy, price_slice, as_of):
    buy_price = float(buy["成交均价"])
    total_cost = -float(buy["发生金额"])
    qty = float(buy["成交数量"])
    cost_price = total_cost / qty if qty else buy_price

    if price_slice.empty:
        return {
            "price_start": None,
            "price_end": None,
            "latest_close": None,
            "latest_date": None,
            "max_high": None,
            "max_high_date": None,
            "min_low": None,
            "min_low_date": None,
            "max_gain_pct": None,
            "max_gain_pct_on_cost": None,
            "lowest_return_pct": None,
            "max_drawdown_pct": None,
            "hold_to_today_pct": None,
            "price_days": 0,
            "price_note": f"未找到 {buy['证券代码']} 自 {buy['日期']} 至 {as_of} 的行情。",
        }

    s = price_slice.sort_values("date").copy()
    max_high_idx = s["high"].idxmax()
    min_low_idx = s["low"].idxmin()
    latest = s.iloc[-1]

    running_peak = None
    max_dd = 0.0
    for _, r in s.iterrows():
        high = float(r["high"])
        low = float(r["low"])
        running_peak = high if running_peak is None else max(running_peak, high)
        if running_peak:
            dd = low / running_peak - 1
            max_dd = min(max_dd, dd)

    return {
        "price_start": str(s.iloc[0]["date"]),
        "price_end": str(s.iloc[-1]["date"]),
        "latest_close": float(latest["close"]),
        "latest_date": str(latest["date"]),
        "max_high": float(s.loc[max_high_idx, "high"]),
        "max_high_date": str(s.loc[max_high_idx, "date"]),
        "min_low": float(s.loc[min_low_idx, "low"]),
        "min_low_date": str(s.loc[min_low_idx, "date"]),
        "max_gain_pct": float(s.loc[max_high_idx, "high"]) / buy_price - 1 if buy_price else None,
        "max_gain_pct_on_cost": float(s.loc[max_high_idx, "high"]) / cost_price - 1 if cost_price else None,
        "lowest_return_pct": float(s.loc[min_low_idx, "low"]) / buy_price - 1 if buy_price else None,
        "max_drawdown_pct": max_dd,
        "hold_to_today_pct": float(latest["close"]) / buy_price - 1 if buy_price else None,
        "price_days": int(len(s)),
        "price_note": "",
    }


def analyze(statement_path, output_json, db, as_of, prices_csv=None):
    trades = _read_statement(statement_path)
    buys = trades[trades["摘要"] == "证券买入"].copy()
    codes = set(trades["证券代码"].dropna())
    start_date = buys["日期"].min()
    prices = _query_prices(db, codes, start_date, as_of, prices_csv)
    buy_stats, allocations, unmatched_sells = _fifo_actuals(trades)

    rows = []
    price_by_code = {code: grp for code, grp in prices.groupby("code")}
    for _, buy in buys.sort_values(["日期", "trade_id"]).iterrows():
        code = buy["证券代码"]
        p = price_by_code.get(code, pd.DataFrame())
        p = p[(p["date"] >= buy["日期"]) & (p["date"] <= as_of)] if not p.empty else p
        metrics = _holding_metrics(buy, p, as_of)
        stat = buy_stats.get(buy["trade_id"], {})
        actual_cost = stat.get("actual_cost_matched", 0.0)
        actual_pnl = stat.get("actual_pnl", 0.0)
        actual_return = actual_pnl / actual_cost if actual_cost else None
        rows.append(
            {
                "trade_id": buy["trade_id"],
                "buy_date": str(buy["日期"]),
                "code": code,
                "name": buy["证券名称"],
                "account": str(buy["股东账号"]),
                "buy_qty": float(buy["成交数量"]),
                "buy_price": float(buy["成交均价"]),
                "buy_fees": float(buy["佣金"] + buy["印花税"] + buy["其他费"]),
                "buy_cash_amount": float(buy["发生金额"]),
                "buy_total_cost": -float(buy["发生金额"]),
                "buy_cost_price": -float(buy["发生金额"]) / float(buy["成交数量"]),
                "actual_sold_qty": stat.get("actual_sold_qty", 0.0),
                "actual_unsold_qty": float(buy["成交数量"]) - stat.get("actual_sold_qty", 0.0),
                "actual_sell_dates": ", ".join(sorted(set(stat.get("actual_sell_dates", [])))),
                "actual_sell_proceeds": stat.get("actual_sell_proceeds", 0.0),
                "actual_cost_matched": actual_cost,
                "actual_pnl": actual_pnl if actual_cost else None,
                "actual_return_pct": actual_return,
                **metrics,
            }
        )

    detail = pd.DataFrame(rows)
    summary = []
    if not detail.empty:
        for (code, name), grp in detail.groupby(["code", "name"], dropna=False):
            total_cost = grp["buy_total_cost"].sum()
            matched_cost = grp["actual_cost_matched"].fillna(0).sum()
            actual_pnl = grp["actual_pnl"].fillna(0).sum()
            total_qty = grp["buy_qty"].sum()
            latest_close = grp.sort_values("buy_date")["latest_close"].dropna()
            summary.append(
                {
                    "code": code,
                    "name": name,
                    "buy_count": int(len(grp)),
                    "first_buy_date": grp["buy_date"].min(),
                    "last_buy_date": grp["buy_date"].max(),
                    "total_buy_qty": float(total_qty),
                    "total_buy_cost": float(total_cost),
                    "weighted_buy_price": float(total_cost / total_qty) if total_qty else None,
                    "latest_close": float(latest_close.iloc[-1]) if len(latest_close) else None,
                    "best_max_gain_pct": grp["max_gain_pct"].max(skipna=True),
                    "worst_max_drawdown_pct": grp["max_drawdown_pct"].min(skipna=True),
                    "worst_lowest_return_pct": grp["lowest_return_pct"].min(skipna=True),
                    "actual_matched_cost": float(matched_cost),
                    "actual_pnl": float(actual_pnl) if matched_cost else None,
                    "actual_return_pct": float(actual_pnl / matched_cost) if matched_cost else None,
                    "actual_sold_qty": float(grp["actual_sold_qty"].fillna(0).sum()),
                    "actual_unsold_qty": float(grp["actual_unsold_qty"].fillna(0).sum()),
                    "price_note_count": int((grp["price_note"] != "").sum()),
                }
            )

    raw_records = []
    for _, row in trades.iterrows():
        rec = {}
        for col in trades.columns:
            val = row[col]
            if isinstance(val, date):
                val = str(val)
            elif pd.isna(val):
                val = None
            rec[str(col)] = val
        raw_records.append(rec)

    payload = {
        "metadata": {
            "source_file": str(statement_path),
            "db": db,
            "as_of": str(as_of),
            "trade_rows": int(len(trades)),
            "buy_rows": int(len(buys)),
            "price_rows": int(len(prices)),
            "method": "买入以交割单发生金额转正作为含费成本；卖出以发生金额作为扣费税后净收入；实际收益按本月买入记录 FIFO 配对卖出计算；假设持有收益按买入成交均价与日线 high/low/close 计算。",
        },
        "summary": summary,
        "detail": detail.where(pd.notna(detail), None).to_dict(orient="records"),
        "allocations": allocations,
        "unmatched_sells": unmatched_sells,
        "raw_trades": raw_records,
    }
    Path(output_json).write_text(
        json.dumps(_json_clean(payload), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--statement", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--db", default="instockdb")
    parser.add_argument("--as-of", default=str(date.today()))
    parser.add_argument("--prices-csv")
    args = parser.parse_args()
    analyze(
        Path(args.statement),
        Path(args.output_json),
        args.db,
        pd.to_datetime(args.as_of).date(),
        Path(args.prices_csv) if args.prices_csv else None,
    )
