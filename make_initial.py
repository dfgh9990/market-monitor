"""用 2026-08-20 真实盘面（腾讯自选股 MCP 抓取）生成首份 data.json。

把真实数据写成 MCP 原始结构文件（overview_raw.json / sector_raw.json），
再调用 pipeline 生成 data.json，用于首次部署 / 离线自测。
后续由自动化任务用实时 MCP 数据覆盖。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import build

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 真实数据（2026-08-20 收盘，来自 westock-mcp）----
OVERVIEW_RAW = {
    "ok": True,
    "data": [
        {"listCode": "market_statis_updown", "row": {
            "CNT_RED": 4096, "CNT_GREEN": 1347, "CNT_ZERO": 105, "CNT_TOTAL": 5548,
            "CNT_REACH_UPLIMIT": 77, "CNT_REACH_DNLIMIT": 0, "CNT_HIGH20": 429, "CNT_LOW20": 200,
            "date": "2026-08-20"}},
        {"listCode": "market_statis_daily_trade", "row": {
            "CLOSE_PRICE_SZZS": 3903.72, "CHANGE_PCT_SZZS": 0.24,
            "CLOSE_PRICE_SZCZ": 13972.78, "CHANGE_PCT_SZCZ": 0.59,
            "CLOSE_PRICE_CYBZ": 3495.59, "CHANGE_PCT_CYBZ": 0.64,
            "MONEY": 20793.63, "MONEY_5DAVG_RATIO": 90.24,
            "MONEY_10DAVG_RATIO": 87.61, "MONEY_20DAVG_RATIO": 89.73,
            "date": "2026-08-20"}},
        {"listCode": "market_statis_technical", "row": {
            "MA_5": 3939.654, "MA_10": 3941.263, "MA_20": 3888.7265,
            "MA_60": 3973.5955, "MA_250": 3977.9774, "MACD": 17.8009,
            "DIF": 1.7988, "DEA": -7.1016, "RSI_6": 44.0011,
            "RSI_12": 48.0795, "RSI_24": 47.4265, "KDJ_K": 50.043,
            "KDJ_D": 69.1116, "KDJ_J": 11.9058}},
        {"listCode": "market_statis_valuation", "row": {"PE_TTM_PCT_10Y": 88.25}},
    ],
}

SECTOR_RAW = {
    "ok": True,
    "data": {
        "fundflow": {
            "plate": {
                "top": [
                    {"name": "医疗服务", "zdf": "4.24", "zljlr": "387223.81"},
                    {"name": "生物制品", "zdf": "7.85", "zljlr": "330543.64"},
                    {"name": "化学制药", "zdf": "2.55", "zljlr": "277072.53"},
                    {"name": "贵金属", "zdf": "5.50", "zljlr": "130000.00"},
                    {"name": "医疗器械", "zdf": "4.27", "zljlr": "90000.00"},
                ],
                "bottom": [
                    {"name": "半导体", "zdf": "-0.36", "zljlr": "-699821.08"},
                    {"name": "电池", "zdf": "-0.84", "zljlr": "-155997.93"},
                    {"name": "小金属", "zdf": "-1.08", "zljlr": "-155063.95"},
                    {"name": "消费电子", "zdf": "-1.20", "zljlr": "-80000.00"},
                    {"name": "光学光电子", "zdf": "-0.90", "zljlr": "-60000.00"},
                ],
            }
        }
    },
}


def main():
    ov_path = os.path.join(HERE, "overview_raw.json")
    sec_path = os.path.join(HERE, "sector_raw.json")
    out_path = os.path.join(HERE, "data.json")

    with open(ov_path, "w", encoding="utf-8") as f:
        json.dump(OVERVIEW_RAW, f, ensure_ascii=False, indent=2)
    with open(sec_path, "w", encoding="utf-8") as f:
        json.dump(SECTOR_RAW, f, ensure_ascii=False, indent=2)

    # 标记为收盘快照时间
    ts = "2026-08-20 15:00"
    out = build(ov_path, sec_path, out_path, ts=ts)
    print("综合评分: {score}  仓位系数: {position_pct}%  ({position_label})".format(**out))
    print("判定:", out["verdict"])
    print("已写入:", out_path)


if __name__ == "__main__":
    main()
