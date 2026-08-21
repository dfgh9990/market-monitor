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


def build(overview_path, sector_path, out_path, breadth_path=None, ts=None):
    with open(overview_path, "r", encoding="utf-8") as f:
        ov = json.load(f)
    with open(sector_path, "r", encoding="utf-8") as f:
        sec = json.load(f)
    raw = extract_overview(ov)
    raw.update(extract_sector(sec))
    merge_realtime_breadth(raw, breadth_path)
    tdx_note = cross_validate_tdx(raw, _auto_tdx_path(out_path))
    # 冰点逆修正：读取/更新连续冰点天数状态（与 data.json 同目录持久化）
    state_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "icepoint_state.json")
    state = load_icepoint_state(state_path)
    ip = update_icepoint(raw.get("breadth", {}), state, _trading_date(raw.get("breadth", {})))
    save_icepoint_state(state_path, state)
    out = compute(raw, ts=ts, icepoint=ip)
    if tdx_note:
        out.setdefault("diagnostics", []).append(tdx_note)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: python pipeline.py overview_raw.json sector_raw.json data.json [breadth_raw.json]")
        sys.exit(1)
    out = build(sys.argv[1], sys.argv[2], sys.argv[3],
                breadth_path=(sys.argv[4] if len(sys.argv) > 4 else None))
    rt = "（实时广度+通达信校验）" if out.get("breadth_realtime") else "（日线广度）"
    print("综合评分: {score}  仓位系数: {position_pct}%  ({position_label}) {rt}".format(rt=rt, **out))
    if out.get("overheat"):
        print("⚠️ 高潮预警：", out.get("market_status"))
    print("已写入:", sys.argv[3])
