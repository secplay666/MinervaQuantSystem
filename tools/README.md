# tools：开发辅助工具

这些脚本不属于系统本身，用来在本机检查界面、准备测试数据、搬运大文件。运行环境另外准备（见各节），不要装进项目的 `.venv`。

| 文件 | 用途 | 运行环境 |
|---|---|---|
| `local_scratch.py` | 建一个一次性的本地根目录：复制一份行情库和配置，建演示账号和设置文件。可以加 `--sw-parquet` 补申万行业指数。用来在不碰真实数据的前提下起本地服务、截图检查 | 项目 `.venv` |
| `shots.py` | 登录本地前端（默认 `http://localhost:5666`），依次给指定页面截全页图，打印页面报错 | Playwright 环境（`../tools/pwenv`） |
| `mshots.py` | 手机网页（`/m/`）的截图，视口 390×844 | 同上 |
| `pm_seed.py` | 通过本地接口往演示账号的仓位管家标的库里加几只标的，方便看界面 | 任意 Python 3 |
| `oss_transfer.py`、`oss_download.sh` | 大文件（例如初始数据包）经阿里云 OSS 中转到服务器：本机上传、生成签名链接、服务器限速下载并校验。密钥只从环境变量 `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` 读取，不打印 | `../tools/ossenv`（`oss2`） |

## 本地界面检查的一般步骤

```bash
# 1. 临时根目录（行情库副本、演示账号）
.venv/Scripts/python tools/local_scratch.py ../scratch --catalog data/market.duckdb
# 2. 本地接口和前端（前端开发服务器把 /api 转到 8000 端口）
.venv/Scripts/quant-app --root ../scratch --env-file ../scratch/app.env serve --port 8000 --no-tls
cd web/apps/web-antd && npx -y pnpm@11.16.0 exec vite --mode development --port 5666
# 3. 截图（Git Bash 下要加 MSYS_NO_PATHCONV=1，否则 /moneymap 会被改成 Windows 路径）
MSYS_NO_PATHCONV=1 ../tools/pwenv/Scripts/python tools/shots.py /moneymap /position/board
```

- 演示账号默认 `demo`，密码默认是脚本里的那个本地值，也可以用 `SHOTS_USER` / `SHOTS_PASSWORD` 覆盖。它只存在于临时根目录里，**不要**在生产或开发环境建同名账号。
- 用完删掉临时目录。不要用这些工具去改 `data/` 下的真实数据：本机不跑 `quant-data ingest`，数据只在服务器上更新。

## 不在仓库里的东西

`../tools/` 里还有安卓工具链、各个 Python 环境、语音转写模型、部署包和日志，这些都不入库。
