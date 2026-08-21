"""管线入口：读取腾讯自选股 MCP 原始 JSON（+ 可选实时广度 + 可选通达信交叉验证），生成 data.json。

用法（本地/自动化）：
  python pipeline.py overview_raw.json sector_raw.json data.json
  python pipeline.py overview_raw.json sector_raw.json data.json breadth_raw.json

- overview_raw.json : westock data_market_overview(type='all') 的日线统计（量能/趋势/指数）
- sector_raw.json   : westock data_sector(mode='ranking') 的实时板块资金流
- breadth_raw.json  : （可选）东方财富实时涨跌家数；若提供，覆盖「涨跌比广度」与「市场情绪温度」
- tdx_breadth.json  : （可选，自动检测）通达信 tdx_screener 涨停/跌停总数，对实时广度做交叉验证
"""
import json
import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score_lib import extract_overview, extract_sector, compute


# ---------- 冰点逆修正状态维护 ----------
# 状态文件与 data.json 同目录，需随仓库一起推送，保证跨自动化运行持久化连续冰点天数。

def _trading_date(breadth):
    """取交易日（YYYY-MM-DD）：优先用实时广度的 fetched_at，回退本地日期。"""
    fa = (breadth.get("fetched_at") or "") if isinstance(breadth, dict) else ""
    if len(fa) >= 10 and fa[4] == "-":
        return fa[:10]
    return datetime.date.today().isoformat()


def prev_trading_day(date_str):
    d = datetime.date.fromisoformat(date_str)
    while True:
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5:  # 0=周一 … 4=周五
            return d.isoformat()


def load_icepoint_state(state_path):
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            s = json.load(f)
        if isinstance(s, dict):
            return s
    except Exception:
        pass
    return {"last_date": None, "consecutive_days": 0}


def save_icepoint_state(state_path, state):
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def update_icepoint(breadth, state, date_str):
    """根据今日广度是否冰点（上涨比例<15%）更新连续天数，并产出冰点逆修正方案。

    返回 {
      active, days, level(0/1/2/3), corrected_score, message, note, up_ratio
    }
    - Day1 冰点(level=1)：广度保持 0~20，判定"恐慌初期，不抄底"
    - Day2 冰点(level=2)：广度逆势修正至 70，判定"物极必反，关注抄底"
    - Day3+ 冰点(level=3)：广度上调至 85+，判定"极度超卖，可分批低吸"
    """
    up = (breadth.get("up", 0) or 0)
    total = (breadth.get("total", 0) or 0) or (up + (breadth.get("down", 0) or 0) + (breadth.get("flat", 0) or 0))
    x = (up / total) if total else 1.0
    is_ice = x < 0.15
    prev = prev_trading_day(date_str)

    if is_ice:
        if state.get("last_date") == date_str:
            days = state.get("consecutive_days", 0) or 1  # 同一交易日多次运行不重复计数
        elif state.get("last_date") == prev:
            days = (state.get("consecutive_days", 0) or 0) + 1
        else:
            days = 1
        state["last_date"] = date_str
        state["consecutive_days"] = days
    else:
        state["last_date"] = date_str
        state["consecutive_days"] = 0

    if is_ice:
        if days <= 1:
            level, corrected, note = 1, None, "冰点·首日（恐慌初期·不抄底·继续观望）"
            message = "❄️ 市场冰点（首日出现）：恐慌初期，不抄底，继续观望。"
        elif days == 2:
            level, corrected, note = 2, 70, "冰点逆修正·Day2·物极必反（广度修正至70）"
            message = "🟡 连续 2 天冰点：情绪极致宣泄，物极必反，开始关注抄底机会！"
        else:
            corrected = min(90, 85 + (days - 3) * 5)  # Day3=85, Day4=90, 之后封顶90
            level, note = 3, "冰点逆修正·Day{}·极度超卖（广度修正至{}）".format(days, corrected)
            message = "🟡 连续 {} 天冰点：极度超卖，具备强反弹需求，可分批低吸！".format(days)
    else:
        level, corrected, note, message = 0, None, "", ""

    return {
        "active": is_ice,
        "days": days if is_ice else 0,
        "level": level,
        "corrected_score": corrected,
        "message": message,
        "note": note,
        "up_ratio": round(x * 100, 1),
    }


# ---------- 连板最高标 ----------
def load_limitup(limitup_path):
    """读取 tdx_screener(message='连续涨停') 的返回 JSON，提取连板数最高的 Top5。

    返回 {"highest": {...}, "top5": [...], "total": N} 或 None。
    """
    if not limitup_path or not os.path.exists(limitup_path):
        return None
    try:
        with open(limitup_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return None
    # screener 返回可能被包在 markdown 里，尝试提取 JSON
    if not isinstance(raw, dict):
        return None
    data = raw.get("data")
    if not data:
        # 可能存的是 MCP 返回的完整响应文本，尝试从中提取 JSON
        return None

    def _f(v, d=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return d

    def sort_key(s):
        return _f(s.get("连续涨停天数0#", 0))

    data_sorted = sorted(data, key=sort_key, reverse=True)
    top5 = []
    for s in data_sorted[:5]:
        top5.append({
            "code": str(s.get("sec_code", "")),
            "name": str(s.get("sec_name", "")),
            "price": str(s.get("now_price", "")),
            "chg": _f(s.get("chg", 0)),
            "consecutive_days": int(_f(s.get("连续涨停天数0#", 0))),
            "days_boards": str(s.get("几天几板", "")),
            "board_type": str(s.get("板型", "")),
            "seal_amount_wan": _f(s.get("涨停成交额(万)", 0)),
            "themes": str(s.get("短线主题名称", "")),
            "reason": str(s.get("原因揭秘", "")),
        })
    highest = top5[0] if top5 else None
    total = raw.get("meta", {}).get("total", len(data)) if isinstance(raw.get("meta"), dict) else len(data)
    return {"highest": highest, "top5": top5, "total": total}


# ---------- 当日温度曲线 ----------
def append_temperature(out, history_path):
    """把本次评分快照追加到 temperature_history.json（按日期分组，同分钟去重）。

    返回当日温度历史列表 [{"time":"HH:MM","score":N,"position":N}, ...]。
    """
    ts = out.get("updated_at", "")
    date = ts[:10] if len(ts) >= 10 else datetime.date.today().isoformat()
    time = ts[11:16] if len(ts) >= 16 else datetime.datetime.now().strftime("%H:%M")
    entry = {"time": time, "score": out.get("score", 0), "position": out.get("position_pct", 0)}

    try:
        with open(history_path, "r", encoding="utf-8") as f:
            hist = json.load(f)
        if not isinstance(hist, dict):
            hist = {}
    except Exception:
        hist = {}

    day_data = hist.get(date, [])
    if not isinstance(day_data, list):
        day_data = []
    day_data = [e for e in day_data if e.get("time") != time]  # 同分钟去重
    day_data.append(entry)
    day_data.sort(key=lambda e: e.get("time", ""))
    hist[date] = day_data

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)

    return day_data


def merge_realtime_breadth(raw, breadth_path):
    """用实时广度覆盖 raw['breadth'] 的盘中字段；high20/low20 仍取日线（实时无此统计）。"""
    if not breadth_path or not os.path.exists(breadth_path):
        return raw
    try:
        with open(breadth_path, "r", encoding="utf-8") as f:
            rb = json.load(f)
    except Exception:
        return raw
    if not isinstance(rb, dict) or "up" not in rb:
        return raw

    b = raw.setdefault("breadth", {})
    for k in ("up", "down", "flat", "total", "limit_up", "limit_down"):
        if k in rb and rb[k] is not None:
            b[k] = rb[k]
    raw["breadth_source"] = rb.get("source", "eastmoney_realtime")
    return raw


def merge_tdx_indices(raw, indices_path, overview=None):
    """用通达信 tdx_quotes 实时指数覆盖腾讯日线指数（8/20 滞后）：
    1. raw['indices'] → 三大指数实时行情（前端大盘指数表格）
    2. raw['volume']['amount_yi'] → 两市实时成交额(亿)；avg10_ratio 用 overview 的 MONEY_10DAVG 基准重算
    3. raw['technical']['price'] → 上证实时现价（score_trend 用；MA 仍取日线，实时价 vs 昨日均线合理）
    返回 (raw, 是否生效)。
    """
    if not indices_path or not os.path.exists(indices_path):
        return raw, False
    try:
        with open(indices_path, "r", encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        return raw, False
    if not isinstance(idx, dict):
        return raw, False
    inds = idx.get("indices")
    if not isinstance(inds, list) or len(inds) < 3:
        return raw, False

    # 1) 大盘指数表格 → 实时（前端 normalizeStatic 用 close/chg_pct）
    raw["indices"] = [
        {"name": i.get("name"), "code": i.get("code", ""),
         "close": i.get("close"), "chg_pct": i.get("chg_pct")}
        for i in inds
    ]
    raw["indices_source"] = "tongdaxin_realtime"

    # 2) 量能健康度：两市实时成交额 vs 10日均值
    amt = idx.get("market_amount_yi")
    if amt:
        v = raw.setdefault("volume", {})
        v["amount_yi"] = amt
        v["source"] = "tongdaxin_realtime"
        # 基准：腾讯 overview 的 MONEY_10DAVG（10日均值，滞后可忽略）
        base10 = None
        if isinstance(overview, dict):
            for item in overview.get("data", []):
                if isinstance(item, dict) and item.get("listCode") == "market_statis_daily_trade":
                    base10 = (item.get("row") or {}).get("MONEY_10DAVG")
                    break
        if base10:
            v["avg10_ratio"] = round(amt / float(base10) * 100, 1)

    # 3) 指数趋势：上证实时现价
    for i in inds:
        if i.get("code") == "000001" or i.get("name") == "上证指数":
            t = raw.setdefault("technical", {})
            t["price"] = i.get("close")
            t["price_source"] = "tongdaxin_realtime"
            break

    return raw, True


def _auto_tdx_path(out_path):
    p = os.path.join(os.path.dirname(os.path.abspath(out_path)), "tdx_breadth.json")
    return p if os.path.exists(p) else None


def cross_validate_tdx(raw, tdx_path):
    """用通达信 tdx_screener 的涨停/跌停总数交叉验证东财实时广度。

    - 仅在 breadth_source=='eastmoney_realtime' 时参与修正（日线广度时间不一致，不做交叉）
    - 单一源偏差 >15% 时取两源均值并留痕，避免任一源异常带偏评分
    - 无论是否修正，都把通达信值附到 breadth 上供前端透明展示
    """
    rb = None
    if tdx_path:
        try:
            with open(tdx_path, "r", encoding="utf-8") as f:
                rb = json.load(f)
        except Exception:
            rb = None
    if not isinstance(rb, dict):
        return None
    lu_tdx = rb.get("limit_up")
    ld_tdx = rb.get("limit_down")
    if lu_tdx is None and ld_tdx is None:
        return None

    b = raw.setdefault("breadth", {})
    notes = []
    if raw.get("breadth_source") == "eastmoney_realtime":
        em_lu = b.get("limit_up", 0) or 0
        em_ld = b.get("limit_down", 0) or 0
        if lu_tdx is not None and em_lu:
            d = abs(lu_tdx - em_lu) / max(em_lu, 1)
            if d > 0.15:
                b["limit_up"] = round((em_lu + lu_tdx) / 2)
                notes.append("通达信校验·涨停：东财{} / 通达信{}（偏差{:.0%}），取均值".format(em_lu, lu_tdx, d))
            else:
                notes.append("通达信校验·涨停一致：东财{} / 通达信{}".format(em_lu, lu_tdx))
        if ld_tdx is not None and em_ld:
            d = abs(ld_tdx - em_ld) / max(em_ld, 1)
            if d > 0.15:
                b["limit_down"] = round((em_ld + ld_tdx) / 2)
                notes.append("通达信校验·跌停：东财{} / 通达信{}（偏差{:.0%}），取均值".format(em_ld, ld_tdx, d))
    b["limit_up_tdx"] = lu_tdx
    b["limit_down_tdx"] = ld_tdx
    b["cross_validated"] = True
    return "；".join(notes) if notes else None


def build(overview_path, sector_path, out_path, breadth_path=None, ts=None, limitup_path=None, indices_path=None):
    with open(overview_path, "r", encoding="utf-8") as f:
        ov = json.load(f)
    with open(sector_path, "r", encoding="utf-8") as f:
        sec = json.load(f)
    raw = extract_overview(ov)
    raw.update(extract_sector(sec))
    merge_realtime_breadth(raw, breadth_path)
    merged_idx, idx_ok = merge_tdx_indices(raw, indices_path, overview=ov)
    tdx_note = cross_validate_tdx(raw, _auto_tdx_path(out_path))
    # 冰点逆修正：读取/更新连续冰点天数状态（与 data.json 同目录持久化）
    state_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "icepoint_state.json")
    state = load_icepoint_state(state_path)
    ip = update_icepoint(raw.get("breadth", {}), state, _trading_date(raw.get("breadth", {})))
    save_icepoint_state(state_path, state)
    out = compute(raw, ts=ts, icepoint=ip)
    if idx_ok:
        out.setdefault("diagnostics", []).append("指数/量能来自通达信实时行情（腾讯日线回退：上证昨收 3903.72）")
    if tdx_note:
        out.setdefault("diagnostics", []).append(tdx_note)

    # 连板最高标 Top5
    work_dir = os.path.dirname(os.path.abspath(out_path))
    lp = load_limitup(limitup_path or os.path.join(work_dir, "limitup_raw.json"))
    if lp:
        out["limitup"] = lp

    # 当日温度曲线（追加并写入 temperature_history.json）
    history_path = os.path.join(work_dir, "temperature_history.json")
    out["temperature_history"] = append_temperature(out, history_path)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: python pipeline.py overview_raw.json sector_raw.json data.json [breadth_raw.json] [limitup_raw.json] [indices_raw.json]")
        sys.exit(1)
    out = build(sys.argv[1], sys.argv[2], sys.argv[3],
                breadth_path=(sys.argv[4] if len(sys.argv) > 4 else None),
                limitup_path=(sys.argv[5] if len(sys.argv) > 5 else None),
                indices_path=(sys.argv[6] if len(sys.argv) > 6 else None))
    rt = "（实时广度+通达信校验）" if out.get("breadth_realtime") else "（日线广度）"
    print("综合评分: {score}  仓位系数: {position_pct}%  ({position_label}) {rt}".format(rt=rt, **out))
    if out.get("overheat"):
        print("⚠️ 高潮预警：", out.get("market_status"))
    if out.get("limitup") and out["limitup"].get("highest"):
        h = out["limitup"]["highest"]
        print("连板最高标：{}（{}）{}连板 {}".format(h["name"], h["code"], h["consecutive_days"], h["days_boards"]))
    print("已写入:", sys.argv[3])
