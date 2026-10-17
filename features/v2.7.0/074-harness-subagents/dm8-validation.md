# F074 DM8 实库专项验证

日期：2026-10-14。结果：本次配置字段迁移与读写专项通过；不等于整个平台 DM8 全量部署通过。

## 环境

- 官方下载介质：https://download.dameng.com/eco/dm8/dm8_20241022_x86_rh6_64_single.tar
- 镜像 dm8_single:dm8_20241022_rev244896_x86_rh6_64，x86_64 Linux，服务器报告 DM Database Server 64 V8。
- 独立容器 jinbao-f074-dm8，端口仅绑定 127.0.0.1:15236，独立卷 jinbao-f074-dm8-data；CASE_SENSITIVE=1、UNICODE_FLAG=1、PAGE_SIZE=32。
- 使用项目锁定的 dmPython / dmSQLAlchemy 驱动，分别验证 dm+dmPython 与 dm+dmAsync。
- 原 MySQL、API、Worker 配置未切换。测试密码、下载镜像与执行日志保存在忽略目录 .venv/f074-dm8-*。

## 实际测试

新增 test/linsight/test_harness_dm8_live.py，必须显式设置 F074_DM8_URL，账号名以 F074_ 开头且 schema 为空，否则拒绝执行；默认无环境时跳过。

在最小旧版 linsight_session_version 表上，调用真实 F074 migration.upgrade()，并执行真实 SQL；未 mock 数据库或 JsonType。测试按生产 env.py 为此 add-column 操作注册非事务 DM Alembic 实现，不执行整个平台 env.py。

通过项：

1. 真实新增 nullable agent_config 字段。
2. 连续执行 upgrade 两次，第二次不重复添加字段。
3. 旧记录配置保持 NULL，旧查询仍可读取全部记录。
4. 独立连接写入并读回包含中文、日文、英文、嵌套数组及大于 32 KB 的 JSON 配置，内容逐项一致。
5. 同一配置经异步 dmAsync 驱动读取，内容一致。
6. 更新为空成员列表后读回一致，另一条不同 tenant_id 记录不被改动（这是精确主键更新验证，不宣称 tenant 自动过滤已完整验收）。

结果：真实专项 1 passed，内部包含以上断言；没有将单个测试拆报为多个通过测试。

## 发现与限制

- dmSQLAlchemy 在本镜像反射该大文本字段为 TEXT/_DMText；不是测试最初预期的字面 CLOB。实际大文本同步/异步往返通过，不根据类型名称单独判断失败。
- 仅授予 RESOURCE 的测试账号无法查询驱动所需 SYS.SYSCOLUMNS。现有反射 helper 会吞掉查询异常并返回字段不存在，导致重复升级报列已存在。SYSDBA 直接授权该系统表也被服务端拒绝。本次改用仅属于该隔离实例的 DBA 迁移账号后通过；生产环境需 DBA 确认驱动所需目录权限，不能直接照搬为应用账号提权。
- 测试仅证明 F074 字段变更及 JsonType 存储兼容性；完整模型创建、所有历史迁移链、整个平台接口、权限和性能仍需单独 DM8 集成验收。
- 没有执行真实数据的 downgrade/drop-column，不把破坏性降级作为正常回滚。
- 所有改动本地保留，未推送远端。

## 恢复字段增量（2026-10-14）

已在新的隔离 F074_REC_* schema 中实际执行 f074_agent_config 与 f074_recovery 升级，各执行两次；旧记录 recovery_epoch 默认为 0，新增 recovery_state 通过 JsonType 写入并读回 attempts/workspace_epoch/phase。同步/异步 agent_config 大文本验证仍通过。该组合专项结果为 1 passed。测试账号凭据继续仅保存在忽略目录；应用仍使用独立 MySQL 联调库。
