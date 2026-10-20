# Harness 开发与验收文档索引

最后整理：2026-10-18。先读当前摘要，再按用途查证据；历史报告保留当时的环境和检查结果，不覆盖最新结论。

## 接手入口

| 文档 | 用途 |
|---|---|
| [当前验收摘要](./current-acceptance.md) | 已验证能力、最新检查、未解决问题与责任边界 |
| [本地环境交接](./local-environment-handoff.md) | 停机范围、保留内容、后续启动与配置位置 |
| [推送前回归](./prepush-validation-2026-10-18.md) | 已推送代码基线 6db8e2657 的检查结果与限制 |
| [任务清单](./tasks.md) | 实施进度；T36 未全量验收 |

## 需求、设计与操作

| 文档 | 用途 |
|---|---|
| [需求](./requirements.md)、[规格](./spec.md)、[设计](./design.md) | 范围、协议与实现设计；评审记录为同目录 *-review.md |
| [恢复策略](./recovery-policy.md) | 无成员模式、外部副作用与沙箱的恢复边界 |
| [自动恢复实现](./automatic-recovery-validation.md) | 租约、epoch、回执、检查点与早期故障验证 |
| [Skill 存储规格](./skill-object-storage/spec.md)、[设计](./skill-object-storage/design.md) | 对象存储真源、缓存与兼容迁移 |
| [Skill 运维手册](./skill-object-storage/runbook.md) | 迁移预览、执行、缺源处置、回退与多实例注意事项 |
| [OpenFGA 补齐](./skill-object-storage/openfga-repair-2026-10-18.md) | Skill 类型、权限模型与 owner 补偿实测 |

## 最新验证证据

| 文档 | 覆盖范围 |
|---|---|
| [完整本地验收](./full-local-acceptance-2026-10-18.md) | 本机应用连接云端中间件的功能矩阵与遗留问题 |
| [多模型与副作用补测](./multimodel-external-recovery-2026-10-18.md) | 模型 1/18 互换、真实 HTTP + Redis + 故障窗口 |
| [流式积压修复](./stream-buffer-performance-2026-10-18.md) | 有界合并、模拟对比、真实长输出与旧任务维护接续 |
| [此前性能改进](./performance-fix-validation-2026-10-18.md)、[E2B 性能](./e2b-performance-validation-2026-10-18.md) | 补播、资源校验、恢复扫描及沙箱文件批量同步 |
| [云端功能验证](./remote-functional-validation-2026-10-18.md)、[启动迁移验证](./remote-startup-migration-validation-2026-10-18.md) | 运行拓扑、中间件与重复升级检查 |
| [无成员补测](./memberless-retest-2026-10-18.md) | 不配置协作人员时的 Harness 与恢复 |

## 历史追溯

- [10 月 14 日基线验收](./baseline-acceptance-2026-10-14.md)：归档原 current-acceptance.md，保留早期本地测试库和旧迁移 head 的证据。
- [基线整合](./rebase-validation.md)、[远端分支拉取整合](./pull-validation-2026-10-18.md)：分支来源、冲突处理与当时回归。
- [浏览器验收](./browser-acceptance-2026-10-18.md)、[修复验证](./browser-fix-validation-2026-10-18.md)、[再次测试](./browser-retest-2026-10-18.md)、[再次修复](./browser-retest-fixes-2026-10-18.md)：逐轮发现与修正，不能单独代替最新完整验收。
- [早期 E2E 报告](./e2e-report.md)、[DM8 专项](./dm8-validation.md)：按报告中的实际环境和覆盖边界解释。

原始日志、探针、截图、配置和凭据仍在本机忽略目录 `src/backend/.venv/f074-*`。仓库内保留结论与可复核的测试代码，不复制凭据或完整原始响应。
