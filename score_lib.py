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
    注意：MCP 返回的 zljlr 单位为「万元」，这里统一换算成「亿元」。
    输出两块：
      - sector_top/sector_bottom：按主力净流入排名的板块（fundflow.plate.top/bottom）
      - sector_rank：按涨跌幅排名的板块（rank.plate，含领涨股，供前端「板块涨幅 TOP」）"""
    top, bottom = [], []
    rank = []
    if isinstance(sector_json, dict) and sector_json.get("ok"):
        data = sector_json.get("data", {})
        ff = data.get("fundflow", {})
        plate = ff.get("plate", {})
        for t in plate.get("top", [])[:5]:
            z = _to_float(t.get("zljlr"))
            top.append({"name": t.get("name"), "zdf": _to_float(t.get("zdf")),
                        "zljlr": (z / 10000.0) if z is not None else None})
        for t in plate.get("bottom", [])[:5]:
            z = _to_float(t.get("zljlr"))
            bottom.append({"name": t.get("name"), "zdf": _to_float(t.get("zdf")),
                           "zljlr": (z / 10000.0) if z is not None else None})
        # 板块涨幅排行（rank.plate：bd_name/bd_zdf/bd_lb/bd_hsl/nzg_name/nzg_zdf）
        for t in data.get("rank", {}).get("plate", [])[:10]:
            if not t.get("bd_name"):
                continue
            rank.append({
                "name": t.get("bd_name"),
                "zdf": _to_float(t.get("bd_zdf")),
                "lb": _to_float(t.get("bd_lb")),
                "hsl": _to_float(t.get("bd_hsl")),
                "leader": t.get("nzg_name"),
                "leader_zdf": _to_float(t.get("nzg_zdf")),
            })
    # 默认按「主线清晰（上涨板块 60~80%）」给 0.7；精确值可由调用方覆盖
    return {"sector_top": top, "sector_bottom": bottom, "sector_rank": rank, "sector_rising_ratio": 0.7}


# ---------- 六维评分 ----------

def score_breadth(b):
    """涨跌比广度：抛物线倒扣分（反身性冷却机制）。

    X = 上涨比例(up/total)：得分 = 4·X·(1-X)·100
      - X=0.5（涨跌各半）得满分 100（最健康、最稳）
      - X→0（极弱）或 X→1（极强/高潮）得分都趋近 0
    哲学：中庸之道——只在"上涨占优但未到极致"时给重仓；
          盛极而衰——X>0.85 触高潮预警自动降权；X<0.15 强制空仓。
    返回 (score, detail, overheat, label)。
    """
    up = b.get("up", 0) or 0
    down = b.get("down", 0) or 0
    flat = b.get("flat", 0) or 0
    total = b.get("total", 0) or (up + down + flat)
    if total <= 0:
        return 50.0, {"上涨比例": "—", "x": 0.5, "市场状态": "无数据", "计分模型": "—"}, False, "中性"
    x = up / total
    if x < 0.15:
        score = 0.0
        status = "极弱 · 冰点（强制空仓）"
        model = "反身性冷却(非对称)"
    elif x <= 0.5:
        # 弱侧：随涨股比单调上升——跌得多→分低，平衡(50%)→满分；符合"跌过头归零、平衡给重仓"
        score = (x - 0.15) / (0.5 - 0.15) * 100
        status = "偏弱" if x < 0.30 else "正常"
        model = "反身性冷却(非对称)"
    else:
        # 强侧：倒 U 抛物线冷却——涨过头(>85%)自动降权防范退潮
        score = 4 * x * (1 - x) * 100
        if x > 0.80:
            status = "🔥 极度高潮，警惕退潮"
            model = "反身性冷却(抛物线)"
        elif x > 0.70:
            status = "偏热"
            model = "反身性冷却(抛物线)"
        else:
            status = "正常"
            model = "反身性冷却(抛物线)"
    overheat = x > 0.80
    detail = {
        "上涨比例": "{:.1f}%".format(x * 100),
        "x": round(x, 4),
        "市场状态": status,
        "计分模型": model,
    }
    return clamp(score), detail, overheat, status


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
    """市场情绪温度（涨跌停比）：同样抛物线倒扣分（反身性冷却）。

    X = 涨停/(涨停+跌停)；无跌停时以市场总量约 0.8% 作常态跌停基准，
    避免"零跌停"被误判为永远=1 而误报高潮。
      - X>0.8 极度亢奋 → 20 分并预警（盛极而衰）
      - X>0.6 偏热     → 抛物线得分后再扣 10
      - X<0.1 极度恐慌 → 10 分（近空仓）
      - 其余           → 抛物线 4·X·(1-X)·100
    返回 (score, detail, overheat, label)。
    """
    lu = b.get("limit_up", 0) or 0
    ld = b.get("limit_down", 0) or 0
    total = b.get("total", 0) or 5000
    if lu == 0 and ld == 0:
        return 50.0, {"涨停": 0, "跌停": 0, "涨跌停比": 0.0, "情绪定性": "无明显涨跌停"}, False, "中性"
    if ld > 0:
        x = lu / (lu + ld)
    else:
        baseline = max(1, round(total * 0.008))
        x = lu / (lu + baseline)
    if x > 0.8 and lu >= 80:
        # 真正的"高潮"需同时满足：涨跌停比极端高 且 涨停绝对数够多（百股涨停级别）。
        # 仅有少数涨停 + 跌停稀少（涨跌停比虚高）属于结构性偏热，不判为全局亢奋。
        score = 20.0
        status = "🔥 情绪极度亢奋，危险！"
        overheat = True
    elif x > 0.6:
        score = clamp(4 * x * (1 - x) * 100 - 10)
        status = "⚠️ 情绪偏热，注意分化"
        overheat = False
    elif x < 0.1:
        score = 10.0
        status = "💀 情绪极度恐慌，冰点"
        overheat = False
    else:
        score = 4 * x * (1 - x) * 100
        status = "✅ 情绪正常"
        overheat = False
    detail = {"涨停": lu, "跌停": ld, "涨跌停比": round(x, 2), "情绪定性": status}
    return clamp(score), detail, overheat, status


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


def position_from_score(score):
    """综合评分 → 动态仓位系数（绝对映射表，非线性）。

    综合评分区间   对应仓位   市场定性    核心操作建议（口诀）
    80 ~ 100 分    100%      强势主升    "贪婪持股"
    60 ~ 79 分     80%       偏强操作    "顺势加码"
    40 ~ 59 分     60%       震荡灵活    "高抛低吸"
    20 ~ 39 分     30%       偏弱防守    "只卖不买"
    0 ~ 19 分      0%        极端风险    "强制休息"
    返回 (position_pct, 市场定性, 口诀)。
    """
    if score >= 80:
        return 100, "强势主升", "贪婪持股"
    if score >= 60:
        return 80, "偏强操作", "顺势加码"
    if score >= 40:
        return 60, "震荡灵活", "高抛低吸"
    if score >= 20:
        return 30, "偏弱防守", "只卖不买"
    return 0, "极端风险", "强制休息"


def compute(raw, ts=None, icepoint=None):
    b = raw.get("breadth", {})
    v = raw.get("volume", {})
    tech = raw.get("technical", {})
    top = raw.get("sector_top", [])
    bottom = raw.get("sector_bottom", [])
    rr = raw.get("sector_rising_ratio")

    b_score, b_detail, b_overheat, b_label = score_breadth(b)
    # ---- 冰点逆修正（Contrarian Reversal）----
    # 连续 N 天广度冰点（上涨比例<15%）时，逆势上调广度分，防范"跌过头却永不抄底"。
    ip = icepoint or {}
    if ip.get("active") and ip.get("level", 0) >= 2:
        b_score = float(ip.get("corrected_score", b_score))
        b_label = "❄️ 冰点逆修正·抄底窗口"
        b_detail["市场状态"] = ip.get("note", b_detail.get("市场状态"))
        b_detail["计分模型"] = "冰点逆修正(Contrarian Reversal)"
        b_detail["冰点修正"] = ip.get("note")
    elif ip.get("active") and ip.get("level", 0) == 1:
        b_detail["冰点修正"] = ip.get("note")
        b_detail["计分模型"] = "冰点（首日·不抄底）"
    s_score, s_detail, s_overheat, s_label = score_sentiment(b)
    dims = {
        "breadth": round(b_score, 1),
        "volume": round(score_volume(v), 1),
        "sector": round(score_sector(top, rr), 1),
        "fund": round(score_fund(top, bottom), 1),
        "trend": round(score_trend(tech), 1),
        "sentiment": round(s_score, 1),
    }
    # 全局高潮预警：广度过热(涨跌比>80%普涨) 或 情绪过热(涨停≥80且涨跌停比>0.8)。
    # 两处阈值已在各自评分函数内封口，这里直接取或，避免日常误报。
    overheat = bool(b_overheat or s_overheat)
    if overheat:
        market_status = "🔥 高潮预警 · 警惕退潮"
    elif b_detail.get("x", 0.5) > 0.7:
        market_status = "偏热"
    elif b_detail.get("x", 0.5) < 0.2:
        market_status = "冰点"
    else:
        market_status = "正常"
    # 冰点逆修正优先于通用"冰点"标签，体现"物极必反"定性与抄底窗口
    if ip.get("active"):
        market_status = ip.get("note", market_status)

    total = sum(dims[k] * WEIGHTS[k] for k in WEIGHTS)
    total = round(total, 1)

    # 综合评分 → 动态仓位系数（绝对映射表，非线性阶梯）
    position, label, verdict = position_from_score(total)

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
    if overheat:
        diags.append("⚠️ 市场情绪进入高潮区（涨跌比/涨跌停比极端），系统已自动降权防范退潮，建议降低仓位")
    if ip.get("active"):
        diags.append(ip.get("message", "市场进入冰点状态，注意风险控制"))
    if not diags:
        diags.append("各项指标均衡，按仓位系数操作即可")

    if ts is None:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    dimensions_out = {}
    label_overrides = {"breadth": b_label, "sentiment": s_label}
    for k in dims:
        dimensions_out[k] = {"score": dims[k], "label": label_overrides.get(k, band_label(dims[k]))}

    sectors_top = [
        {"name": t.get("name"), "zdf": t.get("zdf"), "zljlr": t.get("zljlr")}
        for t in top[:5]
    ]

    # 数据来源标注：广度来自实时接口（东财或通达信）则如实标注
    bs = raw.get("breadth_source")
    if bs in ("eastmoney_realtime", "tongdaxin_realtime"):
        src_name = "东方财富" if bs == "eastmoney_realtime" else "通达信"
        source = "腾讯自选股 MCP + {}实时广度".format(src_name)
        breadth_realtime = True
    else:
        source = "腾讯自选股 MCP (westock-mcp)"
        breadth_realtime = False

    bd = dict(b)
    bd.update({
        "上涨比例": b_detail.get("上涨比例"),
        "涨跌停比": s_detail.get("涨跌停比"),
        "市场状态": market_status,
        "计分模型": b_detail.get("计分模型"),
        "情绪定性": s_detail.get("情绪定性"),
        "overheat": overheat,
    })

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
        "sectors_rank": raw.get("sector_rank", []),
        "diagnostics": diags,
        "breadth_detail": bd,
        "breadth_realtime": breadth_realtime,
        "overheat": overheat,
        "market_status": market_status,
        "icepoint": ip if ip else {"active": False},
        "source": source,
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
