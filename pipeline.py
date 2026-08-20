"""管线入口：读取腾讯自选股 MCP 原始 JSON，生成 data.json。

用法（本地/自动化）：
  python pipeline.py overview_raw.json sector_raw.json data.json

也可被自动化直接 import：
  from score_lib import extract_overview, extract_sector, compute
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score_lib import extract_overview, extract_sector, compute


def build(overview_path, sector_path, out_path, ts=None):
    with open(overview_path, "r", encoding="utf-8") as f:
        ov = json.load(f)
    with open(sector_path, "r", encoding="utf-8") as f:
        sec = json.load(f)
    raw = extract_overview(ov)
    raw.update(extract_sector(sec))
    out = compute(raw, ts=ts)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: python pipeline.py overview_raw.json sector_raw.json data.json")
        sys.exit(1)
    out = build(sys.argv[1], sys.argv[2], sys.argv[3])
    print("综合评分: {score}  仓位系数: {position_pct}%  ({position_label})".format(**out))
    print("已写入:", sys.argv[3])
