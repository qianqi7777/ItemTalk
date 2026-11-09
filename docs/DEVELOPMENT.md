# 开发指南

[← 项目首页](../README.md) · [架构](ARCHITECTURE.md) · [配置](CONFIGURATION.md) · [发布与维护](../infra/MAINTENANCE.md)

## 目录

- [环境与目录](#环境与目录)
- [本地启动](#本地启动)
- [数据库角色与迁移](#数据库角色与迁移)
- [检查与测试](#检查与测试)
- [真实服务与评测](#真实服务与评测)
- [提交与文档维护](#提交与文档维护)
- [常见开发问题](#常见开发问题)

## 环境与目录

| 工具              | 要求 / 用途                                       |
| ----------------- | ------------------------------------------------- |
| Node.js           | 22.12+，前端开发与构建                            |
| Python / uv       | Python 3.12；`uv sync --locked` 安装后端锁定依赖  |
| PostgreSQL        | 16，pgvector 0.8.0+、pg_trgm                      |
| Docker Compose    | 推荐用于本地数据库、API 和 Worker                 |
| PostgreSQL 客户端 | 恢复测试需要匹配的 `pg_dump`、`pg_restore` 等工具 |

```text
ItemTalk/
├── frontend/            React 界面、单测、Playwright
├── backend/
│   ├── app/             API、Worker、业务服务、供应商适配
│   ├── alembic/         版本化数据库迁移
│   └── tests/           单元、集成、权限与恢复测试
├── docs/                使用、开发、配置和架构说明
├── infra/               Compose 配套、运行管理、发布与备份工具
├── .github/workflows/   CI、Deploy、Maintenance images
└── .env.example         无密钥应用配置模板
```

## 本地启动

推荐先按[首页快速开始](../README.md#快速开始)启动完整开发 Compose，再在宿主机启动 Vite。开发服务默认仅绑定环回地址：前端 5173、API 8000、数据库 5432。

### 直接运行宿主机后端

先准备好数据库、扩展和独立角色。若使用开发 Compose 数据库，在仓库根目录执行：

```powershell
docker compose -f docker-compose.dev.yml --env-file .env up -d db
```

在 `backend/` 创建本地 `.env`。它与根目录供 Compose 使用的 `.env` 是不同文件。以下仅适用于本地开发；密码须与实际数据库一致：

```dotenv
ENVIRONMENT=development
MODEL_PROVIDER=mock
STORAGE_BACKEND=local
AUTH_COOKIE_SECURE=false
AUTH_COOKIE_PATH=/
PUBLIC_PATH_PREFIX=
APP_PUBLIC_URL=http://localhost:5173/
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=itemtalk
DB_USER=itemtalk_app
DB_PASSWORD=devpassword
```

从仓库根目录的 PowerShell 终端进入后端，先用迁移身份升级，再切回受限 API 身份：

```powershell
cd backend
uv sync --locked
$env:DB_USER = "itemtalk_migrator"
uv run alembic upgrade head
$env:DB_USER = "itemtalk_app"
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

另开终端，从 `backend/` 使用独立 Worker 身份启动：

```powershell
$env:DB_USER = "itemtalk_worker"
uv run python -m app.worker
```

若各角色密码不同，在各自终端同步配置正确的 `DB_PASSWORD`。显式 `DATABASE_URL` 优先于 `DB_*`，不要保留指向其他库的旧值。不要同时启动占用相同端口的 Compose API 和宿主机 API。

## 数据库角色与迁移

| 角色                | 职责                                 |
| ------------------- | ------------------------------------ |
| 数据库管理员        | 初始化扩展、角色、备份恢复与权限准备 |
| `itemtalk_migrator` | 执行 Alembic、拥有迁移创建的对象     |
| `itemtalk_app`      | API 运行，受限访问并执行 RLS         |
| `itemtalk_worker`   | 独立 Worker 运行，按任务归属访问     |

新建开发库由 [`init-extensions.sql`](../init-extensions.sql)、[`init-roles.sh`](../infra/init-roles.sh) 和 [`runtime-roles.sql`](../infra/runtime-roles.sql) 初始化。Docker 初始化脚本只在空数据目录首次运行；已有库须由管理员补齐角色、扩展与授权，再运行迁移，不能仅重启容器期待初始化重跑。

当前迁移头为 `0020_supplemental_index`，以后以 `uv run alembic heads` 为准。迁移不会自动升级 pgvector 扩展；`0011` 要求 0.8.0+。不要用删除持久卷解决旧库升级问题。

## 检查与测试

### 后端

从 `backend/` 执行：

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

**PostgreSQL 集成测试会清空测试库业务数据。** 只将 `TEST_DATABASE_URL` 指向专用可重建库，预先按 [CI](../.github/workflows/ci.yml) 建立扩展、运行角色和迁移。不要指向日常开发库、服务器库或包含用户数据的库。

测试库使用管理员身份准备扩展/角色，迁移使用 `itemtalk_migrator`，测试连接需具备测试清理所需权限。缺少 `TEST_DATABASE_URL` 时，相关用例显式跳过；有跳过的运行不等于完整数据库集成验证。恢复用例可通过 `TEST_PG_BIN` 指定 PostgreSQL 客户端目录。

### 前端

从 `frontend/` 执行：

```powershell
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

### 浏览器

```powershell
npx playwright install chromium
npm run test:ui
```

`test:ui` 使用内存 API 响应，可独立验证响应式界面、历史、来源标识和模型配置等交互，不代表真实 API 闭环。

`npm run test:e2e` 还包含真实 API/Worker 流程。运行前须准备独立 E2E 库、扩展和角色，完成迁移，以 mock 模型启动 API 与 Worker，并按 CI 设置 `V2_ENABLED`、管理员迁移连接和测试额度。完整初始化步骤以[工作流](../.github/workflows/ci.yml)为准，不要对生产服务运行 E2E。

| 变量                   | 用途                                              |
| ---------------------- | ------------------------------------------------- |
| `DEV_API_PROXY_TARGET` | Vite 转发到独立 API，默认 `http://127.0.0.1:8000` |
| `E2E_BASE_URL`         | 独立测试前端地址                                  |
| `E2E_EXTERNAL_SERVER`  | 使用已经启动的前端，不由 Playwright 启动 Vite     |
| `PLAYWRIGHT_CHANNEL`   | 使用本机特定浏览器通道，例如 `msedge`             |

截图与 trace 输出在 `frontend/test-results/`，不提交到仓库。

## 真实服务与评测

真实模型和搜索请求可能计费。先在受限本地配置中准备服务地址、权限与预算，再从 `backend/` 显式运行：

```text
uv run python -m app.cli smoke --paid --image <本地图片路径> --audio <本地音频路径>
uv run python -m app.evaluate <Gold-Set.json> --paid
uv run python -m app.evaluate <Gold-Set.json> --paid --answers --report <新的受限报告文件>
```

Smoke 输出角色验证结果，不打印密钥和识别内容。Gold Set 格式见 [`GoldCase`](../backend/app/evaluate.py)：包含型号、问题、期望文档/片段、页码、地区/硬件修订，也可给出关键事实与禁止事实。

默认评估检索；`--answers` 才生成回答并检查引用与事实。`expectedFacts` 每组是一项事实的文字备选值，`allowedCitationChunkIds` 接受同样支持事实的其他片段。报告不覆盖已有文件。供应商中断时保存完整评完的样本，标记 `interrupted` 并非零退出；不能把部分结果当整套通过。自动指标和单一设备不能代替人工质量验收。

## 提交与文档维护

1. 从当前主分支创建独立分支，保持改动范围清楚。
2. 修改功能时同步更新对应公开文档，命令、配置名与边界以代码为依据。
3. 运行相关检查，在 PR 中记录验证方式和实际限制。
4. CI 通过后按仓库流程合并；合并、镜像发布、服务器部署是不同阶段。

文档入口由 [README](../README.md#文档导航)维护；详细内容各归其位，使用相对链接，统一一个一级标题、清晰的小节、带语言的代码块和适合比较的表格。保持现有 `infra/` 链接可用。

`.env`、凭据、私有照片、对话、部署回执、设计过程和验收报告不进公开仓库；过程文件使用被忽略的 `ignored-docs/`。不要把当前服务器配置或固定模型版本写成所有安装环境的事实。

## 常见开发问题

| 现象                       | 检查方式                                                   |
| -------------------------- | ---------------------------------------------------------- |
| 页面无法访问 API           | API 是否在 8000 启动、Vite 代理是否正确                    |
| 任务持续等待               | Worker 是否运行，DB/文件目录和配置是否与 API 一致          |
| 登录后仍未登录             | 本地 Cookie Secure/Path、Origin 与公开地址是否匹配         |
| 迁移报角色不存在或权限不足 | 是否首次初始化、迁移身份是否正确、旧库是否补齐授权         |
| pgvector 版本不满足        | 独立检查扩展版本，审核升级，不绕过迁移检查                 |
| 单测通过但大量 skipped     | 是否遗漏专用测试库或恢复客户端                             |
| `.env` 已改但容器没变化    | Compose 是否映射该变量；环境变化后需按开发流程重建相关容器 |

模型、额度与资料网络问题见[配置诊断](CONFIGURATION.md#故障定位)。
