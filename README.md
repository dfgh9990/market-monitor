# 盘中市场强弱监控 · 动态仓位系数工具

基于六维市场强弱评分（涨跌比广度 / 量能健康度 / 板块资金集中度 / 主力资金流向 / 指数趋势 / 市场情绪温度），
加权合成综合评分并映射成动态仓位系数（0~100%），每 60 分钟自动刷新一次。

## 特性
- **零依赖**：仅使用 Python 标准库（`http.server` + `urllib`），无需 `pip install`
- **服务器端取数**：由后端统一拉取东方财富行情，前端只调自己的 API，彻底规避浏览器跨域（CORS）
- **六维雷达 + 温度计 + 仓位环**：单文件内联前端，响应式适配 PC / 平板 / 手机
- **60 分钟自动刷新** + 倒计时 + 手动刷新；盘中 / 休市自动识别
- 涨红跌绿、¥ 货币、中文界面

## 本地运行
```bash
cd market-monitor
python server.py
# 浏览器打开 http://127.0.0.1:5000
# 自定义端口：PORT=8080 python server.py
```
首次启动会触发一次扫描；若东方财富在您网络环境可用，即为实时数据。
（可选）用真实行情快照演示：`SEED_FILE=seed.json python server.py`

## 一键部署到 Render
1. 把本目录推送到一个 **公开 GitHub 仓库**
2. 点击下面的按钮，或在 [Render](https://render.com) 新建 Web Service 并关联该仓库：

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/<你的用户名>/<仓库名>)

> 把上面链接中的 `<你的用户名>/<仓库名>` 替换成你的实际仓库。
> Render 会自动识别 `render.yaml`，构建并分配一个公网 HTTPS 地址，任何设备浏览器均可访问。

## 部署文件说明
| 文件 | 作用 |
|------|------|
| `server.py` | 零依赖后端：取数 + 六维评分 + 仓位映射 + HTTP 服务 |
| `index.html` | 前端页面（温度计 / 雷达 / 仓位环 / 诊断 / 指数 / 板块 TOP10） |
| `render.yaml` | Render Blueprint 配置（Python 运行时，free 计划） |
| `requirements.txt` | 依赖说明（零依赖，仅为构建步骤通过） |
| `Dockerfile` | 可选容器化部署 |
| `seed.json` / `build_seed.py` | 真实行情快照及生成脚本（演示用，线上自动覆盖） |

## API
- `GET /api/status`：返回当前评分快照（JSON）
- `GET /api/refresh`：强制立即刷新一次（同步返回新快照）
- `GET /api/health`：健康检查

## 免责声明
数据来自公开行情接口，基于量化规则的参考分析，不构成任何投资建议。
市场有风险，投资需谨慎。
