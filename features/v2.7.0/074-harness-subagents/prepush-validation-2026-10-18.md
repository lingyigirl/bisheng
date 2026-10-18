# 当前开发分支推送前回归（2026-10-18）

分支：`feat/2.7.0/beijing-ns-llm-guardrail`。本轮依用户要求检查 Harness 范围并推送该分支；不以全站其他模块的存量失败作为本轮新增故障。fetch 后当前远端分支无新增提交，本地在新增本报告前领先 30 个提交。

## 本轮检查

| 检查 | 结果 |
|---|---|
| Linsight + Alembic 单 head + 权限模型/Skill 权限迁移与发布脚本 | 1121 passed、13 skipped、8 warnings，131.04 秒 |
| Harness 客户端与 replayCursor | 21 suites / 233 tests passed，9.002 秒 |
| 真实 HTTP 副作用 + 云端 Redis + 进程故障窗口及恢复回归 | 37 passed、4 warnings，25.77 秒；测试账本没有重复写入 |
| 双子代理结果检查点重建新增回归及相关定向测试 | 44 passed、4 warnings，2.63 秒；该数量与完整后端回归有重叠，不相加 |
| 前端工作区 typecheck、客户端 lint、check-i18n | 均通过 |
| 新测试 Ruff / arch-guard、分支 diff --check | 通过；没有修改机器专用配置、密钥文件或忽略目录 |
| 当前用户会话结果回收 | 会话 be19f8009cd44909be66f7c92daa02a7 已 completed；两次 job_output 都返回非空研究结果 |
| 收尾服务健康 | API :7860、管理端 :3001、客户端 :4001 均 HTTP 200 |

新增 `test_harness_runtime.py` 两项确定性真实 deepagents 图回归：使用生产 PlainRedisCheckpointer + fakeredis 的实际 Redis 命令实现，验证两个子 Agent 结果在已完成图重建、以及主 Agent 整合前取消执行再重建这两个窗口中保留。后者从原检查点继续，主 Agent 收到两份原始 ToolMessage，未再次调用子 Agent。测试没有调用真实模型、外部业务工具或终止正在运行的业务 Worker。

本轮核对近期有界流式消费代码的事件屏障、队列上限、源异常、租约丢失与关闭路径，没有修改可靠回执或放松恢复策略。

## 已知限制保留

- 旧长任务 74bfc049f4dd4b1bba06930db86577bb 的自动接续曾返回 Task produced no result，后来通过原会话维护继续交付文档。本次新增检查点重建场景未重现该失败，不能据此把历史问题标成已修复。
- 记忆仍按约定排除；多厂商、生产并发压力与全部业务工具完整端到端覆盖未完成。
- 全站客户端 5 个失败套件、管理端 54 项 any lint、5 个缺源旧 Skill 和其他已登记的存量问题不因本次通过而消失。没有改动抑制基线或伪造缺失 Skill。
- 本轮为开发分支推送检查，不代表 T36 完整上线验收通过。

详细日志保留于忽略目录 `.venv/f074-logs/prepush-*-1018.log`，真实会话结果回收及健康证据为 `.venv/f074-evidence/review-1018-prepush-health-and-collection.json`。日志、凭据与机器启动配置不提交。
