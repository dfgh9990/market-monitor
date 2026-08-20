"""六维市场强弱评分引擎（纯标准库）。

数据来源：腾讯自选股 MCP（westock-mcp）返回的 overview / sector 原始 JSON。
本模块负责：
  1. extract_overview() / extract_sector() 把 MCP 原始结构归一化为内部 raw dict
  2. compute() 基于六维加权算出综合评分 + 仓位系数 + 诊断
输出 data.json 供前端（Cloudflare Pages / GitHub Pages / 本地）读取。
"""

import json
import datetime

# 六维权重（与需求一致）
WEIGHTS = {
    "breadth": 0.25,   # 涨跌比广度
    "volume": 0.15,    # 量能健康度
    "sector": 0.20,    # 板块资金集中度
    "fund": 0.20,      # 主力资金流向
    "trend": 0.10,     # 指数趋势 RSRS
    "sentiment": 0.10, # 市场情绪温度
}


def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- 从 MCP 原始 JSON 抽取 ----------

def extract_overview(overview_json):
    """overview_json: westock data_market_overview(type='all') 的原始返回。"""
    rows = {}
    if isinstance(overview_json, dict) and overview_json.get("ok"):
        for item in overview_json.get("data", []):
            rows[item.get("listCode")] = item.get("row", {})

    updown = rows.get("market_statis_updown", {})
    trade = rows.get("market_statis_daily_trade", {})
    tech = rows.get("market_statis_technical", {})
    val = rows.get("market_statis_valuation", {})

    breadth = {
        "up": updown.get("CNT_RED", 0),
        "down": updown.get("CNT_GREEN", 0),
        "flat": updown.get("CNT_ZERO", 0),
        "total": updown.get("CNT_TOTAL", 0),
        "limit_up": updown.get("CNT_REACH_UPLIMIT", 0),
        "limit_down": updown.get("CNT_REACH_DNLIMIT", 0),
        "high20": updown.get("CNT_HIGH20", 0),
        "low20": updown.get("CNT_LOW20", 0),
    }

    volume = {
        "amount_yi": trade.get("MONEY", 0),
        "avg5_ratio": trade.get("MONEY_5DAVG_RATIO", 100),
        "avg10_ratio": trade.get("MONEY_10DAVG_RATIO", 100),
        "avg20_ratio": trade.get("MONEY_20DAVG_RATIO", 100),
    }

    indices = [
        {"name": "上证指数", "code": "000001.SH", "close": trade.get("CLOSE_PRICE_SZZS"), "chg_pct": trade.get("CHANGE_PCT_SZZS")},
        {"name": "深证成指", "code": "399001.SZ", "close": trade.get("CLOSE_PRICE_SZCZ"), "chg_pct": trade.get("CHANGE_PCT_SZCZ")},
        {"name": "创业板指", "code": "399006.SZ", "close": trade.get("CLOSE_PRICE_CYBZ"), "chg_pct": trade.get("CHANGE_PCT_CYBZ")},
    ]

    technical = {
        "price": trade.get("CLOSE_PRICE_SZZS"),
        "ma5": tech.get("MA_5"),
        "ma10": tech.get("MA_10"),
        "ma20": tech.get("MA_20"),
        "ma60": tech.get("MA_60"),
        "ma250": tech.get("MA_250"),
        "macd": tech.get("MACD"),
        "dif": tech.get("DIF"),
        "dea": tech.get("DEA"),
        "rsi6": tech.get("RSI_6"),
        "rsi12": tech.get("RSI_12"),
        "rsi24": tech.get("RSI_24"),
        "kdj_k": tech.get("KDJ_K"),
        "kdj_d": tech.get("KDJ_D"),
        "kdj_j": tech.get("KDJ_J"),
    }

    valuation = {"pe_ttm_pct_10y": val.get("PE_TTM_PCT_10Y")}

    return {
        "date": updown.get("date") or trade.get("date"),
        "breadth": breadth,
        "volume": volume,
        "indices": indices,
        "technical": technical,
        "valuation": valuation,
    }


def extract_sector(sector_json):
    """sector_json: westock data_sector(mode='ranking') 的原始返回。
    注意：MCP 返回的 zljlr 单位为「万元」，这里统一换算成「亿元」。"""
    top, bottom = [], []
    if isinstance(sector_json, dict) and sector_json.get("ok"):
        ff = sector_json.get("data", {}).get("fundflow", {})
        plate = ff.get("plate", {})
        for t in plate.get("top", [])[:5]:
            z = _to_float(t.get("zljlr"))
            top.append({"name": t.get("name"), "zdf": _to_float(t.get("zdf")),
                        "zljlr": (z / 10000.0) if z is not None else None})
        for t in plate.get("bottom", [])[:5]:
            z = _to_float(t.get("zljlr"))
            bottom.append({"name": t.get("name"), "zdf": _to_float(t.get("zdf")),
                           "zljlr": (z / 10000.0) if z is not None else None})
    # 默认按「主线清晰（上涨板块 60~80%）」给 0.7；精确值可由调用方覆盖
    return {"sector_top": top, "sector_bottom": bottom, "sector_rising_ratio": 0.7}


# ---------- 六维评分 ----------

def score_breadth(b):
    up = b.get("up", 0)
    total = b.get("total", 0) or (up + b.get("down", 0))
    if total <= 0:
        return 50.0
    up_ratio = up / total * 100
    s = 20 + (up_ratio - 20) / 60 * 80
    if b.get("limit_up", 0) >= 100:
        s += 5
    return clamp(s)


def score_volume(v):
    r = v.get("avg10_ratio", 100) or 100
    s = (r - 60) / (130 - 60) * 100
    return clamp(s)


def score_sector(top, rising_ratio):
    if not top:
        return 50.0
    max_gain = max((t.get("zdf") or 0) for t in top)
    rr = rising_ratio if rising_ratio is not None else 0.5
    s = 35 + min(max_gain, 10) * 4 + (rr - 0.5) * 50
    return clamp(s)


def score_fund(top, bottom):
    net = 0.0
    for t in (top or [])[:3]:
        z = t.get("zljlr")
        if z is not None:
            net += z
    for t in (bottom or [])[:3]:
        z = t.get("zljlr")
        if z is not None:
            net += z
    s = 50 + net * 0.3
    return clamp(s)


def score_trend(t):
    price = t.get("price")
    if price is None:
        return 50.0
    mas = [t.get(k) for k in ("ma5", "ma10", "ma20", "ma60", "ma250") if t.get(k) is not None]
    if not mas:
        return 50.0
    above = sum(1 for m in mas if price >= m)
    ma_score = above / len(mas) * 100
    macd = t.get("macd") or 0
    rsi = t.get("rsi12") or 50
    s = ma_score * 0.7 + (15 if macd > 0 else 0) + (rsi - 50) * 0.3
    return clamp(s)


def score_sentiment(b):
    up = b.get("up", 0)
    total = b.get("total", 0) or 1
    up_ratio = up / total * 100
    lu = b.get("limit_up", 0)
    s = 40 + lu * 0.3 + (up_ratio - 50) * 0.8
    return clamp(s)


def band_label(score):
    if score >= 80:
        return "强势"
    if score >= 60:
        return "偏强"
    if score >= 40:
        return "中性"
    if score >= 20:
        return "偏弱"
    return "极弱"


def compute(raw, ts=None):
    b = raw.get("breadth", {})
    v = raw.get("volume", {})
    tech = raw.get("technical", {})
    top = raw.get("sector_top", [])
    bottom = raw.get("sector_bottom", [])
    rr = raw.get("sector_rising_ratio")

    dims = {
        "breadth": round(score_breadth(b), 1),
        "volume": round(score_volume(v), 1),
        "sector": round(score_sector(top, rr), 1),
        "fund": round(score_fund(top, bottom), 1),
        "trend": round(score_trend(tech), 1),
        "sentiment": round(score_sentiment(b), 1),
    }

    total = sum(dims[k] * WEIGHTS[k] for k in WEIGHTS)
    total = round(total, 1)
    position = int(round(clamp(total, 0, 100)))

    if total >= 80:
        label, verdict = "强势进攻", "市场强势，可积极做多"
    elif total >= 60:
        label, verdict = "偏强操作", "偏强震荡，可适度加仓"
    elif total >= 40:
        label, verdict = "震荡灵活", "震荡行情，灵活应对"
    elif total >= 20:
        label, verdict = "偏弱防守", "偏弱，控制仓位防守"
    else:
        label, verdict = "空仓观望", "弱势，建议空仓观望"

    diags = []
    if dims["volume"] < 50:
        diags.append("量能仅为10日均值的{:.0f}%，反弹持续性存疑".format(v.get("avg10_ratio", 0) or 0))
    if dims["trend"] < 45:
        diags.append("指数低于中长期均线，中期趋势仍偏弱")
    if top:
        diags.append("主线集中于{}（{:.2f}%），注意板块轮动风险".format(top[0]["name"], top[0]["zdf"] or 0))
    if dims["breadth"] >= 80:
        ur = (b.get("up", 0) / (b.get("total", 1) or 1)) * 100
        diags.append("普涨格局（涨股比{:.0f}%），情绪高涨".format(ur))
    if not diags:
        diags.append("各项指标均衡，按仓位系数操作即可")

    if ts is None:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    dimensions_out = {}
    for k in dims:
        dimensions_out[k] = {"score": dims[k], "label": band_label(dims[k])}

    sectors_top = [
        {"name": t.get("name"), "zdf": t.get("zdf"), "zljlr": t.get("zljlr")}
        for t in top[:5]
    ]

    return {
        "updated_at": ts,
        "trading": True,
        "score": total,
        "position_pct": position,
        "position_label": label,
        "verdict": verdict,
        "dimensions": dimensions_out,
        "indices": raw.get("indices", []),
        "sectors_top": sectors_top,
        "diagnostics": diags,
        "breadth_detail": b,
        "source": "腾讯自选股 MCP (westock-mcp)",
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            ov = json.load(f)
        with open(sys.argv[2], "r", encoding="utf-8") as f:
            sec = json.load(f)
        raw = extract_overview(ov)
        raw.update(extract_sector(sec))
        out = compute(raw)
        print(json.dumps(out, ensure_ascii=False, indent=2))
