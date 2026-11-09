# 模型与服务配置

[← 项目首页](../README.md) · [使用指南](USAGE.md) · [开发指南](DEVELOPMENT.md) · [运行管理](../infra/V2_RUNTIME.md)

文本、图像、语音和向量分开配置。环境变量提供服务器预设；管理员可新增经过能力测试的配置，分别启用，各用途互不替代。

## 目录

- [管理员切换模型](#管理员切换模型)
- [四类模型与协议](#四类模型与协议)
- [Responses 与以图搜图](#responses-与以图搜图)
- [环境变量分组](#环境变量分组)
- [超时、重试与额度](#超时重试与额度)
- [资料来源与搜索](#资料来源与搜索)
- [故障定位](#故障定位)

## 管理员切换模型

前提是启用运行管理、已授予管理员权限，并在 API/Worker 配置同一个有效 `MODEL_CONFIG_ENCRYPTION_KEY`。授权步骤见[运行管理](../infra/V2_RUNTIME.md#启用与授权)。

1. 在管理界面选择文本、图像、语音或向量用途。
2. 填写名称、服务基础地址、协议、模型名与 API Key。
3. 点击“测试配置”，通过后“保存并启用”，确认切换。
4. 核对当前启用配置；需要时可启用原配置或恢复服务器预设。

地址使用**公网 HTTPS 基础地址**，例如 `https://api.example.com/v1`；不要填最终的 `/responses`、`/chat/completions`、`/embeddings` 或 `/audio/transcriptions` 路径，也不要附带密钥和查询参数。

测试结果绑定当前表单；修改内容或测试过期后须重新测试。保存和启用是两个操作：若出现“已保存，但未启用”，可在列表中重新启用。版本冲突时刷新核对，不能把保存成功当成启用成功。

**启用后新任务生效，无需为日常切换重新部署。** 进行中的运行和任务继续使用原配置快照；历史消息不重写。密钥加密保存且不回显，换 key 时新增配置。不要直接替换加密主密钥，否则历史配置可能无法解密。

## 四类模型与协议

| 用途             | 负责什么                                      | 协议 / 约束                                             |
| ---------------- | --------------------------------------------- | ------------------------------------------------------- |
| 文本 `chat`      | 问答、引用核验、模型参考、说明书与 Agent 规划 | OpenAI 兼容或百炼的 Chat Completions / Responses        |
| 图像 `vision`    | 外观识别、图文提问、扫描页转写                | Chat Completions / Responses；百炼 Responses 可加图搜图 |
| 语音 `stt`       | 录音转为待确认文字                            | 百炼音频或 OpenAI 兼容转写协议                          |
| 向量 `embedding` | 资料索引与查询向量                            | 服务器预设 Embeddings 协议，仅允许换地址和 key          |

向量的**模型、修订、维度和协议保持服务器预设**，切换不会自动重建资料向量。新地址必须真正提供同一模型与修订；维度测试通过不证明向量空间完全一致。更换向量模型属于单独的受控重建工作。

文本测试检查普通回复、JSON、流式输出，Responses 还验证两轮函数调用；图像、语音和向量使用各自固定测试输入。测试消耗实际供应商及网站预算，能力连通不等于所有设备识别与问答质量已验收。

## Responses 与以图搜图

新环境可在预设中设置：

```dotenv
CHAT_API_PROTOCOL=responses
VISION_API_PROTOCOL=responses
VISION_IMAGE_SEARCH_ENABLED=false
```

旧环境未设置协议时保持 Chat Completions；管理员已启用的配置优先于环境预设。文本与视觉支持 Responses，Embeddings 和语音仍使用专用接口，不能统一改成 `/responses`。

需要图搜图时，图像用途选择“百炼 Responses + 图搜图”。服务器预设则同时要求百炼视觉服务、Responses 与 `VISION_IMAGE_SEARCH_ENABLED=true`。先确认账号、地域和模型实际支持原生工具，再完成能力测试。

| 能力           | 提供的信息                       | 边界                               |
| -------------- | -------------------------------- | ---------------------------------- |
| 视觉理解       | 形状、文字、布局与可比较特征     | 外观推测不证明网络存在对应产品     |
| 以图搜图       | 相似图片、产品页和候选线索       | 仍待用户确认，不直接发布为官方资料 |
| 文字搜索与下载 | 型号的产品页、支持页和说明书正文 | 独立核验来源、型号、版本和正文     |

图搜图仅用于识别，不用于普通问答或扫描页 OCR。工具不可用时可保留外观判断，在预算允许时尝试文字检索；额度、取消或超时可终止任务。不会自动跨供应商或改协议，照片会发送给所配置服务，工具可能额外计费。

依赖供应商不透明思考状态的工具续接暂不支持，会拒绝配置或调用；普通模型输出不能冒充原生搜图结果。

## 环境变量分组

完整模板见 [`.env.example`](../.env.example)，实际约束见 [`Settings`](../backend/app/config.py)。模板模型名和地址是示例预设，不能当作当前线上有效配置。

| 分组       | 主要变量                                                                                    |
| ---------- | ------------------------------------------------------------------------------------------- |
| 环境与入口 | `ENVIRONMENT`、`APP_PUBLIC_URL`、`PUBLIC_PATH_PREFIX`、`AUTH_COOKIE_PATH/SECURE`            |
| 数据库     | `DATABASE_URL` 或 `DB_HOST/PORT/NAME/USER/PASSWORD`；Compose 另有管理员、迁移和 Worker 密码 |
| 文本       | `MODEL_PROVIDER/BASE_URL/API_KEY`、`CHAT_MODEL`、`CHAT_API_PROTOCOL`                        |
| 图像       | `VISION_PROVIDER/BASE_URL/API_KEY/MODEL/API_PROTOCOL`、`VISION_IMAGE_SEARCH_ENABLED`        |
| 语音       | `STT_PROVIDER/BASE_URL/API_KEY/MODEL`                                                       |
| 向量       | `EMBEDDING_PROVIDER/BASE_URL/API_KEY/MODEL/REVISION/DIMENSIONS`                             |
| 搜索       | `SEARCH_PROVIDER/BASE_URL/API_KEY`、`SEARCH_MAX_RESULTS`、`FETCH_DNS_PROVIDER`              |
| 存储       | `STORAGE_BACKEND/DIRECTORY` 或 `COS_SECRET_ID/SECRET_KEY/BUCKET/REGION`                     |
| 管理       | `V2_ENABLED`、`MODEL_CONFIG_ENCRYPTION_KEY`、`MODEL_PROFILES`、`MODEL_PRICES`               |

`VISION_PROVIDER/BASE_URL` 等写法表示共享前缀的独立变量，不是含斜杠的变量名。宿主机后端从当前目录 `.env` 读取配置；Compose `--env-file` 用于变量替换，只有显式映射的变量进入容器。不要假设 `.env` 中任意新增变量会自动传入 API/Worker。

`staging` 和 `production` 都要求 HTTPS、Secure Cookie、足够长度 JWT 密钥与真实模型；`production` 额外要求 COS。本地演示使用开发环境、mock 与本地存储，见[快速开始](../README.md#快速开始)。

## 超时、重试与额度

| 变量                           | 默认值 | 作用                                         |
| ------------------------------ | ------ | -------------------------------------------- |
| `CHAT_TIMEOUT_SECONDS`         | 45 秒  | 问答整体预算，检索、生成和核验共享截止时间   |
| `VISION_TIMEOUT_SECONDS`       | 60 秒  | 普通视觉调用                                 |
| `DOCUMENT_OCR_TIMEOUT_SECONDS` | 180 秒 | 单扫描页模型预算；整份转写另有 10 分钟总预算 |
| `IMAGE_SEARCH_TIMEOUT_SECONDS` | 120 秒 | 原生图搜图                                   |
| `EMBEDDING_TIMEOUT_SECONDS`    | 30 秒  | 向量请求                                     |
| `STT_TIMEOUT_SECONDS`          | 60 秒  | 语音转写                                     |
| `SEARCH_TIMEOUT_SECONDS`       | 20 秒  | 搜索服务                                     |
| `UPSTREAM_MAX_ATTEMPTS`        | 2 次   | 可重试错误的总尝试次数，包含首次             |

上层总截止时间可缩短单次剩余预算，不能把各行时长相加。429/5xx 仅在相应策略与剩余预算允许时重试；网络超时、断线或流式正文开始后不会自动重放生成。

网站日额度预设为 `DAILY_GLOBAL_MODEL_CALL_LIMIT`、`DAILY_MODEL_CALL_LIMIT`，管理员可在 `RUNTIME_GLOBAL_HARD_LIMIT`、`RUNTIME_SUBJECT_HARD_LIMIT` 内调整。按 UTC 日计数，**切换配置不清零**。供应商余额和网站额度是独立限制。

一次问答可能包含向量、生成、引用核验、联网、模型参考及其来源声明检查，多次调用均计入实际预算。价格未配置或 usage 不完整时标记未计价，不表示免费，估算也不代替云账单。

## 资料来源与搜索

搜索预设支持 Bocha。官方域名有独立注册表；登记来源只加入候选 URL，**不直接发布知识**。用户确认型号不能跳过正文适用性检查。

以下从 `backend/` 执行，需相应受限运维写权限，尖括号占位符须替换为核实后的值：

```text
uv run python -m app.cli trust-brand --name <品牌> --domains <官方域名>
uv run python -m app.cli register-source --brand <品牌> --url <官方资料URL> --model-code <完整型号>
```

区分搜索漏召回、站点限流、正文不可读与型号不匹配。已有公开冻结资料可经适用性和完整性校验后复用；第三方索引保持独立等级，不把搜索摘要当作说明书正文。

下载与服务地址执行公网检查、防 SSRF 和 TLS 校验。开发机 Fake-IP DNS 导致保留地址拒绝时，可评估 `FETCH_DNS_PROVIDER=cloudflare`；仍会逐跳检查公网目标和 TLS，并非关闭校验。服务器默认 `system`。

## 故障定位

| 现象                   | 先检查                                           |
| ---------------------- | ------------------------------------------------ |
| 请求额度已用完         | 网站 UTC 日计数、硬上限，以及供应商余额和限流    |
| 新配置未生效           | 是否真正启用、版本冲突、任务是否仍持有旧快照     |
| 模型测试超时           | 基础地址、协议、模型权限、地域、工具支持和预算   |
| 识别型号但无资料       | 搜索、下载状态、型号/版本适用性与正文提取原因    |
| 扫描页失败             | 图像服务额度、20 扫描页上限、单页/总时限和清晰度 |
| 向量测试通过但检索异常 | 模型、修订、维度及实际向量空间是否相同           |
| Responses 工具续接失败 | 是否依赖不支持的不透明思考状态，不能只改 URL     |

日志只记录必要状态、错误码与统计，不打印完整环境文件、密钥或用户内容。
