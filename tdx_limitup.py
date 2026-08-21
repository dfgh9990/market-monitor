"""把 tdx_screener(message='连续涨停') 的返回 JSON 落盘为 limitup_raw.json。

用法（自动化中）：
  1. 调用 tdx_screener(message='连续涨停', pageSize='20')
  2. 把返回 JSON 保存为 limitup_raw_mcp.json
  3. python tdx_limitup.py limitup_raw_mcp.json  →  生成 limitup_raw.json

或直接传入：
  python tdx_limitup.py  ← 从 stdin 读 JSON
"""
import json
import sys
import os


def extract_screener_json(raw_text):
    """从 MCP 返回的文本中提取 JSON 块。

    MCP 可能返回 markdown 包裹的 JSON，也可能直接返回 dict。
    """
    if isinstance(raw_text, dict):
        return raw_text
    if isinstance(raw_text, str):
        # 尝试直接 parse
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            pass
        # 尝试从 ```json ... ``` 中提取
        for marker in ("```json", "```"):
            if marker in raw_text:
                start = raw_text.index(marker) + len(marker)
                end = raw_text.index("```", start)
                snippet = raw_text[start:end].strip()
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    continue
        # 尝试找第一个 { ... }
        first = raw_text.find("{")
        last = raw_text.rfind("}")
        if first >= 0 and last > first:
            snippet = raw_text[first:last + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                pass
    return None


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = sys.stdin.read()

    js = extract_screener_json(content)
    if not js:
        print("ERROR: 无法从输入中提取 JSON", file=sys.stderr)
        sys.exit(1)

    data = js.get("data")
    if not data:
        print("ERROR: JSON 中无 data 字段", file=sys.stderr)
        sys.exit(1)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "limitup_raw.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=2)

    total = js.get("meta", {}).get("total", len(data))
    print("OK: limitup_raw.json ({} stocks, meta.total={})".format(len(data), total))


if __name__ == "__main__":
    main()
