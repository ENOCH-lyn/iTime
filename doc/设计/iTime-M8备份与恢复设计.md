# iTime M8 备份与恢复设计

版本：0.1；更新：2026-10-05。本文细化 `NFR-REL-01`、`REQ-DATA-01`、`REQ-DATA-02` 和
`REL-02`，定义 Web 首版 M8-B 的 PostgreSQL 备份、连续 WAL、时间点恢复、删除台账重放、
恢复收尾和真实 UI 验证。

用户已确认每日备份和 24 小时内恢复目标，并授权遇到阻碍时优先采用工程决策继续推进。本设计
采用不超过 1 小时 RPO；保留、工具和隔离细节是可配置工程默认，不表述为用户原始决定。

## 1. 目标与非目标

M8-B 必须交付：

- PostgreSQL 18 的物理备份和连续 WAL 归档，可恢复到明确时间点。
- 备份仓库客户端加密，仓库密钥、应用解密根密钥和数据库备份分别保管。
- 最新可恢复点距当前不超过 1 小时；从启动恢复到可开放服务不超过 24 小时。
- 恢复只进入新建隔离集群，不覆盖源集群；完成校验前 API 和全部业务 worker 保持关闭。
- 恢复后重放独立删除台账并执行当前时间下的到期清理，避免旧备份复活已删除数据。
- 强制撤销恢复出的用户与管理员会话，收敛租约、预留和在途作业后才能开放服务。
- 真实 PostgreSQL 备份/恢复、故障注入、服务 HTTP 和 Chromium/Edge 恢复后页面验证。
- 结构化记录实际备份大小、WAL 新鲜度、RPO、RTO、清理数量、校验结果和零残留。

M8-B 不负责：

- 选择正式云厂商、对象存储桶、地区、域名、TLS、告警接收人或生产调度器；这些属于 M8-C。
- 构建热备自动故障转移、跨地域双活、零停机恢复或多主数据库。
- 为用户提供整包恢复入口，或从管理员页面直接执行基础设施恢复。
- 恢复第三方供应商已经处理的数据；只保证 iTime 可控制的数据库和临时产物。

## 2. 方案选择

比较三种方案：

1. **pgBackRest 物理备份 + 连续 WAL（采用）**：成熟支持全量/差异备份、PITR、校验、保留、
   客户端加密和文件/S3 类仓库。恢复流程仍由 iTime 安全编排负责，但不重复实现备份格式。
2. PostgreSQL 原生 `pg_basebackup` + 自写归档/保留脚本：依赖少，但需要自行处理加密、校验、
   过期、并发、部分失败和对象存储，恢复可靠性依赖大量自有脚本。
3. 托管数据库快照/PITR：生产运维成本最低，但当前尚未选择供应商，无法在本仓库重复验证，也
   不能替代删除台账和恢复后业务校验。

采用 pgBackRest `2.59.3` 或后续经单独验证的同仓库格式兼容版本。`2.59.3` 修复了此前可能生成
弱加密子密钥/盐及读取加密清单时校验失败的问题，不能回退到受影响版本。正式实现固定版本和
构建摘要；升级时先用现有仓库副本执行备份、校验和恢复回归。

官方参考：

- <https://pgbackrest.org/user-guide.html>
- <https://pgbackrest.org/release.html#2.59.3>

## 3. 恢复目标与保留策略

| 项目 | 工程默认 |
| --- | --- |
| RPO | 不超过 1 小时；WAL 归档新鲜度 15 分钟告警、60 分钟阻断发布 |
| RTO | 不超过 24 小时；从选择恢复点开始到恢复后 HTTP/UI 门禁通过 |
| 基础备份 | 每周一次 full |
| 日常备份 | 每日一次 differential |
| WAL | 连续 `archive-push`，备份和恢复都等待所需 WAL 到达仓库 |
| 保留 | 4 个完整备份集；保留其依赖的 differential 和 WAL |
| 仓库 | M8-B 使用独立只读挂载的文件仓库验证；M8-C 配置主机外对象存储或专用仓库主机 |
| 加密 | pgBackRest 客户端 AES-256-CBC；随机仓库口令不进入数据库、镜像、报告或 Git |
| 删除台账 | 至少每 15 分钟生成一次签名快照；保留期不短于最老可恢复备份集 |

恢复报告分别记录“备份计划目标”和“本次实际值”。没有实际 WAL 时间、恢复目标和服务开放时间
时，不得宣称 RPO/RTO 通过。

## 4. 运行架构

### 4.1 文件边界

新增：

- `server/Dockerfile.recovery-postgres`：基于已固定 PostgreSQL 18 镜像安装固定 pgBackRest。
- `compose.recovery.yaml`：仅用于隔离备份/恢复演练的源库、恢复库和独立仓库。
- `server/recovery/contracts.py`：运行配置、阶段、清单、台账和报告类型。
- `server/recovery/safety.py`：环境、名称、目录、锁、命令和精确清理边界。
- `server/recovery/ledger.py`：从现有生命周期墓碑生成、签名、校验和重放删除台账。
- `server/recovery/finalize.py`：会话撤销、租约/作业收敛、到期清理和恢复开放门禁。
- `server/recovery/verify.py`：迁移、业务不变量、金额、所有权、删除状态和样本摘要核对。
- `server/recovery/run.py`：初始化、播种、备份、制造增量、归档 WAL、恢复、收尾、报告与清理。
- `server/tests/test_recovery_*.py`：安全合同、台账、收尾、报告和真实容器恢复测试。
- `web/playwright.recovery.config.ts`、`web/e2e/recovery-after-restore.spec.ts`：连接恢复栈的 UI 验证。

普通 `compose.yaml`、生产业务路由和 `itime.main:create_app` 不增加测试专用接口。M8-B 可复用
M8-A 的固定数据生成、报告脱敏、运行锁、精确清理和 Playwright 连接外部测试栈模式。

### 4.2 备份数据流

```text
PostgreSQL source
  ├─ weekly full / daily differential ─┐
  └─ archive_command → archive-push ───┼─> encrypted pgBackRest repository
                                       ├─ backup manifest / checksums
existing lifecycle tombstones ─────────┴─ signed deletion-ledger snapshots
```

pgBackRest 仓库和删除台账位于数据库数据卷之外。生产必须至少有一份主机外副本；本地演练使用
独立命名卷和只读恢复挂载证明恢复过程不依赖源数据库卷。

### 4.3 恢复数据流

```text
select backup + target time
  → create empty restore cluster
  → pgBackRest restore + archive-get
  → PostgreSQL reaches target and stops recovery
  → validate schema and ledger signature
  → replay latest deletion ledger newer than target
  → run current-time retention and recovery finalizer
  → verify invariants / decryptability / sample hashes
  → start API without external workers
  → HTTP and browser acceptance
  → mark restore candidate releasable
```

任一步失败都保留恢复库供诊断但不启动业务 worker；报告说明失败阶段、是否改写恢复库、恢复方式
和运行 ID。重试使用同一只读仓库创建新的恢复目标，不在半完成数据目录上继续猜测。

## 5. 安全与隔离

恢复入口同时要求：

1. `ITIME_ENVIRONMENT=test`，Docker context 为专用恢复 profile。
2. 源、目标数据库和 Compose 项目名分别匹配 `itime_backup_<run_id>`、
   `itime_restore_<run_id>` 和精确 run ID。
3. 输出目录固定在 `/tmp/itime-recovery/<run_id>`，权限为 `0700`。
4. 仓库口令、应用根密钥和删除台账 Fernet 密钥通过权限受限临时文件注入，不出现在命令行、
   Compose 渲染、日志、报告或进程环境快照中。
5. 恢复目标必须为空且与源数据卷、仓库卷不同；禁止使用开发或生产数据库名称。
6. 运行锁不存在；清理只操作本次 manifest 中的容器、卷、网络、数据库、目录和进程。
7. 恢复 API 只绑定回环地址；邮件、AI、订阅、公开链接和导出 worker 在开放门禁前不启动。

`restore` 不提供“确认后覆盖”选项。需要替换生产数据库时，由 M8-C 发布流程先恢复到新集群、
验证、切换连接，再保留旧集群用于受控回退。

## 6. 删除台账

### 6.1 数据来源

不新增第二套业务删除表。台账快照只读取现有最小状态：

- `account_deletions` 中 `pending / processing / completed` 的 `subject_digest`、状态和时间；
  `cancelled` 不产生恢复动作。
- `tasks`、`difficulty_records`、`memories`、`calendar_series`、`calendar_sources` 中
  `deleted_at / purge_after / purged_at` 非空的对象 ID、所有者和版本。

快照不含邮箱、标题、正文、URL、密码摘要、Cookie、AI Key、对象数量画像或错误原文。规范 JSON
按类型、ID 排序，包含 schema version、生成时间、源数据库系统标识、最晚状态时间和前一快照
摘要；使用独立 Fernet 密钥加密并以其 HMAC-SHA256 验证完整性。报告只保存密文摘要和计数。

### 6.2 重放语义

恢复目标时间早于台账状态时：

- 账号 `pending / processing`：按恢复库每个用户 UUID 计算
  `HMAC("account-deletion:" + user_id)`，命中后恢复注销作业、冻结账号、撤销会话并复用现有
  `quiesce_account` 停止临时工作。原 `purge_after` 已到时直接进入现有清理 worker。
- 账号 `completed`：按同一 digest 查找恢复出的旧用户，复用账号级联清理并保留完成回执。
- 单项 `deleted`：重新写入原删除/到期时间、失效相关租约和操作结果，不把对象恢复成活动状态。
- 单项 `purged`：复用现有任务、困难、记忆和日历清理函数清空正文及直接明细，保留原墓碑。
- 目标状态已经相同或更新时幂等跳过；类型、所有者或版本不匹配时阻断开放并记录稳定错误。

重放完成后按恢复时的当前 UTC 时间执行 AI 正文、质量样本、媒体、导出、同步事件和各回收站
清理。这样备份中已有的 `expires_at / purge_after` 会按真实到期状态收敛。

### 6.3 台账新鲜度

每次基础/差异备份前后各生成一次台账，日常至少每 15 分钟生成一次。恢复必须选择不早于目标
时间且已验证签名的最新台账；台账超过 1 小时、签名错误、链断裂或仓库中只有更旧快照时，
恢复候选不可开放。

## 7. 恢复收尾

恢复数据库开始接受业务流量前：

1. 校验 Alembic 版本等于当前代码 head；旧版本只允许先执行兼容升级，不能自动降级代码。
2. 重放删除台账并执行全部到期清理。
3. 撤销所有 `device_sessions` 和 `admin_sessions`，递增活动用户 `auth_epoch`，要求所有人重新登录。
4. 清除过期邮箱令牌、限流桶、租约和临时媒体；未发送旧邮件终止，用户可重新请求。
5. 使用既有租约恢复和额度对账逻辑收敛 AI、导出、订阅、公开链接及注销作业；终态不重开，
   运行态先失效旧 lease，再按各自幂等合同进入可重试或明确失败。
6. 放弃恢复出的开放专注并保存恢复原因，避免停机时间计入专注。
7. 校验 AI 预留、供应商成本和用户账本一一对应；无法自动收敛时阻断开放，不制造补扣。
8. 使用恢复环境提供的独立根密钥抽样解密 AI 配置、订阅 URL 和导出元数据；密钥缺失或错误时
   明确失败，不将密钥放入备份。

恢复 finalizer 可重复运行。每个步骤保存完成摘要；中断后重试不会重复扣额、发送邮件、创建作业
或复活对象。

## 8. 完整性与业务门禁

### 8.1 结构和数据

- `pgbackrest check/info`、备份清单和文件校验通过，目标 PostgreSQL 正常结束恢复。
- Alembic head 和 ORM 漂移检查通过；扩展、约束、索引及 page checksum 状态记录明确。
- 用户、任务、日历、对话、规划、执行、复盘、额度和同步代表计数及稳定摘要符合目标恢复点。
- 无孤儿所有权、跨账号标签/项目引用、重复开放专注、无作业活动预留或不一致 AI 金额。
- 已完成账号注销不出现用户行；单项删除/清理状态不倒退，已到期正文、URL、媒体和样本不可读。
- 所有恢复出的会话均已撤销；外部 worker 启动前队列和租约状态已收敛。

### 8.2 RPO/RTO

演练在备份后持续写入带服务器时间的真实业务记录并强制归档 WAL。随机选择一个明确恢复目标，
验证目标前数据存在、目标后数据不存在；实际 RPO 使用故障模拟时刻减恢复库最大已提交探针时刻
计算，RTO 使用恢复命令开始到 HTTP/UI 全部通过的时间计算。

RPO 超过 1 小时、RTO 超过 24 小时、目标时间不可证明或使用测试夹具时间替代数据库提交时间，
均判为失败。

## 9. 动态测试

### 9.1 服务与容器

- 镜像版本、非 root 编排、内存/磁盘预算、仓库和源/目标卷分离。
- 外部/生产环境、非恢复数据库名、目录逃逸、已有目标、并发运行和宽泛清理拒绝。
- full、differential、连续 WAL、仓库加密、保留和 `pgbackrest check/info`。
- 备份后真实 HTTP 写入、WAL 归档、最新恢复和指定时间 PITR。
- 错误仓库口令、错误应用根密钥、损坏备份、缺失 WAL、损坏/过期删除台账和磁盘不足。
- 进程中断后源库继续可用；半完成恢复库不被 API 或 worker 使用。

### 9.2 删除与恢复

- 备份后申请/完成账号注销，再恢复到申请前；台账重放后账号及全部本人数据仍不可读。
- 备份后删除、恢复和到期清理任务、困难、记忆、日历系列及订阅源；验证 active、trashed、
  purged 三种状态及幂等重放。
- 已过期 AI 正文/样本、临时媒体、导出产物、同步事件和回收站在恢复开放前再次清理。
- 会话、租约、开放专注、额度预留和重复 worker 投递在 finalizer 后一致。
- 控制账号及未删除对象保持完整，不能用全库清空来获得隐私门禁通过。

### 9.3 HTTP 与真实 UI

恢复栈启动真实 Uvicorn 和生产构建预览，但不启动任何外部调用 worker。Chromium desktop、
mobile、tablet 及真实 Edge desktop：

1. 旧 Cookie 被拒绝，使用测试凭据重新登录。
2. 任务、规划、日历、专注、复盘、统计和同步状态可达且代表数据与恢复点一致。
3. 已删除账号无法登录，已删除/清理对象不出现在普通列表、搜索、日历或 AI 上下文。
4. 新建一个任务并在第二浏览器上下文 5 秒内看到，证明恢复库可安全继续写入和同步。
5. 检查 axe、控制台、API 5xx、Vite 错误层、空白页和根级横向溢出。

截图保存到 `/tmp/itime-ui-evidence/*-recovery-after-restore.png`，失败证据位于
`/tmp/itime-recovery-results/`。浏览器验证不替代数据库和删除台账门禁。

## 10. 报告与清理

每次运行在 `/tmp/itime-recovery/<run_id>` 生成：

- `manifest.json`：提交、工具版本、环境、源/目标标识、运行阶段和脏工作区状态。
- `backup.json`：备份标签、类型、开始/结束、大小、WAL 范围、仓库校验和台账摘要。
- `restore.json`：目标、实际结束时间、RPO/RTO、finalizer、清理和业务不变量。
- `report.md`、`summary.json`：通过/失败、恢复方式、边界和零残留结果。

报告递归拒绝密码、Cookie、CSRF、仓库口令、数据库 URL、Fernet 密钥、AI Key、完整订阅 URL、
正文和 pgBackRest 加密配置。原始仓库和临时凭据不进入 Git。

结束后只清理 manifest 中的恢复容器、卷、网络、临时目录和随机数据库，并核对：

- `itime_backup_%`、`itime_restore_%` 数据库为 0；
- `itime-recovery-*` 容器、卷、网络和临时进程为 0；
- 开发数据库、开发卷和 M8-A 性能 profile 未被改写。

## 11. 实施与提交顺序

1. 设计与实施计划。
2. 固定 pgBackRest/PostgreSQL 镜像和恢复 Compose。
3. 安全合同、运行清单和精确清理。
4. 删除台账快照、签名、校验和重放。
5. 备份、WAL 和 PITR 编排。
6. finalizer、业务不变量和报告。
7. 真实容器恢复与故障注入。
8. 恢复后 Chromium/Edge UI。
9. 全量回归、证据和文档收口。

每个独立模块或修复在专项动态测试通过后单独提交。

## 12. 完成条件

M8-B 只有同时满足以下条件才可标记完成：

- 固定工具、镜像、配置和命令可从空环境重建；
- 最新恢复和指定时间 PITR 均使用真实 PostgreSQL/WAL 通过；
- 实测 RPO 不超过 1 小时、RTO 不超过 24 小时；
- 错误密钥、损坏备份、缺失 WAL 和损坏台账均在开放前失败；
- 删除台账重放和当前时间清理证明已删数据不会复活；
- 会话、租约、作业、额度和专注在恢复后处于一致状态；
- 恢复后的真实 HTTP 和 Chromium/Edge UI 通过；
- 全量服务回归、前端构建及受影响 E2E 通过；
- 数据库、仓库、容器、卷、网络、进程和临时凭据零残留；
- 文档继续将生产对象存储、正式调度、告警和发布切换标为 M8-C 未完成。
