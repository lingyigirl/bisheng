# Skill 对象存储运维手册

## 存储与前提

新导入/编辑的自定义 Skill 包存于现有 MinIO 配置桶的 `linsight/skills/{tenant_id}/{name}/{content_hash}.zip`。数据库保存元数据及版本引用，本机 `skills_cache_dir` 仅为可重建缓存，默认位于进程 CACHE_DIR 下的 linsight_skills。旧 `skills_root` 是未迁移文件的只读来源，不再接收新写入。

上传上限 10 MiB、解压总量 100 MiB、最多 4096 个文件，路径 UTF-8 最长 512 字节；禁止不安全路径、符号链接和冲突文件名。内部 ZIP 大小最多 110 MiB。超限旧包需要人工拆分或调整内容后重新导入，不能强行跳过保护。

API、Worker 必须使用支持对象版本的协调版本和相同 MinIO。私有 Redis 队列不隔离 Skill 元数据。默认桶的匿名图片策略不包含 Skill 前缀；仍须核对部署实际策略，并确认 Skill 对象匿名访问被拒绝。不要将缓存目录挂载成共享卷，也不要向用户公开对象下载地址代替授权导出。

## 上线与迁移

1. 暂停 Skill 编辑和新任务提交，确认全部相关 API/Worker 的发布窗口；等待任务结束或进入受控可恢复状态。保存元数据、旧文件及运行配置备份。当前本机开发服务无法代替运维确认其他远端实例已协调停写。
2. 发布兼容版本。标准启动器先运行 `alembic upgrade head`；直接 uvicorn 必须另行执行迁移。新增 revision 为 `f035_skill_object_storage`，只添加可空 content_hash 列，不自动搬迁文件。
3. 在持有旧文件的机器准备路径；从 `src/backend` 执行以下命令，使用与服务相同的 config 环境。Windows 将 python 换成 `.venv/Scripts/python.exe`，Linux 可用 `.venv/bin/python`。

```bash
python scripts/migrate_skill_bundles.py --tenant-id 1 --legacy-root /old/skills --report /protected/skills-preview.jsonl
python scripts/migrate_skill_bundles.py --tenant-id 1 --legacy-root /old/skills --report /protected/skills-apply.jsonl --apply
```

租户必须显式指定；报告路径每次新建，拒绝覆盖先前审计报告。JSONL 每条包含记录 ID、租户、名称、迁移前引用、状态及失败类别，不含正文或密钥。保护报告的访问权限；Windows 应按运行账户设置目录 ACL，不能仅依赖 Python 文件 mode。

状态：`pending` 待迁移、`migrated` 已完成、`already_done` 对象核验通过、`missing` 缺少源文件、`conflict` 并发变化、`failed` 格式/存储等失败。缺失/冲突/失败使命令退出非零，其他记录仍继续。每项对象都上传并独立读回核验，再条件更新数据库；旧文件不删除。重跑仅处理仍未完成的记录，不覆盖管理员并发更新。

4. 缺失项从原部署取得真实文件，或通过同名更新文件包恢复。只有名称和描述不能重建正文；请勿创建占位包把 missing 改为成功。
5. 在独立空缓存节点读取、导出并执行标记 Skill，验证主/子 Agent；确认失败记录已处理后开放业务提交。无需迁移未使用的缺失记录来验证其他 Skill，但不得宣称缺失记录已可用。

## 回退

回退前暂停写入和相关任务，先恢复旧版能读的文件及引用，再回滚全部 API/Worker；只回滚代码无法读取 MinIO-only 新包。保留云端包和旧源文件，不自动 drop 新列。

```bash
python scripts/migrate_skill_bundles.py --tenant-id 1 --restore-legacy /restored/skills --report /protected/skills-restore-preview.jsonl
python scripts/migrate_skill_bundles.py --tenant-id 1 --restore-legacy /restored/skills --report /protected/skills-restore-apply.jsonl --apply
```

首次预览列出 `pending_restore`。显式 apply 将当前云端版本完整写入旧布局，目标已有不同内容时返回 conflict；核验后条件恢复旧目录引用和空 content_hash，已完成项可重跑。`/restored/skills` 必须成为回滚服务的 skills_root，或按旧架构挂载到各节点；不要把另一个临时目录的完成误认为旧实例已有文件。源缺失及被删除记录需要结合备份处理，不能由脚本凭空恢复。

## 缓存与历史对象清理

本期不提供后台自动淘汰任务。监控本机缓存根的磁盘容量；缓存可全部重建，旧磁盘源文件则可能仍是未迁移包的唯一副本，两者不能混淆。

清理缓存前停对应节点及其子进程，确认无活跃读写，验证解析后的绝对路径确实为所配置缓存根；只清理该根下版本目录与临时目录。不得递归删除 skills_root、用户上传目录或任意由请求提供的路径。服务恢复后会按需从对象存储重建。

Skill 删除只撤销元数据与后续选择权限，已有缓存不绕过数据库；MinIO 历史版本暂不物理删除，以保留并发安全及故障核对能力。需要回收云端对象时由运维执行独立维护操作：

1. 停止所有 Skill 发布/迁移/恢复操作及引用这些包的任务，核验备份。
2. 导出每个租户的全部有效对象引用，与 `linsight/skills/` 对象清单比对，先输出候选清单供人工核验；至少保留最近 7 天的对象。
3. 删除前重新查询数据库引用，只处理持续未引用的候选，留下操作记录。无法确认其他节点停写时不执行清理。

没有协调停写的条件下，不承诺在线清理安全。本次升级不会自动清空旧目录或生产对象。

## 排障

- 列表存在但详情 11053：核对是否旧记录且缺少源文件，或对象确实不存在。
- 11051：核对包格式、路径、版本引用/完整性或并发更新冲突；重新加载最新详情后再编辑。
- 成员任务 11071：所选 Skill 缺失、损坏、停用/删除或发生恢复摘要变化；不要修改旧任务快照强行续跑。
- OpenFGA 报 `type 'linsight_skill' not found`：目标环境授权模型缺少既有 Skill 类型，owner tuple 写入会进入 failed_tuples 补偿，需按该环境权限模型发布流程补齐后重试。不能将管理员任务跑通等同于 owner 授权补偿已完成；本次未修改共享 OpenFGA 模型。
- MinIO 连接/鉴权故障：属于存储异常，修复服务和凭据后重试；不会回退到本地持久化发布。
- 缓存损坏：读取时重新下载并核验；若云端副本也错误，则拒绝加载，找回正确文件重新发布。

日志和报告不应包含登录 Cookie、MinIO 密钥、完整 Skill 正文或含凭据的 URL。真实集成证据和运行限制见 e2e-report.md。
