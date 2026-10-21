# E2B 产物分类与流式回收验证（2026-10-21）

## 范围

验证 [design.md](./design.md) §12 的 AC-20～AC-24：

- `output/` 无论大小和目录名称均持久化；
- scratch 明确临时产物不传输，未知 scratch 不论大小均持久化；
- 目录在递归遍历前剪枝；
- 选中文件使用 E2B 流式读取和 WorkspaceBackend 流式落盘；
- 单文件失败可继续并明确返回状态；
- 代码执行未知结果仍阻断自动恢复。

## SDK 预验证

- `EntryInfo` 含 `size`、`modified_time`、`path`、`type`。
- `files.list(path, depth, request_timeout)` 支持显式超时。
- `files.read(path, format="stream", request_timeout=...)` 返回按块字节流。
- MinIO 存储封装接受文件对象并计算长度，可承接已校验的本地临时文件。

## 自动化结果

命令：

```powershell
cd src/backend
uv run pytest test/linsight/test_code_interpreter_artifact_policy.py `
  test/linsight/test_e2b_copy_in_out.py `
  test/linsight/test_workspace_backend.py `
  test/linsight/test_init_config_tools.py `
  test/linsight/test_final_result_selection.py `
  test/linsight/test_crash_recovery.py `
  test/linsight/test_code_interpreter_output_path.py `
  test/linsight/test_code_interpreter_root_relocation.py `
  test/linsight/test_code_interpreter_timeout.py `
  test/linsight/test_workspace_original_sync.py `
  test/linsight/test_builtin_roles.py -q
```

结果：**227 passed**。

覆盖：

- `output/` 优先级高于工具链目录；
- 字体、build、target、coverage、测试报告、LaTeX 辅助文件和临时文件仅在 scratch 下跳过；
- 未知大型 scratch 持久化，并可从本地缓存或 WorkspaceBackend 通过 `required_files` 补传；
- E2B 只读取选中文件，使用 stream 和 300 秒请求超时；
- 单文件失败不阻断其他文件，返回 `persisted=false` 和错误；
- 大型 `required_files` 可从本地 `file_dir` 或 WorkspaceBackend 补传；
- WorkspaceBackend 在完整流和大小校验后才上传 MinIO；
- 源流、上传或大小校验失败时不会发布半截文件；
- 旧字段 `path/size/is_product` 保持存在；
- 有状态代码工具恢复阻断回归通过。

其他门禁：

- Ruff 增量 `E4/E7/E9/I`：通过；
- `scripts/arch-guard.sh`：无 VIOLATION；
- `git diff --check`：通过。

## 任务级审查（/task-review）

| 任务 | 结果 | 说明 |
|---|---|---|
| T38～T42 | PASS | 测试先行，AC 标注完整；测试仅使用 fake sandbox、fake MinIO、fakeredis 或临时目录。 |
| T43～T48 | PASS | 分层、命名、序列化、信息泄漏和设计同步检查通过；未新增数据库、权限或 API 契约。 |
| T49 | PASS | 定向集成回归 227 passed；旧字段兼容和恢复安全均保留。 |

## 特性级代码审查（/code-review）

- 边界条件：PASS；空集合、未变化输入、单文件失败、流中断、大小不一致和超大文件路径均有覆盖。
- 权限与认证：N/A；本次不新增外部接口或资源授权路径。
- 并发安全：PASS；代码执行仍同步串行处理 copy-out，没有引入代码执行重放。
- 信息泄漏：PASS；只记录路径、大小和错误摘要，不记录代码或文件内容。
- 测试覆盖：PASS；核心 Service/执行器路径和恢复边界均有离线回归。
- 代码风格：PASS；产物规则集中在单一模块，未复制多套判断。
- 文档同步：PASS；spec、design、tasks 和本验证文档已同步。

## 真实 E2E（待执行）

执行真实 E2E 需要重启加载新代码的 Linsight Worker；Worker 会连接共享 MySQL/Redis，因此必须先获得该次数据库操作批准。

1. 重启 API、Linsight Worker，保留现有管理端和工作台。
2. 在工作台提交短图表任务，要求使用 `chart-designer`。
3. 确认执行日志没有 `scratch/fonts` 文件读取或上传。
4. 确认 PNG 进入 `output/`，工作区可预览且 MD5 与 MinIO 对象一致。
5. 生成大于 5MB 的未知 scratch 文件并保存到 `required_files`，重建沙箱后执行读取。
6. 生成大于 5MB 的正常 output 文件，确认流式落盘、下载和预览。
7. 注入单文件上传失败，确认工具结果是部分失败而非全部成功。
8. 停止沙箱后的未知执行结果仍显示恢复阻断，不自动重放代码。

## 当前结论

离线实现与逻辑验证通过，真实 E2B 网络性能和浏览器预览仍待数据库审批后的现场验证。本阶段未连接共享数据库，未执行迁移，未提交或推送。
