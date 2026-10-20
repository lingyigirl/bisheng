# 远端中间件切换与启动迁移验证（2026-10-18）

用户明确要求切到远端数据库和其他中间件，实际验证程序启动时能否自动更新数据库。本次已修改远端数据库结构；没有推送 Git 代码，也没有把本地测试库数据复制过去。

## 切换前与范围

- 远端 MySQL：172.19.70.107:3306/bisheng，原 revision=f064_field_comment_override。
- Redis / Celery Redis：172.19.70.107:6379 的 DB 1 / 2；OpenFGA：同机 8080；Elasticsearch：同机 9200；Milvus：同机 19530；MinIO：172.19.70.108:9000。
- 原本地 API/Worker 使用 .venv/f074-rebase-runtime.yaml；现两者改用 .venv/f074-remote-runtime.yaml，来自现有远端配置。密码保留加密形式，未写入此报告。
- 保留本地旧启动配置副本 .venv/f074-tools/f074-local-start-{api,worker}-before-remote.cmd，可恢复本地运行环境。配置、日志、探针和凭据均留在忽略目录。
- 远端已有 2 个 Worker，切换前共享队列长度 0，没有 IN_PROGRESS 任务。采用专用队列 f074_remote_verify:queue，让本机 API/Worker 使用相同队列，避免消费其他实例共享队列。此队列隔离不等于 MySQL 数据隔离；没有启动 Celery/Beat 抢占共享队列。
- 升级前保存 revision、表/列/索引清单及相关 SHOW CREATE TABLE。它们是结构快照，不是全量数据备份。

## 自动升级的实际结果

标准 src/backend/entrypoint.bat 与 Linux/Docker entrypoint.sh 已有先执行 alembic upgrade head、失败则拒绝启动 API 的流程。直接 uvicorn 不会执行这些版本迁移；原来的本地定向启动器正是直接 uvicorn。本次为该启动器增加同样的迁移步骤后启动，实际日志依次显示：

1. f064_field_comment_override → f074_agent_config：会话 agent_config。
2. f074_agent_config → f074_recovery：recovery_epoch / recovery_state。
3. f074_recovery → f087_agent_member：成员表及索引。
4. f087_agent_member → f088_agent_member_default：is_default。
5. f088_agent_member_default → f074_recovery_scan_index：(status,id) 扫描索引。

模型 metadata 先创建缺失成员表，Alembic 通过已有守卫兼容已创建的表/字段/等价索引。五个待迁移均为增量 DDL，本次没有执行 downgrade、删表或删字段。

SQLAlchemy 直接查询确认远端 revision=f074_recovery_scan_index，pending=[]；三项会话字段和成员表 is_default、扫描索引均存在。表数量 109→110，仅新增 linsight_agent_member。原有 17 个任务的分状态数量与升级前一致：NOT_STARTED=10、WAITING_FOR_USER_INPUT=1、FAILED=3、COMPLETED=2、TERMINATED=1，旧任务 recovery_epoch 全部为 0。

再次正常重启 API 后，启动日志中的 Running upgrade 数量为 0，仍成功启动；/health 与 /api/v1/env 都为 HTTP 200。因此本次实际证明：落后版本可在启动时补齐，已是最新版本时不会重复执行迁移。没有人为破坏远端数据库来注入迁移失败。

## 中间件及网页验证

| 服务 | 实际核对 |
|---|---|
| MySQL | 已完成真实升级并查询字段、索引、版本与任务状态数量 |
| Redis | PING 成功；专用和共享队列长度均为 0；Worker 心跳由 2 增为 3 |
| OpenFGA | 远端 /healthz HTTP 200 |
| MinIO | 配置中的公共桶、临时桶均存在，使用实际配置凭据访问 |
| Milvus | 真实 SDK 连接成功，读取服务版本 2.5.10 |
| Elasticsearch | 集群接口 HTTP 200，yellow；1 数据节点，10 个活动主分片、10 个未分配副本，未分配主分片为 0 |
| API/网页 | API 启动完成；浏览器访问工作区入口后进入平台首页 /dashboard，显示远端统计数据 |

ES yellow 已记录，未擅自修改共享集群副本数。此次未新建业务任务、调用模型、执行 RAG 检索或 E2B 代码，不能把中间件连通与数据库升级通过等同于远端所有业务功能完整验收。原本地会话/成员/模型测试数据仍只在本地库，不会随连接切换自动搬迁。

数据库结构由项目 migration 更新；基础初始化也会按程序既有逻辑执行。Redis、MySQL、Milvus、ES、MinIO 和 OpenFGA **服务自身的软件版本**不会因为运行 alembic 而自动升级。

## 证据与当前状态

本地 API/Worker 当前保持远端配置，监听本机 7860；两个前端仍在本机 3001 / 4001。重新启动本机 API 使用 .venv/f074-restart-api.ps1，会调用已增加迁移步骤的 .venv/f074-start-api.cmd；只改这些本地忽略文件，没有改团队默认 config.yaml。

证据（均在 src/backend/.venv/ 下）：

- f074-evidence/remote-db-before.json、remote-db-after.json、remote-schema-before.json。
- f074-evidence/remote-shared-preflight.json、remote-middleware-verification.json、remote-restart-idempotency.json、remote-es-status.json。
- f074-logs/f074-api-105141.{out,err}.log（升级启动）、f074-api-105347.{out,err}.log（重复启动）、f074-worker-105301.out.log（专用队列）。
- f074-evidence/remote-middleware-browser.jpg。

仅文档进入本地 Git 提交，远端环境配置和日志不提交；远端数据库的版本升级已实际执行并核验。
