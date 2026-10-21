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

结果：**232 passed**。

覆盖：

- `output/` 优先级高于工具链目录；
- 字体、build、target、coverage、测试报告、LaTeX 辅助文件和临时文件仅在 scratch 下跳过；
- 未知大型 scratch 持久化，并可从本地缓存或 WorkspaceBackend 通过 `required_files` 补传；
- 模型工具 schema 暴露 `required_files` 并透传到执行器，符合路径保持 workspace-relative；
- keep-alive 收到 E2B sandbox 404 时在任何 copy-in 前进入一次性重建，不再退化为 `10053`；
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
| T49 | PASS | 定向集成回归 232 passed；旧字段兼容和恢复安全均保留。 |

## 特性级代码审查（/code-review）

- 边界条件：PASS；空集合、未变化输入、单文件失败、流中断、大小不一致和超大文件路径均有覆盖。
- 权限与认证：N/A；本次不新增外部接口或资源授权路径。
- 并发安全：PASS；代码执行仍同步串行处理 copy-out，没有引入代码执行重放。
- 信息泄漏：PASS；只记录路径、大小和错误摘要，不记录代码或文件内容。
- 测试覆盖：PASS；核心 Service/执行器路径和恢复边界均有离线回归。
- 代码风格：PASS；产物规则集中在单一模块，未复制多套判断。
- 文档同步：PASS；spec、design、tasks 和本验证文档已同步。

## 真实 E2E

用户针对本轮明确批准了 API/Worker 重启及共享 MySQL/Redis/E2B 验收；未执行
迁移、DDL/DML、数据修复或其他数据库内容操作。

### 运行环境

- 重启本地 API 与 Linsight Worker，使用既有远端中间件配置。
- API `http://127.0.0.1:7860/health`、管理端 `:3001`、工作台 `:4001/workspace` 均为 HTTP 200。
- Worker 的 Redis、MySQL、OpenFGA 初始化成功；测试结束后 Worker 以正式
  `timeout=3600` 配置重新启动，临时 10 秒故障注入已还原且未进入提交。

### 普通产物回收

工作台任务 trace：`bf6aafecac29451b8c4b8084b87aeaf9`。

同一代码调用生成 5 个文件：

| 路径 | 大小 | 预期 | 实际 |
|---|---:|---|---|
| `output/e2b-policy-large.bin` | 6,291,456 | 持久化 | `persisted=true` |
| `output/e2b-policy-chart.png` | 317 | 持久化 | `persisted=true` |
| `scratch/e2b-policy-large-state.bin` | 6,291,456 | 持久化、非产品 | `persisted=true`, `storage=workspace` |
| `scratch/fonts/e2b-policy-font.otf` | 512 | sandbox-only | 未持久化 |
| `scratch/build/e2b-policy-cache.bin` | 1,024 | sandbox-only | 未持久化 |

Worker 日志：

```text
E2B run phases: execute=7.110s collect=9.692s persisted=3 failed=0
```

Copy-out 只出现 3 个文件读取请求：两个 `output/` 和一个未知大型
`scratch/`；没有 `scratch/fonts` 或 `scratch/build` 的 `/files?path=` 请求。

工作台“工作区”只展示两个 `is_product=true` 的下载项，符合产物过滤语义。
浏览器实际下载并核对：

| 下载文件 | 字节数 | SHA-256 |
|---|---:|---|
| `e2b-policy-large.bin` | 6,291,456 | `1f3a2c46f47bd900ef863a5db1412bf0e38a9ae9e46a660eeec820439f4036a1` |
| `e2b-policy-chart.png` | 317 | `d0e75f9781c94cea4910b74246e72926467250d254b60f50be6e551f7bece471` |

两者均与沙箱工具返回值完全一致；PNG 的签名、IHDR/IDAT/IEND chunk 与 CRC
已由生成代码校验。

### Sandbox 自然过期与恢复

为验证真实过期路径，本地测试进程临时把初始 sandbox TTL 设为 10 秒，并仅让
重建后的测试 sandbox 获得足够完成 6MiB copy-in 的 300 秒窗口。两处测试注入
随后已移除，正式 Worker 已恢复 3,600 秒 TTL。

工作台任务 trace：`29d39726ccd942028c48d148cf1e327b`。

1. 第一次调用生成 `scratch/e2b-recovery-final-pass2.bin`：
   - 大小：6,291,456 字节；
   - SHA-256：`07d935b0bceac315c31166447ca335053764a53d17a4151a3737ad6fd3f62870`；
   - copy-out：`persisted=1 failed=0`。
2. 第二次调用在 keep-alive 时收到旧 sandbox `i9rd...` 的明确 404。
3. 执行器在 copy-in 前重建为 sandbox `ixzfty...`，没有把后续失败误报成
   无上下文的 `10053`。
4. `required_files=["scratch/e2b-recovery-final-pass2.bin"]` 完成回灌：
   - `prepare=5.042s`；
   - `execute=6.086s`；
   - `collect=0.708s`；
   - 第二次读取大小仍为 6,291,456 字节；
   - SHA-256 仍为 `07d935b0...f62870`；
   - 输出 `match: True`、`RECOVERY_OK`。

### 恢复边界

- 跨用户轮次会创建新的 session workspace；`scratch/` 按既有设计不从前一轮
  自动复制，因此跨轮恢复不能作为同一 sandbox 重建证据。
- 在人工 `ask_user` 暂停后强杀整个 Worker，系统按 AC-24 保持“上次有状态工具
  结果无法确认”的自动续跑阻断。该行为是预期安全边界，不是本次文件恢复失败。
- 本验证不自动重放工具；sandbox 消失发生在下一次代码执行开始前，才允许一次
  重建/重试。

## 当前结论

离线 **232 passed**，真实 E2B 的常规回收、浏览器下载、字体/build 跳过、
6MiB 大 output、未知 scratch 持久化、sandbox 自然过期重建和大型
`required_files` 回灌均通过。已知限制是 E2B 公开 `files.write` 仍会将
required-file multipart 内容读入 Worker 内存；本次只消除了 copy-out 的大文件
内存峰值。未执行迁移或数据修改，未推送远端。
