# Harness 全流程复测（2026-10-21）

## 范围

复测最初需求中除长期记忆外的全部能力：

- 云端沙箱与隔离代码执行；
- Agentic Loop：推理、工具调用、结果回传、错误观察与计划调整；
- 按目标自主规划、迭代执行和产物落盘；
- 工具与连接器注册、选择、调用；
- 子 Agent 独立名称、提示词、模型、Skills、工具和连接器配置；
- 主 Agent 路由、并行委派、结果收集与整合；
- 长任务流式反馈、页面刷新重连、人工暂停恢复和 Worker 重启后续跑。

长期记忆按用户要求排除，不将 MemoryAdapter 预留接口计入本次通过项。

## 自动化门禁

| 检查 | 结果 |
|---|---|
| 后端 `test/linsight` | **1126 passed, 16 skipped** |
| Client Harness Jest | **22 suites / 245 tests passed** |
| 前端 typecheck | 通过（platform、client、file-viewers） |
| Client ESLint | 通过 |
| i18n parity | 通过，无新增漂移 |
| Platform ESLint | 失败：既有 54 个 `no-explicit-any`，与本次 Harness 改动无关 |

后端首轮全量回归发现 1 个本次兼容性回归：未传 `required_files` 时仍向执行器传递空列表，导致 3 个既有 code-alias 测试失败。已改为仅在文件非空时传递该参数，并保留模型 tool schema 中的可选声明。修复后相关 93 项和全量 Linsight 1126 项通过。

## 真实工作台流程

### 自主规划、Agentic Loop 与主/子 Agent

会话：`2f7c275eabf74933abb3da9bdc65b69c`

- 主 Agent 自主建立 5 项待办。
- 并行委派 `FULL-计算员-1018` 和 `FULL-核验员-1018`。
- 计算员使用 `bisheng_code_interpreter` 在 E2B 中计算并写
  `scratch/harness-calc.csv`。
- 核验员按其独立资源配置没有代码执行器，因此如实停止伪造，改用 calculator
  完成复算并写 `scratch/harness-verify.csv`。这是成员资源隔离生效，不是平台
  错配。
- 主 Agent 仍执行第三次计算：先故意导入
  `harness_missing_dependency`，收到真实 `ModuleNotFoundError`；随后改用
  `statistics` 完成复算，没有重复无效调用。
- 三套结果在 count、sum、mean、median、严格大于 90 的数量和列表上完全一致。
- 主 Agent 写 `output/harness-flow-report.md`，完成任务收集与整合。
- 任务回复明确标记核验员“未使用代码执行器”这一测试子项失败，没有把部分通过
  宣称为全部通过。

### 流式反馈与刷新重连

在上述主流程执行期间主动刷新整个工作台页面。刷新后：

- 恢复当前等待中的任务和已经展示的计划；
- 两个子 Agent 的身份卡、运行状态和最新输出继续出现；
- 后续工具错误、计划调整和最终产物均完成，没有丢任务或重复执行。

### 人工暂停与恢复

会话：`a992d6f0b7a24e9486a14cf8a88f096f`

1. 在任何状态工具有调用前调用 `ask_user` 暂停。
2. 用户选择“继续执行”。
3. 恢复后调用 E2B，输出 `RESUME_OK`。
4. 写 `output/harness-resume-check.txt`，内容为 `paused-resumed`，大小 15 字节。

链路通过。

### Worker 重启后的断点续跑

会话：`8575eda608014e87bd4a0c57325db38e`

1. 任务在调用任何代码/文件工具前停在 `ask_user`。
2. 终止并重启本地 Linsight Worker；Redis 和 MySQL 初始化成功。
3. 在原工作台选择“继续”。
4. 新 Worker 恢复任务并执行 E2B。
5. 输出 `RESUME_AFTER_WORKER_OK`，写
   `output/harness-worker-resume.txt`，大小 24 字节。

任务 trace：`e948bd84b52e41deb7d1e1fd24f52bb4`，最终状态 SUCCESS，代码输出和
文件均正确。Worker 同时记录：

```text
No final task result; using direct-answer fallback
Task completed via direct-answer fallback (1 report files)
```

这条任务没有规划子任务，也不会产生 planner 的 `TaskEnd`，因此
`_handle_task_completion` 在 `_final_result` 为空时进入 direct-answer
fallback 是当前预期分支；fallback 随后正常收集 `output/` 文件、写入
`FINAL_RESULT` 并把任务标记为 SUCCESS。它不是文件恢复失败，也不是旧
“长任务规划完成后无结果”问题的复现。唯一可改进点是简单任务的正常 fallback
仍使用 WARNING 日志，容易让监控误以为恢复异常。

### 工具与 MCP 连接器

会话：`c6bb5d662a92494abcc83e65c1092d92`

成员 `企业知识产权数据获取` 的资源配置为：

- 模型：跟随主智能体；
- 工具：代码执行器；
- 连接器：企业知识产权 MCP 8 个工具。

成员实际调用：

- `health_ping_ecf45082`，返回
  `{"status":"ok","provider":"zjs_ipr","db_ready":true}`；
- `get_tm_baseinfo_b22856b3`；
- `get_tm_flow_base_5fa5319e`；
- `get_tm_product_base_aa709135`；
- `get_tm_announce_base_544e2af0`；
- `get_company_copyright_detail_7bcf33c0`；
- `get_software_copyright_owner_6648ec8e`。

七类数据工具均返回真实记录，主 Agent 将调用清单、错误和关键字段写入
`output/ip_connector_acceptance_tencent.md`。

过程中发现并要求后续查询注意：

- 数据库主体名使用半角括号；全角括号写法会返回 `not_found`；
- `get_tm_flow_base_5fa5319e` 首次出现
  `(pymysql.err.OperationalError) (1105, 'context canceled')`，同一调用重试后
  成功；
- 当前授权的知识产权连接器没有专利检索工具，因此不能生成专利概览。

## 独立成员配置观察

- 计算员与核验员使用不同成员身份和提示词，工具列表实际不同。
- 企业知识产权成员可独立选择模型、Skills、工具和 8 个连接器。
- 当前界面允许每个成员独立选择模型，自动化和既有跨模型验收已覆盖不同模型
  配置；本次实时任务只验证了成员级工具与提示词隔离，没有重新制造跨厂商模型
  调用。

## 结论

除长期记忆外，本次要求的核心链路均完成真实运行或自动化验证：

- 云端沙箱和隔离执行通过；
- 自主规划、工具调用、错误回传与计划调整通过；
- 主 Agent 并行路由、子 Agent 配置隔离和结果整合通过；
- 流式反馈、页面刷新重连、人工暂停恢复和跨 Worker 执行续跑通过；无规划
  子任务的简单恢复任务按预期使用 direct-answer fallback；
- 工具注册与 MCP 连接器调用通过；
- 无新增 Harness 前端类型、lint 或 i18n 问题。

仍不将 T36 全量上线验收勾选为完成。本期未覆盖生产级长时并发、跨物理机、
所有外部连接器副作用、跨厂商模型组合和全平台 DM8 验收；平台既有的 54 个
lint 错误继续保留。
