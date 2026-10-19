# 本地北京分支同步与重启验收

日期：2026-10-19；环境：Windows、本地前后端、已配置的远端数据库及中间件。

## 同步结果

- 按用户要求，将当前任务修改放到本地 `feat/2.7.0/beijing-ns-llm-guardrail` 分支，不向远端推送。
- 远端没有同名 `feat/2.7.0/089-task-stream-and-export` 分支；本地北京分支整合 `origin/feat/2.7.0/beijing-ns-llm-guardrail` 的 6 个更新提交，远端跟踪提交为 `1bd8e0a7a`，本地合并提交为 `543b5d302`（合并远端北京分支最新更新）。
- 合并没有文本冲突。本地相对远端为 ahead 3 / behind 0，包含原有本地提交及本次合并提交。
- 同步前 99 个改动文件已打包备份；恢复后对照 SHA256，99 个文件逐字节一致。恢复过程中消除了 stash 带来的 48 个文件纯换行差异。
- 原有任务修改仍保留为本地未提交工作区修改。本轮额外更新了一份旧权限测试替身，并新增本文档。
- 备份和证据位于 `src/backend/.venv/f089-sync-20261019/`：`pre-sync.zip`、`manifest.json`、`working-tree.patch`、`restore-proof.json`。stash 及本地备份引用 `refs/codex/backups/f089-before-beijing-sync-20261019` 均保留。

## 重启与真实任务

重启前确认本地验证命名空间无排队及执行中任务，再停止确认属于本项目的旧进程。通过独立终端会话启动服务，未替换远端数据库、中间件或业务密钥配置。

| 服务 | 地址 / 状态 |
| --- | --- |
| 管理平台 | `http://127.0.0.1:3001`，HTTP 200 |
| Workspace 客户端 | `http://127.0.0.1:4001/workspace/c/new`，HTTP 200；也可通过 3001 的代理访问 |
| FastAPI | `http://127.0.0.1:7860/health`，HTTP 200 |
| Linsight Worker | 单 Worker、最大并发 2，使用原验证队列 `f074_remote_verify`，结束检查心跳 TTL 13 秒 |

本次没有重启或验证 Celery 各队列与 Beat，不能据此宣称全部后台工作流已验收。

真实协作任务：`http://127.0.0.1:3001/workspace/c/e60a09167242403798a6a25b70dda6f1`。

- 主智能体将乘法委派给仅配置计算器的临时协作成员，成员实际调用 `calculator` 一次，返回 `3973`。
- 主智能体实际调用 `get_current_time` 一次，最终界面显示乘积和当前时间。
- 任务在第 1 个执行 epoch 完成，回放有 15 个事件，没有生成文件，没有写入常用成员模板。
- 页面已确认任务完成、成员结果和工具输出均可见。截图：`startup-completed.png`。
- 新合入的评测比较及报告导出路由出现在 OpenAPI 中；本轮没有对已有业务评测执行真实导出。
- 结束时队列长度为 0；本轮 API 访问日志中 `status_code=5xx` 记录为 0。该结论只覆盖本轮日志，不代表所有路径均已测试。

终端会话、进程、端口和日志目录记录于 `runtime-state.json`。服务在验收结束后继续运行。

## 回归结果

| 检查 | 结果 |
| --- | --- |
| 后端 `test/linsight test/evaluation test/tool` | 1241 通过、14 跳过、11 条 warning，133.68 秒 |
| 客户端 Linsight 与 WebSocket 回归 | 22 套、242 项通过，10.444 秒 |
| 新合入的平台系统日志导出测试 | 8 项通过，3.52 秒 |
| 前端工作区 `pnpm typecheck` | 通过 |
| 客户端 `pnpm --filter bishengchat lint` | 通过 |
| `pnpm check-i18n` | 通过 |
| 前端工作区 `pnpm lint` | 未通过：平台侧此前已存在的 54 项 `no-explicit-any` 错误 |
| `git diff --check` | 通过 |

后端执行时显式设置 `PYTHONUTF8=1`、`PYTHONPATH` 和既有远端配置文件。跳过的 14 项不计入通过数。本轮为相关模块回归与启动验收，不是全仓库、全部中间件或并发容量验收；此前的侧栏性能数据另见 `resource-availability-performance-2026-10-19.md`。

## 发现并处理的问题

1. 旧工具权限测试替身遗漏 `ToolMcpConnectError`，导致服务模块在测试隔离环境中无法导入；补齐错误类型替身。
2. 旧测试仍预期逐项查询编辑与删除权限，而实现已使用批量权限映射；调整替身及调用断言，仍检查使用权限过滤、编辑/删除授权与未授权结果，并补齐普通用户的 `is_admin()`。只修改测试，不放宽产品权限。
3. Windows 默认 GBK 子进程日志无法输出内容过滤场景中的 Unicode 符号；在测试命令中启用 UTF-8，复测内容过滤降级成功。已启动的 API 和 Worker 也启用了 UTF-8。
4. 私有运行状态探针曾把日志 ANSI 颜色控制符拼入心跳键名，误报键不存在；剥离控制符后确认当前 Worker 的真实心跳键存在。这是验收脚本问题，不是 Worker 故障。
5. 全前端 lint 的平台侧 54 项既有问题仍保留，不能将本轮验收描述为全仓库质量门禁通过。

本轮没有发现阻止当前前后端及 Linsight 协作任务运行的新合并问题。没有推送、发布或修改远端 Git 分支。
