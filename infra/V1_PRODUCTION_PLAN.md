# ItemTalk V1 真实验收与生产上线执行方案

状态：**受阻，等待真实服务及生产验收**。本文件是审核方案，不是操作授权。当前不发送邮件、不访问真实COS、不修改服务器，不安装/启用timer、云端规则，不迁移或重启生产数据库/服务。完成本地准备后停止，等用户分别确认真实测试和生产维护。225项原后端CI属于Mock/隔离环境验证，不能填写为生产验收通过。

## 1. 交付与配置位置

已具备每日四文件备份上传/读回SHA-256、7天已完成本地备份清理、COS长期保留、每周临时库恢复、持久告警事件/受控日志/有界重试。SMTP是可选后备，默认关闭；腾讯云监控邮件为首选。代码/入口存在不代表真实服务已验证。

真实密钥、邮箱地址及生产环境值只写受限文件。Windows本地文件用用户专属ACL，移除无关用户读取权；Linux文件由执行账号持有、权限0600，目录0700或已审核的0750。环境文件路径可以出现在命令参数，变量值不可放参数、聊天、日志或Git。运维CLI不打印配置值、不默认读取应用.env；本地显式--env-file，服务器由Compose加载。

| 用途/准备状态 | 变量名称 | 配置位置 |
|---|---|---|
| 独立数据库备份桶，凭据待配置；桶/地域按用户指定 | DB_BACKUP_COS_BUCKET、DB_BACKUP_COS_REGION、DB_BACKUP_COS_SECRET_ID、DB_BACKUP_COS_SECRET_KEY | 本地backend/.env.backup；服务器/opt/itemtalk/.env.backup；模板infra/backup.env.example |
| 地域及生产开关，保持关闭 | BACKUP_SOURCE_REGION、BACKUP_JOBS_ENABLED=false | 同上；北京ap-beijing→南京ap-nanjing；实际桶地域仍需控制台核验 |
| 腾讯云联系人/邮件渠道，尚未配置 | TCOP_REGION、TCOP_ALERT_CONTACT_GROUP_ID、TCOP_NOTIFICATION_TEMPLATE_ID | 同上，填写控制台对应ID；邮箱在CAM验证，不写日志 |
| CLS事件采集与日志通知渠道，尚未配置 | TCOP_CLS_TOPIC_ID、TCOP_CLS_NOTICE_GROUP_ID | 同上，填写实际主题/渠道组ID；不是设置后自动创建云资源 |
| 告警生产开关和选择 | OPS_ALERTS_ENABLED=false、OPS_ALERT_CHANNEL=tcop | 同上；当前没有自动生产投递进程，开关不代替安装、采集和规则配置 |
| SMTP可选后备，全部真实账号参数待配置 | SMTP_HOST、SMTP_PORT、SMTP_TLS_MODE、SMTP_USERNAME、SMTP_PASSWORD、SMTP_FROM、SMTP_TO | 本地backend/.env.alerts或服务器受限.env.backup；模板infra/backup.env.example |
| SMTP单次真实测试开关，保持false | OPS_SMTP_TEST_ENABLED=false | 同上；将来仅在本轮真实通知测试获准后临时设置true，与生产开关分离 |
| 独立测试PG及只读样本资产，需准备 | DB_HOST、DB_PORT、DB_NAME、DB_USER、DB_PASSWORD、STORAGE_BACKEND、COS_BUCKET、COS_REGION、COS_SECRET_ID、COS_SECRET_KEY | 本地backend/.env.acceptance；模板infra/acceptance.env.example；必须是验收库/验收资产配置 |
| 正式应用COS，仍需确认就绪，不能填备份桶 | COS_BUCKET、COS_REGION、COS_SECRET_ID、COS_SECRET_KEY | 应用本地backend/.env；服务器/opt/itemtalk/.env.production；模板根.env.example |
| 备份读取应用资产的独立只读身份 | BACKUP_COS_SECRET_ID、BACKUP_COS_SECRET_KEY、BACKUP_UID、BACKUP_GID | 服务器.env.production；运维容器映射源资产COS身份，数据库备份上传使用另一对DB_BACKUP_COS凭据 |
| 应用/角色/国内模型的现有配置，上线前复核，不宣称全部缺失 | DB_ADMIN_PASSWORD、DB_MIGRATION_PASSWORD、DB_PASSWORD、DB_WORKER_PASSWORD、JWT_SECRET、MODEL_*、CHAT_MODEL、VISION_*、EMBEDDING_*、SEARCH_*、STT_*、APP_PUBLIC_URL、AUTH_COOKIE_PATH、PUBLIC_PATH_PREFIX、ENVIRONMENT、STORAGE_BACKEND | 服务器.env.production及审核后的.env.production.next；国内接口、密钥与地域匹配，生产STORAGE_BACKEND=cos |
| 固定发布元数据，无密钥 | IMAGE_TAG、RELEASE_SHA、API_IMAGE、FRONTEND_IMAGE、DB_IMAGE、BACKUP_IMAGE | 成功维护工作流产物image-overrides.env，核验后成为发布目录.env.image_tag |

SMTP仅支持校验服务器证书的SSL或STARTTLS，拒绝明文；推荐按邮件服务商选择465/SSL或587/STARTTLS。配置文件不会自动启用发送。云渠道的重试/失败/投递状态由云端记录和人工收件证据验收，不能把CLS采集成功算为邮件送达。

## 2. COS最小权限建议

使用专用CAM编程访问身份，仅绑定本仓库 `cos-backup-policy.example.json` 这类自定义策略，不绑定COS全权限、Administrator或应用对象删除策略。策略限定用户指定桶/APPID和 `itemtalk/postgresql/*`；在控制台核对APPID及资源格式后由用户手动提交，当前不应用。

| 操作 | 授予范围/目的 |
|---|---|
| GetBucketACL、GetBucketPolicy、GetBucketVersioning | 指定桶只读，核验私有访问及未启用/暂停版本控制 |
| GetObject | 仅备份前缀，下载并核对全部字节 |
| PutObject | 仅备份前缀，要求private ACL及forbid-overwrite=true，新增唯一对象 |
| Delete*、PutBucket* | 显式拒绝该身份删除对象/桶或改桶配置；不允许脚本设置生命周期 |

不需要列举全桶、挂载、跨桶复制、分块上传或应用桶写入权限；V1单文件上限2GiB。SDK只执行普通PutObject/GetObject；如果真实授权诊断要求额外权限，先核对具体action和资源，不能为消除403直接授予全权限。对象ACL、桶ACL及CAM用户策略应分别核对，模板是CAM身份策略，**不要把Allow策略照贴到桶策略**（现工具对桶中扩大访问的Allow规则要求独立审查）。

新桶保持私有、不设置过期删除生命周期；现有桶若启用/暂停版本控制，先停止验收并审核禁止覆盖方案，不自动关闭。COS长期保留会持续产生存储成本，由用户后续决定生命周期；脚本不删历史/失败上传前缀。最小权限格式及条件依据：[COS授权指引](https://intl.cloud.tencent.com/zh/document/product/436/30580)、[PutObject](https://cloud.tencent.com/document/product/436/7749)、[条件键](https://cloud.tencent.com/document/product/436/71306)、[版本读取](https://cloud.tencent.com/document/product/436/19888)。

## 3. 用户在腾讯云控制台的手动清单

当前只查看/准备；创建实际规则或开启采集、发送测试及服务器安装均等下一次明确确认。

1. COS存储桶列表→itemtalk-backup-1314964135→概览：确认南京ap-nanjing、APPID、私有读写。核对生命周期没有过期删除规则，版本控制满足工具前置条件；不配置COS挂载。
2. CAM用户列表：准备专用备份编程身份及上述自定义策略，拒绝删除/桶写操作，凭据保存到受限.env.backup；不要向聊天发送。源应用/验收资产使用另一个只读身份，目标资产桶不能用数据库备份桶代替。
3. CAM→用户→用户列表→新建用户→自定义创建→仅用于接收消息：填写联系人邮箱，在邮箱中点击验证链接确认接收；无需给消息接收人编程或管理员权限。[官方联系人与验证步骤](https://cloud.tencent.com/document/product/248/54981)。
4. 腾讯云可观测平台→告警管理→告警配置/基础配置→通知模板：准备ItemTalk专用邮件模板、接收组、全天通知时段及失败/恢复类型。界面名称可能因控制台版本变化；以通知模板功能定位。暂不绑定/启用生产策略，不发送测试。[邮件通知配置](https://cloud.tencent.cn/document/product/248/50415)。
5. CLS：准备专用日志主题及最小采集身份；记录主题ID。计划分别采集backups/alerts/event-*.json固定JSON失败事件、backups/completed-*.json每日完成凭证及backups/weekly-*.json每周完成报告，标记不同sourceType。所有文件为完整一行JSON；按createdAt/completedAt作为事件时间，不能把重新采集旧文件的时间当新成功心跳。排除整个生产.env、数据库日志内容与用户上传目录。不安装LogListener/启动采集直到服务器操作另获确认。
6. CLS监控告警：准备失败事件策略、无成功备份超过26小时/无成功恢复超过8天的缺失数据策略；日志渠道组选择已验证邮件接收对象，记录渠道组ID。CVM资源监控准备磁盘可用低于20%/内存压力/实例异常，HTTPS另检查健康版本及证书剩余24小时。规则保存/启用/真实发送仍待确认。[CLS规则](https://cloud.tencent.com/document/product/614/51742//)、[通知渠道组](https://cloud.tencent.com/document/product/614/59661)。
7. 当前事件/报告不等于连续采集：启用前必须验证daily/weekly成功记录采集及云端按事件时间检测26小时/8天缺失，健康由外部HTTPS拨测和CVM监控覆盖，并验证“任务根本未运行”和“主机失联”能告警。初次采集历史文件不得清除已经过期的心跳告警。缺失检测由云端执行，不能依赖失联主机自己发失败消息；采集链路未就绪是上线阻断项。
8. 用户检查GitHub仓库参与者的通知邮箱和Actions失败通知设置，后续进行一次受控失败工作流通知验收。我们不假定GitHub账户已开邮件，也不新增GitHub邮件密钥。

首选腾讯云监控/CLS邮件，无需自建Prometheus/Grafana。只有云邮件渠道不能满足需求时才选择SMTP后备；SMTP联系人在SMTP_TO、发件人/授权凭据在受限文件配置，并核对服务商发件域名、配额、TLS及退信处理。

## 4. 真实COS验收：仅确认后执行

### 前置和入口

用户须确认使用的专用测试数据库、样本资产、备份前缀、真实凭据已配置及允许本轮上传/下载。独立测试PG16需具备原表owner/运行角色、pgvector和pg_trgm、目标revision；样本为合成数据，不复制私有生产数据。备份工具始终只读源DB，恢复只能创建自己的随机临时库。完整测试仍需只读源资产COS配置，仅有DB备份桶凭据不够。

本地backend目录，加载两份受限文件，不在参数传递值：

```text
python -m app.backup_jobs daily --real-cos --env-file .env.acceptance --env-file .env.backup --pg-bin <PG16客户端目录> --output-directory <本轮专用备份目录>
python -m app.backup_jobs weekly --real-cos --env-file .env.acceptance --env-file .env.backup --pg-bin <PG16客户端目录> --output-directory <同一目录>
```

服务器或Linux隔离环境可用固定摘要backup镜像；在确认后显式docker run/Compose加载受限env文件、加入已核验网络、仅挂专用/backups，无Docker socket/生产资产写挂载。服务器运行这些命令本身属于状态改变，须另确认，当前不执行。

### 逐项成功标准及失败处理

| 项目 | 成功标准/证据 | 失败处理 |
|---|---|---|
| 区域、私有与权限 | 控制台南京地域证据；源服务器北京；SDK私有ACL/桶策略/版本检查通过 | 停止；不改桶权限或扩大身份权限来绕过 |
| 样本一致性与导出 | 确认样本来源/DB名称；dump可列举，资产引用大小/hash一致，原对象未变 | 保留本地唯一副本；记录受控失败，不上传缺对象备份 |
| 四文件上传 | 唯一日期/UUID前缀，不覆盖历史对象；受限attempt记录保存前缀 | 半途失败不生成完成记录；COS残留保留，不自动删 |
| 下载SHA-256 | 每个远端对象完整读回，大小与本地SHA-256一致；checksum清单一致 | 非零退出；产生失败事件，不能标记任务完成 |
| 日备份完成 | 四文件全验后生成completed记录，uploaded-verified；restoreVerified仍false | 未完成所有校验不产生成功凭证 |
| 周恢复 | 从COS重新下载，实际pg_restore到新临时库；结构/表数据摘要/RLS/权限/函数/触发器及资产引用一致 | 无weekly成功记录；只清理本轮创建的临时库/目录，不删源库或COS |
| 源不被覆盖 | 对照前后源清单/记录；生产数据未参与，目标库随机独立 | 发现误指生产或不明更改即停止，转独立事故审查，不继续重试 |
| 保留策略 | 本地已完成备份超过7天才清理；未知旧备份/失败唯一副本保留；历史COS对象仍可读 | 清理异常停止并告警；不得手动删未保全副本来腾空间 |

上传、下载、校验和恢复证明分别保存；每周完成报告只能证明该次备份，不能替代生产维护前的停写配对快照。失败故障注入主要在Mock完成，不通过破坏真实历史备份测试失败逻辑。

## 5. 真实邮件验收：仅联系人就绪且确认后执行

### 腾讯云首选

在独立测试规则/测试主题中投递一条唯一notification-test事件，确认云日志采集记录、测试规则评估/触发历史及通知渠道结果。规则和事件由获准的控制台/采集路径执行，当前CLI `app.ops_alerts --emit notification-test` 仅落盘，**不会发邮件**。至少一位指定联系人在收件箱/垃圾箱找到匹配eventId的邮件，记录本轮时间、渠道、事件ID、实际收件确认；没有收件证据不能标已送达。等待期限建议10分钟，届时仍无邮件判失败/待查，不推定成功。

云端通知失败时保留通知失败/投递记录；排查邮箱验证、接收组、模板绑定、通知时段、规则限频、云额度/状态和垃圾箱。修正后由用户确认再做单次测试。CLS已采集、规则已触发、HTTP200或云“已提交”都不是邮件收件证据。

### SMTP后备入口

默认配置检查不会连接SMTP：

```text
python -m app.ops_mail --env-file .env.alerts
```

将来获准后，仅把独立测试开关OPS_SMTP_TEST_ENABLED设为true，OPS_ALERTS_ENABLED仍false，再单次测试：

```text
python -m app.ops_mail --env-file .env.alerts --send-test --real-email --directory <本轮独立事件目录> --report <全新受限报告文件>
```

成功标准：TLS证书/主机名检查通过、认证及全收件人接受、事件state=submitted、报告notificationVerified=false；随后人工实际收件匹配eventId，独立记录送达证据才通过真实通知验收。SMTP入口永远不会替用户把notificationVerified写true。

连接/认证/拒收失败：报告failed、事件retry-wait、受控日志；不能记录发送成功。CLI每次仅一次投递，不自动重复真实发送；同一测试失败等待nextAttemptAt，到用户批准重试时用 `--retry-test <报告中的testRunId>` 加上相同发送门禁与全新report，保留eventId/Message-ID。最多5次，耗尽仍保留事件；已submitted不重复发送。异常终止遗留测试锁需人工核验，不自动删。达到SMTP接受后的连接中断可能导致重试重复邮件，Message-ID用于辅助去重，不能宣称精确一次。

## 6. 生产上线步骤、影响及停机风险

### 发布对象和窗口

选择**最终提交**的成功CI和Maintenance images产物，重新核对ZIP/内部SHA-256、源码SHA、0009目标迁移头及四镜像摘要。新增本轮代码后，之前f83a12e产物不包含新的SMTP入口，不能用于交付该入口。最终版本写进审核记录再请求执行确认；requires-maintenance保持，不能运行仅支持expand-only的deploy.sh绕门禁。

影响限定ItemTalk的API/Worker/前端、数据库schema/运行角色、应用存储切换和新运维任务。现有其他项目、Nginx整体服务及证书续期不得停用。角色/RLS及COS切换可能不兼容旧应用；必须保留旧数据库/文件/配置/角色恢复点。

建议预留**30–60分钟维护窗口**，仅为规划预算，不是已测出的停机时间。真实耗时取决于上线前DB/资产规模、COS传输和隔离恢复耗时；确认前更新估计和RTO。无法提供停机保证。建议新版本启动后20分钟仍未通过最小健康/角色/资产检查即进入回滚；备份/迁移阶段超时则保持停写，先判断事务/进程状态，不重复启动同一操作。最终RTO、停写时段、操作者和可接受数据损失由用户确认。

### 顺序与门禁

| 顺序 | 获准后操作步骤 | 影响/继续条件 |
|---|---|---|
| 0 审批 | 分别确认真实COS/通知测试范围，再确认固定版本的生产维护、备份与回滚窗口 | 当前无任何上述执行授权；可先确认测试，不等于确认上线 |
| 1 只读预检 | 核对current/旧SHA、镜像摘要、compose ps、HTTPS健康与证书、DB revision/角色属性/扩展、磁盘/内存/资产大小、timer与日志采集现状 | 不读出密钥/用户内容；历史诊断不能代替此次预检。空余空间覆盖源数据、快照、临时恢复及回滚副本，另留至少20%余量 |
| 2 准备版本和受限配置 | 保存四文件原包、核验并解包到新release目录，摘要引用成为.env.image_tag；拉取四个固定摘要。准备.env.production.next和.env.backup，校验权限及必填项 | 不覆盖唯一旧版本/旧配置；compose config只用--quiet，不能打印完整config。BACKUP_JOBS_ENABLED/OPS_ALERTS_ENABLED均false |
| 3 维护入口及停写 | 取得独占.deploy.lock；仅ItemTalk入口进入维护503/限制验收来源，nginx -t通过才reload（若需要改location必须在已确认范围）；停旧Worker、API及旧caddy，确认无其他写入/清理任务 | ItemTalk暂不可用；其他项目和Nginx继续工作。记录在途任务/租约，不让新旧Worker并行 |
| 4 旧状态保护 | 在同一停写窗口备份旧DB、全部本地资产、原配置、Nginx片段、角色/权限及密码恢复依据、旧镜像/Compose、revision | 用PG16工具；数据库dump不含全局角色，另用管理员pg_dumpall --globals-only保存至0600文件，不将角色密码输出到日志；生产配置只能复制到受限备份位置，不放发布包 |
| 5 备份实际验证 | 校验旧配对快照所有文件SHA；用隔离恢复工具核验旧DB/资产清单与结构/内容/权限；旧运行角色恢复依据可用 | 原存储local时只读挂旧资产并用--assets-directory、--writers-paused；COS时用--cos-assets、--writers-paused。不以pg_restore --list代替实际恢复；失败不迁移 |
| 6 资产与角色准备 | 本地→正式应用COS先生成storage_transfer计划，核对后--apply同键复制/读回验证；管理员为缺失角色应用runtime-roles.sql，受限stdin设置新密码/权限，保存实际变更记录 | 源DB/本地文件保留；禁止覆盖冲突/改桶配置。新库角色NOSUPERUSER/NOBYPASSRLS，API/Worker不能拥有表；新role配置不自动由已有数据卷的init脚本执行，必须显式步骤 |
| 7 向前迁移 | 保持入口维护与所有Writer停止，以迁移角色运行新摘要镜像中的alembic upgrade head，核验revision/约束/RLS/FORCE/guard关系 | 仅获准后执行；失败停止，不自动downgrade、不得立刻换旧应用假装回滚。已有DB数据卷继续使用，不重建/删除 |
| 8 受限启动 | 审核后启用新应用配置，先启动API及caddy，验收来源受限；检查DB角色/health/buildVersion/路径/Cookie，再启新Worker验证任务 | 只ItemTalk服务；DB镜像若需更新PG16 patch，另在确认窗口中评估重启/扩展兼容。未确认则继续用已审核旧DB摘要，不默默变更 |
| 9 生产验收 | 完成下述生产清单、真实COS备份/恢复和至少一次实际邮件通知；记录报告位置 | 使用专用账号/合成数据；真实模型调用上限另确认，不复用已用完的额外20次授权；不得称Mock为真实 |
| 10 运维启用审批 | 安装unit/timer、采集和云规则属于独立变更。用户确认后才将BACKUP_JOBS_ENABLED=true，安装每日03:00/周日04:00东八区timer及OnFailure/健康任务，绑定已验证邮件规则 | 检查systemd-analyze/calendar及首次真实执行结果、云采集与缺失心跳告警；SMTP后备若选择，还需单独审核运行投递器，不因模板开关自行启用 |
| 11 发布完成 | 最终健康/版本/真实验收与运维门禁都符合，原子切current、解除ItemTalk维护限制；记录旧/新SHA、revision、摘要、恢复点、执行时间 | 若用户只授权应用试运行而未授权运维启用，只能标“应用试运行，备份/告警生产未启用”，不能标完整V1验收完成 |

维护期间已有timer即使将来安装，也因独占.deploy.lock不能与迁移/回滚重叠；备份任务还持独占.backup.lock。保护旧的pre-*快照，不能让本地7天清理删除唯一维护恢复点。部署无需COS挂载，不新增Prometheus/Grafana。

## 7. 失败及回滚执行方案

1. 未开始schema/角色变更时失败：保持入口维护，停止本轮新服务，保留失败报告；用原配置/旧摘要重启旧ItemTalk，再检查旧健康/文件/任务。确认成功后解除维护，不改DB。
2. schema、RLS或角色已改变后失败：新API/Worker必须保持停止；不得仅替换旧镜像、运行deploy.sh或alembic downgrade。先保存失败状态DB/受限报告，以备审查；确认该过程不会覆盖唯一旧恢复点。
3. 校验停写窗口旧dump/资产/配置/全局角色文件SHA。以管理员在新建隔离rollback库恢复旧dump，核对完整清单；资产恢复到独立新目录，使用工具的路径/链接/hash校验，不能tar extractall覆盖生产资产。
4. 审核恢复角色的属性/成员/密码及旧配置兼容，必要时按受限原角色恢复依据恢复（不要把完整globals.sql盲目重放到已有共享实例）。终止仅ItemTalk数据库连接，将失败itemtalk库改名保留，把已验证rollback库切为itemtalk；需要管理员权限及已确认的目标库名。其他项目库、数据卷不删除。
5. 以旧版本Compose/摘要和原受限配置指向恢复的资产目录，先启旧API/caddy、查HTTPS/buildVersion/认证/旧资产，再启旧Worker；确认旧任务恢复且没有双Writer。恢复原ItemTalk Nginx片段/解除维护；证书续期保留。
6. 回滚验收通过后记录恢复点/旧SHA/实际停机时长及丢弃的专用验收记录范围。迁移到COS的新对象、失败DB、旧dump及小型审计报告保留；任何后续删除另行确认，脚本不清空COS。

恢复点之后的数据损失边界：维护期间禁止普通写入，仅专用验收数据可能需要丢弃；若意外接受用户真实写入，先保全并制定数据协调方案，不能直接恢复旧快照覆盖。回滚RTO以真实隔离恢复演练耗时和实际规模更新，未演练不得承诺无损/零停机。

## 8. 生产逐项验收与状态记录

| 验收项 | 判定通过所需证据 |
|---|---|
| 固定发布 | 最终SHA、成功CI、原始发布包各校验及OCI revision/摘要一致；health buildVersion匹配 |
| DB权限与迁移 | 目标0009 revision；API/Worker非owner、无SUPER/BYPASSRLS、私有表FORCE RLS；越权访问404；迁移角色隔离 |
| 共享HTTPS | /itemtalk/路径、HTTPS可信证书/续期/过期监测、Secure Cookie/Path、SSE无缓冲；原项目路由仍正常 |
| 业务闭环 | 专用账号识别→确认→核验资料→引用问答；公共/私有手册版本、反馈、记忆确认/撤销/隔离、引导暂停重载恢复、高风险拒绝、删除清理均成功 |
| 应用真实COS | 签名上传/确认/读取/不可变副本、私有权限、来源清理隔离；专用样本删除不影响其他对象；不混用DB备份桶 |
| 真备份 | 本轮四文件真实上传/完整下载/大小及SHA一致，完成凭证；7天策略及COS历史无删除 |
| 真恢复 | COS重下载、临时DB实际恢复及结构/数据/权限/资产一致；原库未覆盖；清理仅本次目标 |
| 真通知 | 联系人邮件验证、日志/规则/发送失败及重试证据、至少一次收件eventId确认；只submitted不得通过送达验收 |
| 运维启用 | 用户独立确认后timer/采集/规则实际启用；每日/每周成功心跳及未运行/主机失联/磁盘告警可观测，通知失败保留失败状态 |
| 回滚 | 配对旧DB/资产/配置/角色备份经隔离演练、旧摘要可启动、当前规模RTO已确认；旧恢复点不被清理 |

每项按“本地实现完成／本地测试通过／真实服务待验收／生产未启用”分别记录；真实报告另记录执行人、时间、样本、受限证据路径和结果，不记录密钥或生产数据。任何明确门禁缺失，整体V1仍“受阻，等待真实服务及生产验收”。

## 9. 下一次需要用户确认的事项

先确认COS身份和应用/验收资产桶就绪、独立测试库与样本范围、联系人邮件已验证、选择腾讯云主渠道或SMTP后备，以及允许的**真实测试范围**。测试通过后，再确认最终SHA/摘要、维护日期时段、停机/RTO、备份/角色恢复点、生产应用切换与独立运维启用范围。两次确认不得互相替代；当前只交付准备材料，完成后停止等待。
