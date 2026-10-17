# 网页验收问题修复与复验（2026-10-18）

针对 `browser-acceptance-2026-10-18.md` 中的 5 项问题，本轮已修复并完成真实网页复验。没有涉及记忆实现、权限扩张、数据库迁移或远端推送。

## 修复与影响

| 问题 | 修复 | 复验 |
| --- | --- | --- |
| API 恢复后遮罩不退出 | 遮罩显示期间串行探测 API，每 5 秒一次，单次超时 5 秒；成功解除遮罩，卸载后停止调度 | 07:49:12 停止 API，07:49:50 分离重启；网页实际显示错误提示。恢复后提示自动消失，未点击刷新，原 URL 和 epoch=1 保持，Worker 继续执行完成 |
| 后台子 Agent 提前完成、结果为回执 | 启动 ToolMessage 带 job 元数据且不再关闭调用；后台结算按原 call_id 生成最终执行事件，走原有持久化/回放 | 计算成员实际持续运行，卡片从“正在执行”变为“已完成 · 114s”（包含委派生成与启动时间），展开后显示真实报告、CSV 和连接器结果；刷新后仍为相同结果 |
| 无法选连接器、历史配置隐藏 | 从已有权限过滤工具目录读取完整可选能力，react-query 共享缓存；缺失的已选动作以不可用 ID 展示 | 网页新建成员选择 `f074_multiply`，保存并实际执行；历史只读查看中该动作勾选且禁用，模型/两项 Skill/代码执行器均正确 |
| 高频轮询与沙箱空等 | job_output 默认异步等待最多 60 秒，可指定 0–120 秒；超时或收集者取消不取消子任务，仍由任务所有者清理 | 本次主 Agent 只调用 job_output 2 次，没有主 Agent 代码执行或 sleep；子成员按测试要求在真实计算里 sleep(40)，属于耗时模拟，不是父级空等 |
| CSV 不能上传 | 在普通/图片解析两种任务上传格式中加入 csv，沿用已有解析、原始文件保留与大小限制 | 原先被拒绝的同一个 CSV 网页上传成功，沙箱读取、汇总、二次核验、网页下载并解析核对均通过 |

后台等待不新增网络轮询；连接器目录不自动授权或自动选中能力。维护提示的探测仅在故障 UI 可见时运行，单页面最多一个在途请求，成功后停止。服务端原有执行权限校验保持生效。

## 真实任务

- 浏览器会话：`0b34c6fec552476badf1f0bfdebc7ad1`；任务版本：`bfd68775d47d4d2ab5a9ddde2b30667c`。
- 网页创建测试成员：`e2e-fix-1018-CSV连接器`，库 id 10；独立模型 3（model 1），Skills 为 xlsx、e2e-f074-multiply，工具 6，连接器 39。主模型为 4（model 2），另一成员沿用库 id 9 的只读核验员，均未设默认。
- 上传 `web-1018-input.csv`，4 条构造数据；并行后台计算与只读手算，随后同一核验员第二次调用核对实际产物。
- 云端 Python 3.13.16 实际读取原文件，生成 `output/fix-1018-monthly.csv`：2026-01=200、2026-02=200，总额 400 元。
- 子成员真实连接器调用参数 a=400、b=3，执行事件中返回 `{"result":1200,"verification":"f074-tool-f68e5172675c"}`；同时返回配置 Skill 标记。不是以口算代替工具执行。
- 最终状态 completed、计划 6/6，结束标记 `FIX-1018-WEB-OK`。网页下载至 `C:/Users/hei/Downloads/fix-1018-monthly.csv`，用 CSV 解析器确认两行值均为 200。
- API 中断后自动恢复过程未重载页面；完成后另行手动刷新以验证持久化回放，三张成员卡片分别为完成 114s、10s、15s，计划完成标记唯一。

本次最终 usage：主 Agent 12 次模型调用、输入 214415 tokens；子 Agent 11 次、输入 75769 tokens。原问题样本为主 Agent 32 次、job_output 18 次，本次为 12 次、2 次。两次输入格式/技能/连接器与提示不同，不能作为严格性能基准；本次仍有较大的基础上下文和模型推理开销，不宣称所有成本问题已解决。

## 自动化与检查

- `pytest test/linsight -q`：**959 passed / 11 skipped / 0 failed**，156.32 秒。新增覆盖默认等待、查询超时、收集者取消、子任务终止唤醒、真实 LangGraph custom stream 结束事件及工具失败状态。
- 前端相关 **8 suites / 38 tests passed**：遮罩恢复/重复故障/无重叠与卸载清理、完整目录/历史缺失动作、卡片最终报告/耗时回放/终止状态、成员选择、任务输入及 WebSocket 恢复。
- 根目录 `pnpm typecheck`、`pnpm check-i18n`、`pnpm locales:check`、client lint 通过；架构守卫通过；修改的 Python 格式化通过，Ruff 非历史歧义字符规则检查通过。
- 根目录 `pnpm lint` 仍在 platform 阶段有历史 **54 个 no-explicit-any** 错误，与合并验收基线一致；没有把它记录为全门禁通过。Python 既有中文标点/歧义字符诊断未扩大为无关清理。

## 证据与边界

本地忽略目录 `src/backend/.venv/f074-evidence/`：

- `review-1018-fix-web-completed.json`、`review-1018-fix-web-usage.json`、`review-1018-fix-web-proof.json`；
- `fix-1018-api-offline.jpg` 与 `fix-1018-api-auto-recovered.jpg`；
- `fix-1018-background-running.jpg`、`fix-1018-background-final.jpg`；
- `fix-1018-connector-locked.jpg`、`fix-1018-completed.jpg`、`fix-1018-replay-completed.jpg`。

检查日志位于 `src/backend/.venv/f074-logs/fix-1018-*`。测试成员和任务保留便于复现，凭据、运行配置、日志、截图和下载文件不纳入 Git。

本轮没有重新验证数小时长稳、DM8、全租户攻防或 Worker 任意崩溃点；这些边界沿用之前报告。旧任务中已经持久化的启动回执不会被伪造为新完成事件，本次生命周期修复适用于更新后的执行。记忆范围继续排除。
