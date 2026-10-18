# Skill 权限模型缺项修复

## 范围与实现

按现有 C4 所有者登记契约的缺陷修复处理：SkillService 已调用 PermissionService.authorize，但静态模型未声明 linsight_skill，导致 owner 写入失败。补齐类型和发布/补偿工具，不改变技能管理接口、租户筛选或用户可见能力。源文件缺失不在此修复范围。

- 静态模型升级标记 v2.0.4，新增 linsight_skill，沿用 owner → manager → editor → viewer 与 can_* 层级；没有 parent/shared_with，不开放租户间共享。
- 新脚本 scripts/migrate_fga_skill_permissions.py 默认预览，发布必须显式 apply 与 expected-model-id；只追加 Skill 类型，保留云端其他类型与 conditions。已有不兼容 Skill 定义会拒绝覆盖。
- 发布后读回核对，兼容 OpenFGA 自动补充的空 protobuf 字段。权限模型版本字符串不触发自动升级；发布及服务重启仍为显式运维步骤。
- 独立的 --repair-owners --tenant-id 阶段仅处理目标租户 pending/dead 的 owner write，锁定补偿与当前 Skill 元数据，再校验 id、租户及 created_by；经 PermissionService 写入，读回关系且 can_read 通过后才标记 succeeded。删除或归属不匹配的资源不恢复授权；其他操作不重放。
- 报告采用独占创建，失败时也保留已执行进度；没有数据库 DDL 变更，不放入 Alembic。

## 云端执行与验证

旧模型 01M4BBVZFK17265KMWRX6N3S59；新模型 01M56QABV7KRCXT2NVDPZNPS5X。原模型保留，未覆盖现有其他类型或关系。首次发布的读回校验因服务端补充 computedUserset.object="" 报错，记录新模型 id 后停止；修正默认字段比较并完整比对通过，复用同一个模型，没有重复发布。

本机 API 和专用 Linsight Worker 均已重启，启动日志确认采用新模型。其他远端 API/Celery/Worker 进程未由本次操作重启；已有进程缓存模型 id，需要运维协调重启，固定 model_id 的部署还需更新配置。没有推送 Git 远端或部署远端应用代码。

补偿前 11 条 Skill owner 记录均为 dead；校验后恢复 9 条（Skill id 3–11），状态 succeeded。2 条对应已不存在的 Skill id 1、2，保留 dead 并报告跳过，没有创建悬空授权。重跑仅见这 2 条跳过，不重复授权。

通过当前 API 新建合成测试 Skill remote-skill-fga-1018（id 12），实际 owner tuple 创建成功，failed_tuple 未增加。直接 OpenFGA 高一致性检查 owner/can_read/can_edit/can_manage/can_delete：创建者全部 true，无关用户全部 false。API/平台/客户端健康均 HTTP 200。原有 5 个业务 Skill 的缺失文件仍需找回或重新导入。

## 测试与自审

- 定向模型、迁移、双模型 manager、已有 datasource 发布脚本及 SkillService 回归：131 passed，4 warnings。
- 扩展权限模块回归：668 passed、27 failed；修改前模型同环境对照（排除本次修改/新增模型测试）579 passed、27 failed，失败测试名称集合完全一致，无新增失败。既有问题涉及旧权限测试夹具、配置期望及异步桥接等，不将该扩展套件描述为全绿。
- Ruff format/check 通过；相关文件 arch-guard 无 VIOLATION；git diff --check 通过。
- 自审确认：云端定义保留、发布幂等、严格模型校验、其他租户拒绝、删除/归属变化跳过、真实关系检查后记成功。未使用独立审查 Agent，不声称做了独立审查。
- 未执行 DM8 实机、远端多节点统一发布或普通用户完整浏览器流程；实际权限验证包含 OpenFGA 层所有者允许和无关用户拒绝。

## 操作与回退

具体命令见 src/backend/scripts/README.md 的 Skill 章节。应用环境必须使用与服务相同的 config；先预览、发布模型、更新固定模型配置（若有）并重启服务，再按租户预览/执行 owner 补偿。

回退模型：将相关服务 openfga.model_id 固定为旧模型并重启。旧模型不能评估 Skill 类型，新登记的关系保留但不生效；此为恢复旧行为而非解决 Skill 权限。需要撤销补偿时应基于本次报告逐条核对当前归属，经 PermissionService 显式撤销；不要批量删除其他关系或重置全局重试队列。

本机忽略提交的证据位于 src/backend/.venv/f074-evidence/：fga-skill-plan.json、fga-skill-apply.json、fga-skill-apply-verified.json、fga-skill-owners-{plan,apply,rerun}.json、fga-skill-final-check.json。相关日志位于 .venv/f074-logs/；不提交凭证、配置或完整日志。
