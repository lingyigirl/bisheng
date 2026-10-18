# 本地环境停机与交接

记录日期：2026-10-18。依用户要求完成本机服务收尾；这是运行状态记录，后续重新启动后以实际进程、端口和容器状态为准。

## 已停止

停机前查询当前租户无 IN_PROGRESS 任务，随后核对进程命令和父子关系，仅停止本项目运行入口及其子进程。

| 服务 | 原监听端口 / 标识 | 收尾处理 |
|---|---|---|
| FastAPI | 127.0.0.1:7860 | 停止本机启动器及 API 进程 |
| Linsight Worker | 专用 f074_remote_verify 队列配置 | 停止启动器、Worker 与执行子进程 |
| 管理端 Vite | 127.0.0.1:3001 | 停止开发服务及启动器 |
| 客户端 Vite | 127.0.0.1:4001，/workspace | 停止开发服务及启动器 |
| 合成 HTTP 工具探针 | 127.0.0.1:8767 | 停止测试服务及启动器 |
| 本地测试中间件 | jinbao-f074-* | 停止 MySQL、Redis、MinIO、Milvus、etcd、Elasticsearch、OpenFGA 共 7 个容器 |

本次停止 20 个已核对归属的项目进程（含启动器及附属进程）。Docker Desktop 保持运行，容器和数据卷未删除。云端数据库、中间件与共享数据未停止或清理；本次没有启动、停止或部署云端 API。

## 保留内容

- 代码验证基线 `6db8e2657` 已推送到 `origin/feat/2.7.0/beijing-ns-llm-guardrail`；最新提交与文档整理状态以 Git 为准。
- 数据库记录、MinIO 产物、Skill 对象与缓存、测试数据卷、下载文件保留。
- 本机运行配置：`src/backend/.venv/f074-remote-runtime.yaml`；账号、登录 cookie、日志、脚本和截图等仍在 `.venv/f074-*` 忽略目录中。
- 前端机器配置：客户端 `.env.local` 被忽略；管理端 `.env.development.local` 原已在仓库，只含代理地址，此次未改动。
- 原始停机进程清单保存在忽略文件 `.venv/f074-evidence/shutdown-local-processes-2026-10-18.json`。PID 文件只作历史句柄，不能直接认定对应进程仍是本项目进程。

## 后续重新启动

1. 先确定目标环境：继续使用云端中间件，或使用已保留的本地测试容器。前者无需启动本地 7 个中间件容器；后者使用本机忽略目录 `.venv/f074-compose.yaml` 的原配置启动，不能混用数据库、存储签名地址和权限模型。
2. 按 [后端约定](../../../src/backend/AGENTS.md) 与 [Windows 后端启动说明](../../../docs/本地后端启动-Windows.md) 启动 API 和独立 Linsight Worker。现有本机分离启动入口仍在 `.venv/f074-start-*.cmd`，恢复脚本为 `.venv/f074-restart-api.ps1`、`.venv/f074-restart-worker.ps1`；调用前检查脚本的目标路径、配置与进程筛选范围。
3. 按 [Windows 前端启动说明](../../../docs/本地前端启动-Windows.md) 启动管理端和客户端。保留代理配置与后端 MinIO sharepoint 一致，否则签名文件下载可能 403。
4. 检查 :7860 健康接口、:3001 首页、:4001/workspace/，再提交一个合成短任务确认 Worker 消费、流式回传及产物下载；不能仅凭前端页面打开认定 Worker 正常。
5. 如需再次停机，先检查在途任务和队列，核对进程归属后停止；本地测试容器使用 stop，保留数据卷。不要依据过期 PID 杀其他进程。

启动会执行数据库升级时，应按 [启动迁移验证](./remote-startup-migration-validation-2026-10-18.md) 与 [Skill 存储运维手册](./skill-object-storage/runbook.md) 检查当前环境。最近实测 head 为 f035_skill_object_storage；缺源 Skill 与 owner 补偿属于运维数据过程，不因单纯启动服务自动补出原文件。

## 接手重点

先读 [当前验收摘要](./current-acceptance.md)。旧长任务自动接续收尾失败、一次 API 原因未明退出、5 个旧 Skill 缺源仍保留跟踪；新增并行结果重建测试通过不等于历史故障已修复。记忆、跨厂商和生产压力覆盖按摘要中的边界处理。
