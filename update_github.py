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

# 仅推送数据文件（源码由人工维护）
FILES = ["overview_raw.json", "sector_raw.json", "data.json"]


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


def get_sha(path):
    st, resp = api_req("GET", f"{BASE}/{path}?ref={BRANCH}")
    if st == 200 and isinstance(resp, dict):
        return resp.get("sha")
    return None


def upload(path):
    with open(path, "rb") as f:
        content = base64.b64encode(f.read()).decode("ascii")
    sha = get_sha(path)
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
