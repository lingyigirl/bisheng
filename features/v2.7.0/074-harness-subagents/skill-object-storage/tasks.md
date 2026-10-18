# Skill 对象存储执行记录

## 文档状态

| 文档 | 状态 | 依据 |
|---|---|---|
| 需求发现 | 已确认 | 2026-10-18 用户“继续” |
| spec.md | ✅ 已评审、用户已确认 | 2026-10-18 用户“确认”；spec-review.md |
| design.md | ✅ 已评审、用户已确认 | 2026-10-18 用户“确认” |
| tasks.md | ✅ 已拆解、自审通过 | tasks-review.md |

## 实施任务

- [x] T-01 基础设施：模型 content_hash、settings.skills_cache_dir；加法列保留可回退，旧引用保留。
- [x] T-02 基础设施：当前 head=f074_recovery_scan_index 后的 DDL-only revision，fresh/旧表守卫与单 head；依赖 T-01。
- [x] T-03 后端测试：test/linsight/test_skill_object_storage.py，共享 fake MinIO 与独立缓存，完整性、并发、故障、路径保护。覆盖 AC: AC-01, AC-02, AC-03, AC-06, AC-07, AC-09, AC-10, AC-19, AC-20, AC-21, AC-22。
- [x] T-04 后端 Domain：skill_store.py 的 BundleRef、确定性 ZIP/哈希与对象读写；依赖 T-03。
- [x] T-05 后端 Domain：skill_bundle_cache.py 的有界读取、完整性与原子缓存、同进程单飞；依赖 T-04。
- [x] T-06 后端测试：test_skill_repository.py 的条件发布、迁移 CAS 与租户隔离。覆盖 AC: AC-06, AC-07, AC-13, AC-16, AC-19。
- [x] T-07 后端 Domain：skill_repository.py，Service 无直接 ORM，条件内容更新；依赖 T-06。
- [x] T-08 后端测试：test_skill_service.py、test_skill_api.py 适配版本存储，明确缺包与导出行为。覆盖 AC: AC-01, AC-02, AC-05, AC-07, AC-08, AC-13, AC-17。
- [x] T-09 后端 Domain/API：skill_service.py 适配发布引用与线程 IO，复用现有端点；依赖 T-07、T-08。
- [x] T-10 Worker 测试：test_skill_provisioning.py、test_agent_config_service.py 验证未选/停用拒绝、内容迁移恢复稳定和损坏拒绝。覆盖 AC: AC-04, AC-08, AC-11, AC-12, AC-19。
- [x] T-11 Worker：skill_provisioning.py、agent_config_service.py 读取批量元数据与完整包，沿用 Worker 已恢复的 tenant ContextVar；依赖 T-09、T-10。
- [x] T-12 后端测试：test_migrate_skill_bundles.py 默认预览、重跑、缺包、并发冲突及回退。覆盖 AC: AC-14, AC-15, AC-16, AC-17, AC-18, AC-21, AC-22。
- [x] T-13 后端运维：scripts/migrate_skill_bundles.py 与 scripts/README.md，租户显式选择、逐项读回核验和条件发布/恢复；依赖 T-07、T-12。
- [x] T-14 后端运维：migrate_sop_to_skill.py、seed_overflow_skill.py 适配新版引用；依赖 T-09。
- [x] T-15 后端回归：旧 store、GitHub 导入、dormant middleware 的测试适配；依赖 T-04、T-09、T-11。
- [x] T-16 文档：运维 runbook 与 F035 design §7 指向新契约，记录缓存/历史版本清理、协调升级与回退；依赖 T-13。
- [x] T-17 验证：ruff、arch-guard、迁移单 head、test/linsight 全量及邻域回归，记录环境性失败；依赖 T-15。
- [x] T-18 集成/E2E：云端 MinIO 匿名策略/跨缓存/主成员任务、浏览器导入查看导出；证据忽略保存，仅操作标记测试 Skill。覆盖 AC: AC-01, AC-02, AC-03, AC-04, AC-05, AC-08, AC-11, AC-12, AC-18, AC-19。依赖 T-16、T-17。
- [x] T-19 评审与交付：按 code-review 自审差异，修复发现项，写最终实测边界，仅本地提交；依赖 T-18。

## 已执行

- 读取官方与当前 Skill 存储实现及所有生产调用链，明确磁盘旧契约、任务白名单复制、成员恢复摘要差异。
- 完成需求发现及规格；更新版本契约，保持 Skill 对象归属 F035。
- 完成目标设计文档；业务代码、增量字段与标记测试包已经更新；未批量迁移或改写原五条缺失业务包。

## 实际偏差记录

- 旧 dormant middleware 保持不接入生产；其遗留磁盘夹具保留用于兼容读取回归。实际生产对象存储加载由 provisioning 与 AgentConfigService 完成。
- 历史对象清理通过停写维护手册提供，本期不新增自动清理任务或在线 GC 端点。
- 真实云端 owner tuple 写入暴露现有 OpenFGA 缺类型问题，按既有补偿处理；目标环境发布限制已记录于 e2e-report.md。

## 验证记录

- 全量 Linsight + Alembic 单 head：1011 passed、8 skipped；最终缓存边界/结构守卫定向回归 19 passed，非累计计数。
- 管理/存储/迁移/运行时邻域：171 passed。ruff 无非 Unicode 类问题；arch-guard 无 VIOLATION，已知 settings WARNING 保留。
- 云端跨缓存、匿名拒绝、主/成员真实执行、MinIO 产物核验、浏览器导入/预览/导出及迁移回退均已执行，详见 e2e-report.md。
- DM8 实机、多台物理节点协调发布与生产全量迁移未执行；不列为已通过。
