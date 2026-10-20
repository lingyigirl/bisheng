# Skill 对象存储改进：需求发现与范围确认

状态：2026-10-18 用户依次确认需求范围、[规格](skill-object-storage/spec.md)、技术设计；已实施，实际验证与遗留环境问题见 [e2e-report](skill-object-storage/e2e-report.md)。下文是已确认的原始范围，不代表旧环境文件已自动恢复。

## 已核对的现状

- 本分支 SkillStore 用 skills_root 下的目录作为持久存储，数据库 object_path 为磁盘相对路径。切换远端数据库并不会带来对应文件，造成列表可见但实际加载失败。
- 官方 v2.6.0 同样采用磁盘持久化；本次核对的官方 main 已采用 MinIO 文件包为权威存储、内容哈希分版本、本机按需解压缓存。参照官方实现，不直接整体替换模块。
- 当前生产执行入口是 skill_provisioning，将选中且启用的 Skill 文件复制到任务工作区；旧 skill_middleware 明确标记为 dormant。F074 还对成员 Skill 计算摘要，并在恢复时检查变化，不能漏改或改变历史摘要口径。

官方依据：

- https://github.com/dataelement/bisheng/blob/main/src/backend/bisheng/linsight/domain/services/skill_store.py
- https://github.com/dataelement/bisheng/blob/main/src/backend/bisheng/linsight/domain/models/linsight_skill.py
- https://github.com/dataelement/bisheng/blob/main/src/backend/bisheng/core/config/settings.py
- https://github.com/dataelement/bisheng/blob/v2.6.0/src/backend/bisheng/linsight/domain/services/skill_store.py

## 建议确认的范围

1. 导入、创建、更新的 Skill 文件包写入现有 MinIO，数据库记录对象引用及内容版本。对象键按租户、名称、内容哈希隔离，更新不覆盖其他并发版本。
2. API 与 Worker 从同一对象存储读取；每个进程所在机器允许保留可重建的本地缓存。删除缓存后仍能重新下载并完成执行，不依赖共享磁盘。此目标不等同于本机绝不落盘。
3. 详情、文件查看、导出、更新、删除、任务加载与成员 Skill 校验全部适配；不改用户选择 Skill 的交互，不引入新的存储凭据。
4. 旧 Skill 文件提供独立运维迁移流程：默认预览、显式执行、可重复运行、记录缺失和失败；校验对象写入成功后才更新对应元数据，不伪造缺失文件。数据库 Alembic 只负责必要的结构变更，不承担数据搬迁。
5. 缺失原始文件的现有记录保留并报告，需要从原部署取回或重新导入；不通过静默跳过掩盖失败。
6. 保持租户隔离、既有权限入口、工具范围、子 Agent 配置和任务恢复语义；不将本次升级扩展到长期记忆或官方其他功能。

## 影响面、性能、预期结果、风险与回退

**文件与行为**：涉及 SkillStore、SkillService、Skill 元数据模型/必要的 Alembic 结构迁移、配置、skill_provisioning、AgentConfigService 及其 repository、旧迁移脚本和相关测试。正式设计阶段列出实际调用清单，避免只替换 SkillStore 导致签名不匹配。沿用当前分支和既有功能归属，必要时补充版本契约，不抢占其他 Feature 的对象归属。

**性能**：写入时单次打包并上传，首次读取下载解压，同一内容版本命中本机缓存时不发对象存储请求；同一任务使用批量元数据查询、复用下载结果，阻塞磁盘/MinIO 操作不占用异步事件循环。设置压缩包大小、解压大小、路径安全上限，验证并发填充缓存，避免半包和重复下载。缓存容量管理、过期版本清理需给出明确策略；不在写入热路径删除其他版本。

**结果与验收**：在同一 MinIO 上用两套独立缓存模拟不同机器；A 导入后 B 无原文件也能查看、导出、执行。覆盖中文文件名、二进制资源、同名跨租户隔离、并发更新、缓存丢失和损坏、对象不存在、存储故障及旧数据迁移。实测主 Agent/子 Agent 读取 Skill，保留断点恢复的摘要变化拦截；最后在当前云端中间件和浏览器联调，测试数据采用明确标记。

**风险与回退**：属于存储/元数据契约升级，必须协调 API 与 Worker 版本。旧版本不能直接读取只存在 MinIO 的新记录，不能把回滚代码描述成无条件可行。保留旧磁盘文件和迁移前元数据清单，优先前向修复；需要回退时按设计中的导出/恢复步骤先恢复旧版可读的文件与引用，再回滚进程。迁移或对象读取失败应明确失败且可重试，不删除唯一副本。上线不自动清空共享对象或旧文件。

## 流程与下一步

本改动改变跨 API/Worker 的存储及元数据契约，属于 docs/SDD-Guide.md §1 的 Full SDD。按 §3，需求发现、spec、design 分别需要确认。当前先确认上述范围；之后形成可验收的 spec，再完成设计与任务清单、实现及测试。
