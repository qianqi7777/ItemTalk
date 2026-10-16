# ItemTalk（识物说）

ItemTalk 是面向数码产品的多模态 AI 产品说明书与操作助手：识别你手里的设备，检索并核验官方资料，生成带引用的动态说明书，并一步一步引导你完成操作。

已具备 P0 与 MVP 的应用代码，可在本地运行“注册 → 上传照片 → 识别确认 → 资料核验 → 引用问答”闭环。真实模型、COS、搜索、正式镜像发布和服务器上线须分别验收；本地 mock 通过不代表生产服务已经可用。

V1 正在验收：已接入版本化说明书、回答反馈、知识缺口、历史会话与产品切换、需用户确认的私有记忆、结构化操作引导、运行角色 RLS、账号/实例完整清理及个性化手册。真实供应商质量、配对恢复及维护窗口上线验收须分别完成。

资料构建完成后，Worker 自动生成带来源和页码的公共说明书。已完成章节不能覆盖；补充资料创建新版本，部分失败时保留旧可用版本。用户可查看章节、重试未完成任务和修改本人回答反馈。

图像识别要求型号有可见文字或用户明确输入支持，无法读到完整型号时要求补拍。销售地区和硬件修订分别检查对应标签，制造地、版权年份与固件号不能代替；候选始终需要用户确认，不直接作为已核验资料。

用户明确表达偏好或个人设置时，后台只提取待确认记忆，页面“我的记忆”可确认或删除。未确认、过期或删除的记录不进入回答；产品设置按实例隔离，只有本人全局偏好可跨本人产品使用。候选提取任务不会把私有内容写入公共知识缺口。

操作引导每次只展示一个动作及核验引用、前提和成功条件；高风险请求直接停止，不提供勾选解除。反馈包含当前步骤、版本与幂等键，澄清不推进，失败进入有证据的排查分支。照片只描述观察，不能代替官方证据或自动判定动作完成。

问答与操作引导使用相同的风险规则，规范化空白、全角及大小写；明确危险的文字在附件读取或模型调用前拒绝。规则仍是有限检查，不能保证识别全部危险表达。问答缺少有效引用时降级说明资料不足，不保留未引用的模型猜测。

有引用的事实回答会增加一次引用支持核验，逐条判断动作、条件与参数是否对应，并要求摘录确实来自所引片段。多余引用删除后证据仍充分才保留回答，否则说明资料不足；发布前重查来源状态和正文。核验判断仍来自模型，不能代替全面语义质量验收。普通有引用问答通常消耗向量、回答、核验三次模型调用，重试与记忆提取另按实际调用计入预算；无引用、明确资料不足或危险请求不会额外调用引用核验。

官方资料向量化按最多十个片段分批保存，通过来源、型号和版本核验后才发布。长手册不因超过一百个片段而截断；失败重试复用正文、页码与模型修订一致的已完成向量，保留原引用 ID。全部批次完成才将构建标记就绪，每日额度不足仍会中断，不自动提高额度。

引导采用受限 DeepAgents 规划与 LangGraph 等待节点，等待点和业务状态在同一 PostgreSQL 事务中提交。暂停后可重新打开页面恢复当前步骤；只允许本人加载检查点。暂停恢复点保留，终态七天后通过 Worker 清理，不影响其他引导。

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

## 说明书与数据删除

说明书支持通用版和仅本人可见的实例版本。个人版本冻结已确认且未过期的设置与偏好；官方正文仍只来自核验证据，个人设置补充另行标注为非官方证据，并检查记忆归属和危险操作。撤销记忆后不再展示相应补充，生成过程遇到输入变化会停止发布并要求新版本。全部核验片段按资料/页码分组进入有界章节，资料更新后复用输入未变化的已完成章节，也可选择未完成章节单独补全；旧版本在新版本完整前保持可读。

私有数据可从页面删除账号、产品、会话和未被引用的照片。删除先屏蔽访问并取消私有任务，Worker 分批清理对象后按外键顺序删除记录；仍被其他内容引用的照片会保留，公共知识与公共构建不受影响。临时 PUT 签名有效期内保留清理依据，签名到期后再删除文件，避免旧签名重新创建对象。上传确认独立记录对象预留，主事务回滚产生的孤儿文件也会被定时清理。账号完成清理后不保留个人身份、私有任务 payload 或 Agent 检查点。

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

核验人员准备 Gold Set JSON 后，可运行 `uv run python -m app.evaluate <数据集路径> --paid`。每项包含型号 ID、问题、预期文档版本及片段 ID、页码和地区/硬件修订，输出文档/片段 Recall@10、MRR@10、页码/适用性及检索为空指标。检索为空不代表模型会拒答，mock 分数仅检查评测工具。

加 `--answers` 才实际生成回答并统计引用有效性、人工标注引用目标、关键事实和无依据拒答。`expectedFacts` 每组是同一事实的文字备选值，`forbiddenFacts` 标记不应出现的错误事实；`allowedCitationChunkIds` 可注明其他同样支持事实的片段，避免重复章节被误报。加 `--report <新文件>` 可保存受限详细报告供人工核对逐句支持与安全；命令不覆盖已有报告。自动字面/引用指标与单一产品样例不能替代全面质量验收，真实模型和向量请求均消耗供应商额度。

额度或供应商中断时，评测停止后续调用，保留已经完整评完的样例；报告明确标记 `evaluationStatus=interrupted`、请求数和完成数，命令非零退出。未完成样例不混入指标分母，部分报告不能作为整套验收通过依据。

公共来源使用独立可信域名注册表，由运营人员核验品牌官网后维护：

```powershell
uv run python -m app.cli trust-brand --name <品牌> --domains <官方域名>
uv run python -m app.cli register-source --brand <品牌> --url <官方资料链接>
```

用户确认只创建个人实例。研究任务下载公共官方资料，解析页先暂存；型号、来源和资料版本均通过核验后才发布知识片段。私有照片不进入公共知识库。来源撤销后停止新检索，已有引用保留并显示状态变更。

登记来源仅加入待核验 URL，不发布知识。链接须属于该品牌已登记的官方域名并通过公网地址检查；撤销的来源不能借登记恢复。搜索漏召回时，研究任务可使用这些候选来源，仍须独立核验正文中的型号、地区和硬件版本。

搜索漏召回时可以复用已登记的官方来源，但来源路径须明确匹配型号，且仍走下载、解析、精确适用性和三级发布核验。不得把搜索摘要或非官方站点当作已核验证据。开发机 Fake-IP DNS 如解析到保留地址，默认 SSRF 检查会拒绝；可显式使用 `FETCH_DNS_PROVIDER=cloudflare`，来源登记和资料下载使用相同解析配置，通过加密 DNS 获取地址后仍逐跳检查公网 IP、固定连接并验证 TLS，生产默认保持 system。

## 发布与回滚

生产共用服务器入口为 `/itemtalk/`，Caddy 只绑定环回 8400；Nginx 剥离前缀并关闭 SSE 缓冲。前端 base、API 地址和 Cookie Path 应保持一致。独立 HTTPS Compose 仅适用于独占 80/443 的服务器。

服务器试用使用 `ENVIRONMENT=staging`、`STORAGE_BACKEND=local`，上传文件持久化到 `/opt/itemtalk/data/assets`，API 和 Worker 共用该目录。试用仍要求强 JWT 密钥、Secure Cookie、HTTPS 和真实模型；正式生产继续强制 COS。当前 Actions 部署环境名为 `staging`。

向 `main` 提交代码后，CI 全部通过才会构建和推送对应 SHA 的镜像，再通过 SSH 发布到服务器。仓库变量为 `DEPLOY_HOST`、`DEPLOY_USER`，Secrets 为 `DEPLOY_SSH_PRIVATE_KEY`、`DEPLOY_KNOWN_HOSTS`；供应商密钥只保存在服务器 `.env.production`。GHCR 拉取使用本次 Actions 的临时 Token，发布结束后注销。

生产需要受限的 `.env.production`、独立 API/Worker/迁移数据库角色、固定 SSH host key、只读 GHCR 凭据，以及已验证的备份恢复点。API 使用 `itemtalk_app`，Worker 使用 `itemtalk_worker` 和独立 `DB_WORKER_PASSWORD`；运行账号无表所有权、超级用户或 BYPASSRLS 权限。私有表强制 RLS，事务内绑定已验证用户，Worker 通过受限队列函数领取任务后绑定任务所有者。不得在终端输出完整 Compose 配置或把生产环境文件放入仓库。

0007 启用 RLS 会改变旧应用的认证和数据库权限，当前发布标记为 `requires-maintenance`。CI 可以正常测试；Deploy 在构建发布镜像和连接服务器之前跳过自动部署。已有服务器须在确认的维护窗口中暂停 API/Worker、保存数据库与文件配对备份、用管理员预置 `infra/runtime-roles.sql` 中的角色，再应用迁移并验证新服务；旧应用回滚需配对恢复备份，不能只切换镜像。新建开发数据库由 Compose 初始化角色；已有开发数据库也须先预置角色。

部署工作流仅构建成功 main CI 对应的完整 SHA，发布脚本使用固定 `sha-<SHA>` 镜像，保存迁移 revision、镜像摘要和发布前备份。健康/版本检查成功后切换 `current`；失败恢复上一发布应用，不自动降级数据库。首次部署失败则停止新应用，并撤销新 Nginx 路由。

`infra/backup.sh` 和 `infra/check-health.sh` 分别提供数据库备份检查、HTTPS/证书到期检查；相应 systemd timer 需经确认后安装。监控非零退出应接入运维告警。备份归档结构验证不能替代实际恢复演练。

试用版备份同时保存数据库 dump、持久化上传文件归档及校验和。维护窗口内运行备份可避免并发清理影响数据库与文件的一致性；恢复时应配对还原这些文件。

可在拥有建库及恢复对象所有权权限的隔离环境运行 `uv run python -m app.restore_check`。命令使用一致性快照导出，在本次新建的 `itemtalk_restore_*` 数据库中恢复，核对所有表的 SHA-256 内容摘要、扩展、约束、RLS 策略、对象所有权和守卫函数执行权限后清理临时库，并保留受限备份及校验文件。恢复环境须预置同名数据库角色；导出保留 owner 和 ACL，避免恢复后丢失安全边界。Windows 可通过 `--pg-bin <PostgreSQL bin 目录>` 指定客户端。生产执行同样属于需要确认的服务器变更，演练必须在预留容量内进行。

本地文件存储的完整配对演练需先暂停 API、Worker 及其他写入/清理入口，然后运行 `uv run python -m app.restore_check --assets-directory <资产根目录> --writers-paused --output-directory <资产目录外的备份目录>`。命令配对导出 dump、文件归档和摘要清单，再在新数据库及空目录中实际恢复，核验全部文件字节与已确认资产的数据库哈希/大小；缺失、损坏、链接或路径越界会失败。演练结束清理临时目标，保留备份、受限清单及校验和；此模式只验收本地存储，不代替 COS 对象恢复。

COS 可用 `uv run python -m app.restore_check --cos-assets --writers-paused --output-directory <受限备份目录>` 配对备份数据库及当前对象。工具只读数据库登记的资产/清理对象，不遍历共享桶；已确认资产缺失或哈希不符失败，未上传临时对象的缺失状态记录并重验。实际恢复到新数据库及隔离本地目录，报告明确 `assetRestoreTarget=local-isolated`。此结果不代替目标 COS 回写与新存储读取验收，也不包含桶版本历史或未登记孤儿对象；恢复演练需要 PostgreSQL 客户端、数据库管理员及充分磁盘容量。

从服务器试用的本地存储改为正式 COS 前，可用 `uv run python -m app.storage_transfer --assets-directory <原资产目录> --writers-paused --report <新报告文件>` 生成复制计划。实际写入需另加 `--apply`，且先按 [维护清单](infra/MAINTENANCE.md)确认备份和停写方案。工具逐文件核对 SHA-256 与大小、复用同内容目标、拒绝覆盖冲突，不改源数据库/文件或桶权限；目标桶须满足私有访问和无版本保留的前置条件。

服务器部署、Nginx 修改和定时器安装都须先确认具体方案。代码实现和本地验证不会自动改变现有服务器。

维护版本由 `Maintenance images` 工作流准备镜像：仅接受本仓库 `main` 的成功 push CI（手动运行须提供完整 SHA），源提交须包含维护打包工具且标记为 `requires-maintenance`。工作流发布并实际拉取 API、前端、数据库和独立备份镜像，核对各镜像源码 SHA，生成摘要引用、目标迁移 revision 和受校验和保护的发布配置包；它没有 SSH 或服务器部署步骤。维护操作采用产物 `image-overrides.env` 中的 `API_IMAGE`、`DB_IMAGE`、`FRONTEND_IMAGE`、`BACKUP_IMAGE` 摘要引用，与服务器受限生产配置分别加载。产物保留 30 天，上线前须保存审核后的副本；镜像发布成功不代表 COS、备份恢复或服务器上线已通过。

COS 定时备份使用独立 `backup` 运维容器，包含 PostgreSQL 16 客户端、管理员数据库连接、只读根文件系统和专用备份目录，不向 API/Worker 传递管理员权限，也不调用模型。`infra/backup.sh daily` 导出一致性快照和只读资产，上传到独立备份桶后逐文件下载核对 SHA-256；`weekly` 从 COS 重下已完成备份，恢复到独立临时数据库/目录验证。共享部署锁及独占备份锁防止维护或备份重叠。在线快照不暂停普通写入，快照之后的数据不在恢复点内；原对象缺失或变化导致失败，不能代替迁移前的停写备份。

运行前须为 `/opt/itemtalk/backups` 准备与 `BACKUP_UID/BACKUP_GID` 一致的受限所有权，确认 DB 已运行并预留恢复空间。`BACKUP_COS_SECRET_ID/KEY` 只读应用资产；独立 `DB_BACKUP_COS_*` 从服务器受限 `.env.backup` 加载，不回退到应用桶。已上传核验的本地备份保留 7 天，COS 长期保留且无自动删除接口。默认 `BACKUP_JOBS_ENABLED=false`、告警渠道未连接。详细配置、重试状态、真实测试入口和部署/回滚要求见 [备份与告警方案](infra/BACKUP_AND_ALERTS.md)及 [配置模板](infra/backup.env.example)。Mock 测试、真实服务待验收和生产未启用必须分别记录。

上线前按 [维护手册](infra/MAINTENANCE.md)审核环境变量、固定镜像、停写备份及回滚步骤。可选SMTP后备 `python -m app.ops_mail --env-file <受限文件>` 默认只检查配置，明确真实测试开关、参数及新报告齐备后才单次投递；SMTP接受仍不能证明邮件送达。生产启用及真实测试必须分别确认。

数据库备份专用子用户使用 [最小权限策略模板](infra/cos-backup-policy.example.json)；对象读写只授权备份前缀，桶配置只读，显式拒绝删除、桶配置写入与对象ACL更改。真实凭据仅在本地/服务器受限文件，绑定及更新密钥后再单独确认真实测试。

开发过程记录、配置指导、阶段验收报告及针对当前环境的上线审核计划统一存放于 Git 忽略的 `ignored-docs/`，不提交。仓库保留应用使用说明、通用运维手册、无密钥配置模板及可执行代码。

真实 COS 供应商预检可在本地 `backend` 运行 `uv run python -m app.cos_probe --real-cos --origin https://49.233.65.195 --report ../.cache/cos-probe-<本轮唯一标识>.json`。需先在本地 `.env` 填入 COS 四个变量，报告独占创建；该命令只为本轮新建合成图片对象，核对私有桶、前端 Origin 的 PUT/GET 与 Content-Type CORS、签名上传/读取、临时键覆盖不影响私有副本，以及本地归档恢复字节到 COS。清理前核对对象 nonce，不删归属不符的对象；强制退出或清理失败须依据受限报告人工核对残留。程序无数据库/模型调用，SDK和HTTP响应不输出到日志、响应限4KiB、15秒超时且无自动重试；报告只能证明供应商协议，应用权限和数据库配对恢复另需验收。
