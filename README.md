# ItemTalk（识物说）

ItemTalk 是面向数码产品的多模态 AI 产品说明书与操作助手：识别你手里的设备，检索并核验官方资料，生成带引用的动态说明书，并一步一步引导你完成操作。

已具备 P0 与 MVP 的应用代码，可在本地运行“注册 → 上传照片 → 识别确认 → 资料核验 → 引用问答”闭环。真实模型、COS、搜索、正式镜像发布和服务器上线须分别验收；本地 mock 通过不代表生产服务已经可用。

## 本地运行

需要 Python 3.12、uv、Node.js 22.12+ 和带 pgvector 的 PostgreSQL 16。Windows 开发环境也可用 Docker Desktop 启动数据库。

复制 `.env.example` 到本地 `.env`，只在本地文件保存实际凭据。无外部账号时，用 `MODEL_PROVIDER=mock`、`STORAGE_BACKEND=local` 运行演示；Compose 开发环境会固定使用非生产 Cookie 和本地地址。

```powershell
docker compose -f docker-compose.dev.yml --env-file .env up --build
cd frontend
npm ci
npm run dev
```

打开 `http://localhost:5173/`。API 与 Worker 共用 PostgreSQL 队列和对象存储。开发 Compose 自动执行迁移，数据库和上传文件使用独立持久卷。

直接在宿主机运行后端时，从 `backend/` 启动；使用环境变量指定数据库，设置 `ENVIRONMENT=development`、`AUTH_COOKIE_SECURE=false`、`AUTH_COOKIE_PATH=/`、`APP_PUBLIC_URL=http://localhost:5173/`。

```powershell
cd backend
uv sync --locked
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
# 在第二个终端、同一环境启动 Worker
uv run python -m app.worker
```

## 验证

`TEST_DATABASE_URL` 必须指向**专用且可清空**的测试库。集成测试会清空其中的业务数据；缺少该配置时，PostgreSQL 用例会显式跳过。

```powershell
cd backend
uv run ruff check .
uv run ruff format --check .
uv run alembic upgrade head
uv run pytest
cd ../frontend
npm run lint
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

E2E 使用独立测试库，先启动 mock API 和 Worker。CI 对前端、后端迁移/约束及浏览器闭环分别设置门禁；测试图片和模型结果均为明确标识的演示数据。截图输出在 `frontend/test-results/`，不提交。

## 真实服务验证

已确定文本/视觉分别配置为 `qwen3.7-flash`，向量为 `text-embedding-v4`、1024 维，语音为 `qwen3-asr-flash`，搜索为 Bocha。代码中的百炼 provider 名沿用 `dashscope`。查询和文档索引必须使用相同模型与修订；更换修订不覆盖已有向量，由后续受控重建处理。

本地真实配置位于 Git 忽略的 `backend/.env`，从 `backend/` 启动即可加载。Compose 使用该文件时显式传入 `--env-file backend/.env`。文本使用 `MODEL_BASE_URL/MODEL_API_KEY/CHAT_MODEL`，视觉使用 `VISION_*`，向量使用 `EMBEDDING_*`，搜索使用 `SEARCH_*`，ASR 使用 `STT_PROVIDER/STT_BASE_URL/STT_API_KEY/STT_MODEL`。各角色保留独立超时；429/5xx 最多重试至配置的总尝试次数，流式输出开始后、网络超时或断线后不自动重放。

转写通过本人已确认的音频资产调用 `POST /api/v1/assets/{id}/transcribe`（需幂等键）。它只返回可编辑文本与 `requiresConfirmation=true`，不创建用户消息或问答运行。前端录音转换成 16kHz 单声道 WAV，最长 60 秒；用户确认文字后放入提问框，再主动发送。未配置 STT 时入口隐藏，能力通过 `GET /api/v1/capabilities` 查询。

真实付费 smoke 必须显式启用：

```powershell
cd backend
uv run python -m app.cli smoke --paid --image <本地铭牌图片路径> --audio <本地音频路径>
```

该命令只输出角色验证结果，不打印密钥或识别内容。正式部署前还须验证 COS 上传/下载/确认不可覆盖、真实铭牌匹配、来源/地区/页码及检索 Gold Set。

同时启动多个本地回归实例时，Vite 可通过 `DEV_API_PROXY_TARGET` 指定独立 API 地址，Playwright 可通过 `E2E_BASE_URL` 指定独立前端地址；这些变量不包含供应商密钥，也不进入生产模型配置。

核验人员准备 Gold Set JSON 后，可运行 `uv run python -m app.evaluate <数据集路径> --paid`。每项包含型号 ID、问题、预期文档版本 ID、页码及地区/硬件修订，输出 Recall@10、页码/适用性和无依据指标。mock 分数仅检查评测工具，不作为真实质量结论。

公共来源使用独立可信域名注册表，由运营人员核验品牌官网后维护：

```powershell
uv run python -m app.cli trust-brand --name <品牌> --domains <官方域名>
uv run python -m app.cli register-source --brand <品牌> --url <官方资料链接>
```

用户确认只创建个人实例。研究任务下载公共官方资料，解析页先暂存；型号、来源和资料版本均通过核验后才发布知识片段。私有照片不进入公共知识库。来源撤销后停止新检索，已有引用保留并显示状态变更。

登记来源仅加入待核验 URL，不发布知识。链接须属于该品牌已登记的官方域名并通过公网地址检查；撤销的来源不能借登记恢复。搜索漏召回时，研究任务可使用这些候选来源，仍须独立核验正文中的型号、地区和硬件版本。

搜索漏召回时可以复用已登记的官方来源，但来源路径须明确匹配型号，且仍走下载、解析、精确适用性和三级发布核验。不得把搜索摘要或非官方站点当作已核验证据。开发机 Fake-IP DNS 如解析到保留地址，默认 SSRF 检查会拒绝；可显式使用 `FETCH_DNS_PROVIDER=cloudflare`，通过加密 DNS 获取地址后仍逐跳检查公网 IP、固定连接并验证 TLS，生产默认保持 system。

## 发布与回滚

生产共用服务器入口为 `/itemtalk/`，Caddy 只绑定环回 8400；Nginx 剥离前缀并关闭 SSE 缓冲。前端 base、API 地址和 Cookie Path 应保持一致。独立 HTTPS Compose 仅适用于独占 80/443 的服务器。

服务器试用使用 `ENVIRONMENT=staging`、`STORAGE_BACKEND=local`，上传文件持久化到 `/opt/itemtalk/data/assets`，API 和 Worker 共用该目录。试用仍要求强 JWT 密钥、Secure Cookie、HTTPS 和真实模型；正式生产继续强制 COS。当前 Actions 部署环境名为 `staging`。

向 `main` 提交代码后，CI 全部通过才会构建和推送对应 SHA 的镜像，再通过 SSH 发布到服务器。仓库变量为 `DEPLOY_HOST`、`DEPLOY_USER`，Secrets 为 `DEPLOY_SSH_PRIVATE_KEY`、`DEPLOY_KNOWN_HOSTS`；供应商密钥只保存在服务器 `.env.production`。GHCR 拉取使用本次 Actions 的临时 Token，发布结束后注销。

生产需要受限的 `.env.production`、独立应用/迁移数据库角色、固定 SSH host key、只读 GHCR 凭据，以及已验证的备份恢复点。不得在终端输出完整 Compose 配置或把生产环境文件放入仓库。

部署工作流仅构建成功 main CI 对应的完整 SHA，发布脚本使用固定 `sha-<SHA>` 镜像，保存迁移 revision、镜像摘要和发布前备份。健康/版本检查成功后切换 `current`；失败恢复上一发布应用，不自动降级数据库。首次部署失败则停止新应用，并撤销新 Nginx 路由。

`infra/backup.sh` 和 `infra/check-health.sh` 分别提供数据库备份检查、HTTPS/证书到期检查；相应 systemd timer 需经确认后安装。监控非零退出应接入运维告警。备份归档结构验证不能替代实际恢复演练。

试用版备份同时保存数据库 dump、持久化上传文件归档及校验和。维护窗口内运行备份可避免并发清理影响数据库与文件的一致性；恢复时应配对还原这些文件。

可在拥有建库权限的隔离环境运行 `uv run python -m app.restore_check`。命令使用一致性快照导出，在本次新建的 `itemtalk_restore_*` 数据库中恢复，核对所有表的 SHA-256 内容摘要、扩展和约束后清理临时库，并保留受限备份及校验文件。Windows 可通过 `--pg-bin <PostgreSQL bin 目录>` 指定客户端。生产执行同样属于需要确认的服务器变更，演练必须在预留容量内进行。

服务器部署、Nginx 修改和定时器安装都须先确认具体方案。代码实现和本地验证不会自动改变现有服务器。
