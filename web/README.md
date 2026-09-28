# Minerva 前端（PC 网页 + 安卓 App）

基于 [vue-vben-admin](https://github.com/vbenjs/vue-vben-admin) 5.7.0（上游提交 `c5204a6`，MIT 许可，见 [LICENSE](LICENSE)）。

相对上游做了以下裁剪：

- 只保留 Ant Design Vue 版应用 `apps/web-antd`；
- 去掉了其他 UI 版本、mock 后端、文档站、演示页面和 git 钩子（lefthook）。

设计见仓库根目录的 [ADR-010](../docs/adr/ADR-010-frontend-api-access.md) 和 [应用设计](../docs/design/stage4-app-design.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `apps/web-antd` | PC 网页，包括首页、每日决策与审核、账户、市场与个股、通知、数据健康、系统管理、个人设置 |
| `apps/web-antd/src/api/minerva.ts` | 后端 `/api/v1` 的类型与调用 |
| `apps/web-antd/src/router/routes/modules/minerva.ts` | 菜单与权限码（`meta.authority`） |
| `packages/`、`internal/` | vben 的共享包与构建配置（上游原样） |

## 开发

需要 Node 24.12 及以上、pnpm 11，用 `npx pnpm@11.16.0` 调用即可，不必全局安装。

```bash
npx pnpm@11.16.0 install
# 另开终端启动后端（不启用 TLS，端口 8000）：quant-app serve --no-tls --port 8000
npx pnpm@11.16.0 -F @vben/web-antd run dev        # http://localhost:5666，/api 代理到 127.0.0.1:8000
npx pnpm@11.16.0 -F @vben/web-antd exec vue-tsc --noEmit --skipLibCheck
npx pnpm@11.16.0 run build:antd                    # 产物 apps/web-antd/dist
```

## 部署

构建产物由后端同源托管：设置 `MINERVA_WEB_DIR=<仓库>/web/apps/web-antd/dist` 后运行 `quant-app serve`。

- 前端使用 hash 路由，所以服务器不需要额外的路由配置。
- 接口地址是同源的 `/api/v1`。

## 约定

- **颜色**：红涨绿跌（A 股习惯），见 `src/utils/format.ts`。
- **单位**：金额以"分"传输，界面上换算为元。
- **复权**：K 线默认前复权，只用于展示（ADR-004）。
- **权限**：由后端决定。前端菜单只是按权限码隐藏，不构成安全边界。
