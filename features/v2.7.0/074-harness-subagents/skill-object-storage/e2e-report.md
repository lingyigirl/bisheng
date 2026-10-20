# Skill 对象存储验证（2026-10-18）

## 结果

已实施 MinIO 权威存储、不可变内容版本、本机完整性缓存、旧目录只读兼容、管理与任务加载适配、独立迁移/回退脚本。当前本机 API 与专用 Worker 已启用新代码，仍连接云端中间件，没有推送 Git 远端，也没有升级其他远端节点。

数据库已实际执行 `f074_recovery_scan_index → f035_skill_object_storage`，只添加可空 content_hash 列。Alembic 单 head 及 fresh metadata / 旧表守卫测试通过；没有在 revision 中搬迁业务数据。

| 项目 | 实测 |
|---|---|
| 核心/邻域单测 | 171 passed，覆盖管理 API、存储、CAS repository、迁移、摘要兼容、租户及任务加载 |
| 全量 Linsight + 单 head | 1011 passed、8 skipped；随后缓存读取边界与迁移守卫最终定向回归 19 passed。后者与全量有重叠，不相加计数 |
| 静态检查 | 改动 Python 文件格式化完成；ruff 无非 Unicode 类问题，40 条既有 Unicode 提示保留；arch-guard 无 VIOLATION，settings 的既有敏感常量 WARNING 未新增 |
| 实际上传/导出 | API 导入含 SKILL.md、证据附件、256 字节二进制的包；导出与原文件逐字节一致 |
| 跨缓存读取 | 独立节点 B 缓存根、无旧目录也能从 MinIO 读取；删除 B 缓存后重建并逐字节核对。为同机独立缓存模拟，未部署第二台物理机器 |
| MinIO 权限 | 核对实际匿名策略只开放图片前缀；已知 Skill 对象匿名 GET=403 |
| 主 Agent | d49a075fd6e3409090e41202e9e59603 completed，实际 read_file 两次，工具输出含 REMOTE-SKILL-CROSS-NODE-OK |
| 子 Agent | faf8c8537f194c6788c7c05ce16426e6 completed，指定 storage-reader 读取 Skill 与附件；主 Agent 整合并交付文件。原始事件 5 条 read_file 完成回执含实际证据，最终产物从 MinIO 读回并校验 MD5 |
| 浏览器 | 工作台技能管理打开详情/正文/附件，显示标记；实际点击导出并核对下载包；另通过本地文件导入入口提交 REMOTE-SKILL-UI-1018，详情显示正确正文；工作区刷新可见成员任务完成及交付文件 |
| 实际迁移/回退 | 仅对标记的 REMOTE-SKILL-MIGRATE-1018 执行云端→旧目录恢复→预览→迁移→重跑；版本哈希及 F074 旧摘要均一致，源文件保留，API 详情可读 |
| CLI 预览 | 只读扫描当前租户，5 条既有业务包缺失、3 条新测试包已核验；缺失项返回非零，不修改既有记录。独立 CLI 的事件循环关闭连接告警已修复并复核 |
| 收尾状态 | 两个任务均 completed，专用队列长度 0，API/两个前端 HTTP 200 |

## 环境限制与未验收项

- 既有 5 个业务 Skill 仍只有元数据、源文件缺失。升级不能凭名称生成正文，未创建占位文件，也未将这些记录伪装为迁移完成。
- 云端 OpenFGA 当前模型缺少 linsight_skill 类型，创建测试 Skill 的既有 owner tuple 逻辑返回类型错误并记录补偿。未绕过 PermissionService 或擅改共享模型；需要按目标环境权限发布流程补齐模型再重试补偿。本次管理员 API/执行与租户隔离测试不代表完整 owner 授权联调通过。
- 实际确认的是本机 API/Worker 连接云端中间件；按用户确认，云端没有 FastAPI 实例。若其他部署存在后端进程，需先核实再按 runbook 协调发布，不能把通用部署建议当作现有服务清单。
- DM8 实机、真实多机部署、全局生产迁移/回退、生产对象 GC 未执行。结构使用通用 String 与既有双库守卫，DM8 仍需中央回归。
- 不涉及长期记忆、沙箱内存快照或外部工具副作用恢复。
- 收尾时曾发现本机 API 无监听、工作区进入登录页；恢复 API/Worker 后接口与浏览器正常，持久任务仍完成。日志未给出可确认的退出原因，未将其归因于 Skill 逻辑，也不将本次功能冒烟冒充长期稳定性压测。

## 证据与测试数据

真实数据位于 `src/backend/.venv/f074-evidence/`，日志位于 `.venv/f074-logs/`，均忽略提交；不提交配置、Cookie、密钥或完整运行日志。

- skill-object-storage-before.json、skill-object-storage-e2e.json：升级前结构、实际策略与跨缓存证明。
- skill-storage-final-check.json：真实工具回执、云端产物/浏览器导出内容与健康。
- skill-migration-e2e.json、skill-migration-preview3.jsonl：真实迁移/回退与只读预览。
- review-1018-skill-object-{main,member}-{task,final}.json：模型任务及过程记录。
- skill-object-storage-detail.jpg、skill-object-storage-ui-import.jpg、skill-object-storage-member.jpg：浏览器证据。
- skill-storage-final-regression.log、skill-storage-unit3.log、skill-storage-final-safety.log：测试结果。

仅保留带 REMOTE 标记的测试 Skill 三条：remote-skill-storage-1018、remote-skill-migrate-1018、remote-skill-ui-1018，供复核。原有五条记录未改内容/存储引用。
