<div align="center">

# 识物说 · ItemTalk

**从照片，到有据可查的设备答案。**

拍照识别 · 型号确认 · 资料检索 · 设备问答 · 操作引导

[![CI](https://github.com/qianqi7777/ItemTalk/actions/workflows/ci.yml/badge.svg)](https://github.com/qianqi7777/ItemTalk/actions/workflows/ci.yml)

[快速开始](#快速开始) · [使用指南](docs/USAGE.md) · [开发指南](docs/DEVELOPMENT.md) · [模型配置](docs/CONFIGURATION.md) · [架构说明](docs/ARCHITECTURE.md)

</div>

---

ItemTalk 是面向数码设备的多模态助手。它结合照片、文字和产品资料判断设备型号，在用户确认后检索说明书、建立资料索引，并回答具体使用问题。

答案会区分资料依据：官方资料、第三方补充资料，以及明确标注为 **“模型推测 · 未经资料核验”** 的参考回答。识别候选、用户确认和来源核验是不同阶段，不会相互替代。

## 能做什么

| 场景           | 当前能力                                                                       |
| -------------- | ------------------------------------------------------------------------------ |
| 不知道设备型号 | 先看外观与可见文字，再结合产品资料给出候选；可选择、排除或继续补拍             |
| 想找说明书     | 检索官网与支持页面，核验型号适用性；官方不可用时尝试第三方补充索引             |
| 只有扫描 PDF   | 普通页面直接提字，扫描页调用已配置图像模型转写，保留页码并缓存成功页面         |
| 想问具体操作   | 优先查资料，引用核验后回答；资料不足时继续联网，仍不足则给出带标识的模型推测   |
| 想一步一步操作 | 每步展示动作、前提、成功条件和核验引用，支持反馈、暂停与恢复                   |
| 想保留个人设置 | 待确认记忆由本人决定保留或删除，未确认和已失效内容不用于个性化回答             |
| 服务额度用完   | 管理员可分别测试并切换文本、图像、语音、向量服务，新任务采用新配置             |
| 手机或电脑使用 | 桌面模块工作区、手机任务主区、来源与工具面板，支持轻玻璃效果和减少动态效果偏好 |

> 扫描页每份说明书最多处理 **20 页**，超限会明确提示。外观识别和模型推测仍可能出错；第三方资料不会显示成官方已核验资料。

## 一次使用流程

```text
拍照 / 上传图片与文字
        ↓
外观分析 → 查看候选 → 确认型号 / 排除候选后补拍
        ↓
检索说明书 → 核验适用性 → 建立资料索引
        ↓
设备问答 / 查看说明书 / 管理记忆 / 操作引导
```

拍不到铭牌时也可以开始识别。系统会说明匹配点、疑点和建议补拍的位置；用户确认型号后仍要独立核验资料来源、地区和硬件版本。

## 快速开始

本地演示需要 **Docker Compose** 和 **Node.js 22.12+**。宿主机直接运行后端时另需 Python 3.12、uv 和 PostgreSQL 16；数据库需要 pgvector 0.8.0+ 与 pg_trgm。

**1. 准备本地配置**

在仓库根目录执行：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，将下面两项改为演示模式。模板里的生产存储配置不能原样用于无云账号的本地演示。

```dotenv
MODEL_PROVIDER=mock
STORAGE_BACKEND=local
```

**2. 启动数据库、迁移、API 和 Worker**

```powershell
docker compose -f docker-compose.dev.yml --env-file .env up --build
```

开发 Compose 使用本地地址与开发 Cookie，为数据库和文件创建持久卷。首次启动自动初始化数据库角色和迁移；已有数据库的处理见[开发指南](docs/DEVELOPMENT.md#数据库角色与迁移)。

**3. 在另一个终端启动前端**

```powershell
cd frontend
npm ci
npm run dev
```

打开 [localhost:5173](http://localhost:5173/)，注册后开始体验。Mock 返回演示结果，不代表真实照片识别或供应商服务已验收。启动问题见[常见开发问题](docs/DEVELOPMENT.md#常见开发问题)。

## 文档导航

| 我想了解                                | 阅读入口                                                        |
| --------------------------------------- | --------------------------------------------------------------- |
| 拍照、候选选择、补拍、问答和资料状态    | [使用指南](docs/USAGE.md)                                       |
| 本地开发、数据库、测试与贡献流程        | [开发指南](docs/DEVELOPMENT.md)                                 |
| 四类模型、Responses、图搜图、超时和额度 | [模型与服务配置](docs/CONFIGURATION.md)                         |
| 多模态、检索、证据边界和数据隔离        | [架构与数据流](docs/ARCHITECTURE.md)                            |
| 管理员授权、运行配置、指标与兼容回退    | [运行管理](infra/V2_RUNTIME.md)                                 |
| CI、维护镜像、备份恢复和上线检查        | [发布与维护](infra/MAINTENANCE.md)                              |
| 可选 COS 定时备份与告警                 | [备份与告警](infra/BACKUP_AND_ALERTS.md)                        |
| 环境变量模板                            | [应用配置](.env.example) · [备份配置](infra/backup.env.example) |

## 技术组成

| 层           | 实现                                                               |
| ------------ | ------------------------------------------------------------------ |
| 前端         | React、TypeScript、Vite、Ant Design                                |
| API / Worker | Python 3.12、FastAPI、PostgreSQL 任务队列                          |
| 数据与检索   | PostgreSQL 16、pgvector、pg_trgm、混合检索                         |
| 操作引导     | DeepAgents、LangGraph、PostgreSQL 检查点                           |
| 模型与搜索   | Chat Completions / Responses、Embeddings、语音专用协议、Bocha 搜索 |
| 文件与发布   | 本地存储或腾讯云 COS、Docker Compose、GitHub Actions               |

精确依赖以 [Python 清单](backend/pyproject.toml)、[uv 锁文件](backend/uv.lock)、[前端清单](frontend/package.json)和 [npm 锁文件](frontend/package-lock.json)为准。

## 运行与维护约定

- `staging + local` 支持 HTTPS 服务器试用；`production` 配置校验仍要求 COS。可选定时备份和邮件并非自动启用。
- 当前[发布兼容标记](infra/schema-compatibility.txt)为 `requires-maintenance`。主分支 CI 成功后可生成维护镜像，**不会自动更新服务器**。
- 真实供应商、图片质量、下载站点和具体设备适用性须分别验证，测试通过不能保证所有外部站点始终可用。
- 私有配置、设计过程、部署回执和验收报告放在 Git 忽略的 `ignored-docs/`；公开文档只保留可复用说明和无密钥示例。
