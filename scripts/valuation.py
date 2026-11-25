#!/usr/bin/env python3
"""华尔街渣宝4.0 — 估值算术工具（确定性，零依赖）。

提供 SOTP 加总、估值差百分比、对标巨头倍数、最大替代增量百分比、
值博率（上涨空间/下跌空间，1/3/5年三档）、β/α 拆分的计算。
AI 在定性判断完「每块业务用什么估值法、给多少基数与倍数」后，把数字交给本脚本，
避免手算误差。可作为模块 import，也可命令行传 JSON 调用。

命令行示例 1（SOTP + 对标，同 3.0 用法）：
  python valuation.py '{"market_cap":1000,"businesses":[{"name":"电池","val":750},
    {"name":"储能","val":400},{"name":"出海","val":240}],
    "comparables":[{"name":"国内龙头A","mcap":8000,"currency":"CNY"},
                   {"name":"国外龙头B","mcap":50000,"currency":"USD","fx":7.1}]}'

命令行示例 2（值博率三档 + β/α，4.0 新增）：
  python valuation.py --zhibo '{"market_cap":1000,
    "segments":[{"horizon":"1年","valuation":1200,"downside_valuation":850},
                {"horizon":"3年","valuation":1600,"downside_valuation":800},
                {"horizon":"5年","valuation":2200,"downside_valuation":800}],
    "beta_pct":-10}'
  # beta_pct: 纯市场因素(β)对总估值差的贡献，负=市场情绪压制，正=情绪溢价
"""
import json
import sys


def sotp_total(businesses):
    """businesses: [{"name":str,"val":float}, ...] → 总估值(亿)"""
    return round(sum(float(b["val"]) for b in businesses), 2)


def diff_pct(valuation, market_cap):
    """估值差百分比 = (估值 - 市值) / 市值 × 100；市值<=0 返回 None"""
    if not market_cap or market_cap <= 0:
        return None
    return round((valuation - market_cap) / market_cap * 100, 2)


def comparable_multiples(giant_mcap, target_mcap):
    """巨头市值 ÷ 本品市值 = 倍数"""
    if not target_mcap or target_mcap <= 0:
        return None
    return round(giant_mcap / target_mcap, 2)


def max_incremental(giant_mcap, target_mcap, substitutable_share=1.0):
    """最大替代增量% = (巨头市值-本品市值)/本品市值 × 可替代份额 × 100"""
    if not target_mcap or target_mcap <= 0:
        return None
    return round((giant_mcap - target_mcap) / target_mcap * substitutable_share * 100, 2)


def upside_downside(valuation, market_cap, downside_valuation=None):
    """上涨/下跌空间(%)。
    上涨空间 = (估值-市值)/市值；下跌空间 = (市值-悲观估值)/市值。
    valuation 为 None（无数据，不估）→ 返回 (None, None)，禁止脑补。
    下跌空间无悲观估值数据时返回 None（禁止脑补）。"""
    if valuation is None:
        return None, None
    up = diff_pct(valuation, market_cap)
    down = None
    if downside_valuation is not None and market_cap and market_cap > 0:
        down = round((market_cap - float(downside_valuation)) / market_cap * 100, 2)
    return up, down


def zhibo_ratio(upside_pct, downside_pct):
    """值博率 = 上涨空间 / 下跌空间。任一为 None 或下跌<=0 → None（不强行凑数）。"""
    if upside_pct is None or downside_pct is None or downside_pct <= 0:
        return None
    return round(upside_pct / downside_pct, 2)


def verdict_zhibo(ratio):
    """值博率 → 风险/收益结论"""
    if ratio is None:
        return "无数据，不判断（禁止脑补）"
    if ratio > 1.5:
        return "收益显著大于风险"
    if ratio >= 1.0:
        return "收益略大于风险"
    if ratio >= 0.7:
        return "风险收益基本平衡"
    return "风险大于收益"


def beta_alpha(total_diff_pct, beta_pct):
    """总估值差拆成 β(纯市场因素) + α(个股自身)：alpha = total - beta"""
    if total_diff_pct is None or beta_pct is None:
        return None
    return round(total_diff_pct - beta_pct, 2)


def run(payload: dict) -> dict:
    market_cap = float(payload.get("market_cap", 0))
    businesses = payload.get("businesses", [])
    total = sotp_total(businesses)
    out = {
        "sotp_total_yi": total,
        "market_cap_yi": market_cap,
        "diff_pct": diff_pct(total, market_cap),
        "comparables": [],
    }
    for c in payload.get("comparables", []):
        fx = float(c.get("fx", 1.0))
        giant_cny = float(c["mcap"]) * fx
        out["comparables"].append({
            "name": c.get("name"),
            "giant_mcap_cny_yi": round(giant_cny, 2),
            "multiple": comparable_multiples(giant_cny, market_cap),
            "max_incremental_pct": max_incremental(
                giant_cny, market_cap, float(c.get("substitutable_share", 1.0))),
        })
    return out


def run_zhibo(payload: dict) -> dict:
    """值博率三档 + β/α 拆分。
    segments: [{"horizon":"1年","valuation":X,"downside_valuation":Y}, ...]
    beta_pct: β(纯市场因素)对总估值差的贡献%。"""
    market_cap = float(payload.get("market_cap", 0))
    segs_out = []
    for s in payload.get("segments", []):
        val = s.get("valuation")
        dv = s.get("downside_valuation")
        up, down = upside_downside(val, market_cap, dv)
        ratio = zhibo_ratio(up, down)
        segs_out.append({
            "horizon": s.get("horizon"),
            "valuation_yi": val,
            "downside_valuation_yi": dv,
            "upside_pct": up,
            "downside_pct": down,
            "zhibo_ratio": ratio,
            "verdict": verdict_zhibo(ratio),
        })
    out = {"market_cap_yi": market_cap, "segments": segs_out}

    beta_pct = payload.get("beta_pct")
    if beta_pct is not None:
        # 以 1 年段估值差作为总估值差代表（或由 segments 指定 base_horizon）
        base_h = payload.get("base_horizon", "1年")
        base = next((s for s in segs_out if s["horizon"] == base_h), segs_out[0] if segs_out else None)
        if base:
            total_diff = base["upside_pct"]
            out["beta_alpha"] = {
                "base_horizon": base_h,
                "total_diff_pct": total_diff,
                "beta_pct": beta_pct,
                "alpha_pct": beta_alpha(total_diff, beta_pct),
                "note": "β=纯市场情绪贡献(正=情绪溢价/负=情绪压制)；α=个股自身基本面错配",
            }
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--zhibo":
        if len(args) < 2:
            print(__doc__)
            sys.exit(0)
        print(json.dumps(run_zhibo(json.loads(args[1])), ensure_ascii=False, indent=2))
    elif len(args) < 1:
        print(__doc__)
        sys.exit(0)
    else:
        print(json.dumps(run(json.loads(args[0])), ensure_ascii=False, indent=2))
