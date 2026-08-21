"""自动化专用：把生成的数据文件推送到 GitHub 仓库。

仅推送数据文件（不动源码）。Token 从环境变量 GITHUB_PAT 读取。
用法：  GITHUB_PAT=xxx python update_github.py
"""
import os, base64, json, urllib.request, urllib.error

TOKEN = os.environ.get("GITHUB_PAT")
OWNER = "dfgh9990"
REPO = "market-monitor"
BRANCH = "main"
BASE = f"https://api.github.com/repos/{OWNER}/{REPO}/contents"

# 推送清单：数据文件（每轮变化）+ 前端与源码（仅变化时才推送，避免提交刷屏）。
# breadth_raw.json 为通达信实时广度快照；icepoint_state.json 为冰点逆修正连续天数状态（必须持久化）。
# index.html / score_lib.py / pipeline.py 等是站点与评分引擎本体，需随仓库部署到 Cloudflare Pages。
FILES = [
    "data.json", "breadth_raw.json", "overview_raw.json", "sector_raw.json", "icepoint_state.json",
    "limitup_raw.json", "temperature_history.json", "indices_raw.json",
    "index.html", "score_lib.py", "pipeline.py", "tdx_breadth.py", "tdx_limitup.py", "tdx_indices.py",
    "update_github.py", "README.md",
]


def api_req(method, url, data=None):
    req = urllib.request.Request(url)
    req.method = method
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "market-monitor-auto")
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def get_current(path):
    """返回仓库当前文件的内容 sha 与 base64 内容（不存在返回 (None, None)）。"""
    st, resp = api_req("GET", f"{BASE}/{path}?ref={BRANCH}")
    if st == 200 and isinstance(resp, dict) and "content" in resp:
        return resp.get("sha"), resp.get("content")
    return None, None


def upload(path):
    with open(path, "rb") as f:
        content = base64.b64encode(f.read()).decode("ascii")
    sha, remote_b64 = get_current(path)
    # 变化检测：内容一致则跳过，避免每轮无谓提交（尤其 index.html / 源码）
    if sha and remote_b64 and remote_b64.replace("\n", "") == content:
        print(f"SKIP {path} (未变化)")
        return
    body = {"message": "auto update " + path, "content": content, "branch": BRANCH}
    if sha:
        body["sha"] = sha
    st, resp = api_req("PUT", f"{BASE}/{path}", body)
    if st in (200, 201):
        print(f"OK   {path}")
    else:
        print(f"FAIL {path} -> {st}: {str(resp)[:300]}")


if __name__ == "__main__":
    if not TOKEN:
        print("GITHUB_PAT not set"); raise SystemExit(1)
    for f in FILES:
        if os.path.exists(f):
            upload(f)
        else:
            print(f"SKIP {f}")
