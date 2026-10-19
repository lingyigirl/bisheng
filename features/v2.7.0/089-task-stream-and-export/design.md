# Design: 任务正文流式展示与报告交付修复

**关联**：[spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**：v2.7.0
**最后更新**：2026-10-19
**状态**：已确认并实施；本文为实现快照。实测与尚未完成的预算见 performance-report.md、e2e-report.md，不以预算代替测量。

## 1. 目标与非目标

补齐任务模式普通 assistant 正文的实时展示，使主 Agent、前台/后台成员、恢复和历史展示保持一致。统一智能体工具与“另存为”的报告资源、Word/PDF 转换及产物核验，控制长输出、重复转换和并发导出的成本，同时支持 Linux 与 Windows。

不增加新的模型、UI 或状态管理库，不改变模型调度、外部副作用恢复规则、长期记忆或业务报告事实判断。不新增 XLSX/PPTX 通用转换器；其现有产物只增加一次性、可复用的格式完整性检查。

## 2. 关键约束

遵循 [constitution.md](../../../docs/constitution.md) C1–C7、[版本契约](../release-contract.md) 和根/子项目 AGENTS.md。既有对象归属继续为 F035/F074，不新增数据库表、列或迁移。

- 十种 MessageEventType 保持不变；新增正文作为既有 task_execute_step 的一种语义，不把普通回答标记为 thinking。
- 新版任务继续先记录 Redis Stream 再由 WebSocket 读取；旧任务的旧队列与终态契约继续可用。页面订阅不拥有任务执行生命周期。
- 原会话、原文件只读保留；验证使用独立任务和合成样例，不改动共享中间件配置。
- 新客户端创建任务时提交可选能力标记 `client_capabilities=["assistant_text_v1"]`；服务端将 `agent_config.text_protocol=1` 固化到新任务快照，只有该任务发布 assistant 步骤。省略能力标记的旧客户端、旧任务保持旧行为，不能给旧页面直接投递未知正文步骤。旧服务端忽略该可选标记时，新页面仍可接收原 final_result。
- common 层只接受已解析的文本/二进制资源，不读取领域对象、数据库或任意 URL。授权、工作区定位与缓存由领域服务负责。
- 修改超 600 行的源文件时，提取职责到小模块，不能继续扩张现有 mapper、状态管理器或前端 WebSocket 大文件。触及的前端中文按既有 i18n-localizer 规则清理，新词条同改三语。

### 2.1 性能预算与样例

统一采用可重放的合成模型增量，排除模型供应商等待；网络传输另外记录。测试记录 CPU、内存、OS、Python/浏览器/LibreOffice/字体版本。Linux 与 Windows 使用相同输入，分别出报告，不从本机高性能 CPU 推断信创设备表现。

| 样例/指标 | 拟定验收预算 | 测量边界 |
|---|---|---|
| 3 个成员，每个 30 个文本增量/秒，共 2 分钟 | 接收模型增量到写入可订阅事件 p95 ≤ 500ms；本机 WS 收到到界面提交 p95 ≤ 200ms | 后端/浏览器分段计时，不跨机器直接相减未校时的时钟 |
| 首个非空正文 | 本机端到端 p95 ≤ 1 秒 | 从后端收到正文开始，非从提交问题开始 |
| 正文持续生成 | 每任务常态 UI 提交 ≤ 10 次/秒，语义边界立即提交；尾部丢失数 0 | 统计实际状态提交；不通过丢字达标 |
| 10000 条执行事件、2MiB 累计文本的历史快照 | 本地投影 p95 ≤ 2 秒，输入/滚动事件处理 p95 ≤ 100ms | 快照下载时间单列；后台分批构建投影 |
| 打开/切换会话 20 次 | 卸载后旧连接/计时器/监听器归零；强制 GC 后额外 retained heap ≤ 20MiB | 同一输入、同一浏览器，记录基线；CPU 4 倍节流、禁用 GPU 另测 |
| 10 页、2 图、5 表、约 100KiB 文本 | 热环境 DOCX ≤ 10 秒，PDF ≤ 30 秒；首次 PDF 含渲染进程冷启动 ≤ 120 秒 | 源资源已可读取；网络读取/排队单列 |
| 相同产物重复转换/下载 10 次 | 内容未变时真正转换和完整核验各只执行一次；本地命中响应 ≤ 2 秒 | 不含大文件网络下载；每次仍做授权及版本检查 |
| 并发导出时聊天 | 本地健康/API 事件循环延迟 p95 ≤ 100ms；流式延迟仍满足上表 | 与无导出对照，不用提高超时掩盖阻塞 |

初始容量：markdown ≤ 2MiB、最多 50 张唯一图片、单图压缩体积 ≤ 10MiB、总输入资源 ≤ 64MiB、单图解码 ≤ 4000 万像素；完整产物核验 ≤ 64MiB、解包展开 ≤ 256MiB、最多 10000 个 ZIP 条目。输入超限明确失败，不截断后当成功交付。

每部署节点默认同时运行 2 个重转换/核验子进程、最多 4 个等待者；排队最长 15 秒，转换最长 120 秒，核验最长 30 秒。临时工作集目标 ≤ 512MiB/子进程；峰值 RSS 需实测报告，进程组预算超限中止。源内容、单图和展开体积先限额，不能只依赖进程内存告警。

这些限额进入后端配置，Linux/Windows 默认一致。若实际样例不能满足预算，先定位瓶颈并报告证据；改变已确认预算再请求确认，不在验收报告中偷偷放宽。

## 3. 方案对比与选定

### 决策 1：正文以专用步骤流传递，复用既有事件补播

- 备选：把正文伪装成 thinking；另建 SSE；扩展现有步骤语义。
- 选定：task_execute_step 增加 step_type=assistant，独立消息身份、增量/快照/结束语义；保留 final_result 为任务最终结果。
- 原因：本会话有 389 条思考增量，但 mapper 丢弃普通 content；新传输通道会重复实现鉴权、游标和恢复。正文不可继续藏在思考卡片里。
- 重新考虑条件：现有十种事件的兼容边界无法承载额外步骤语义，或传输协议整体升级；不能在实现中偷偷新增另一套订阅。

### 决策 2：有界合并与投影，不逐 token 改写整棵状态树

- 备选：每 token 更新全局状态和数据库；结束才返回；有界增量合并。
- 选定：扩展现有 250ms/8192 字符/16 帧的 coalescer，同一身份内合并正文；正文生命周期、工具、澄清、恢复和终态为屏障。前端常态每 100ms 合并提交，屏障立即刷新；持久化由 step_projection_writer 的单个异步写入器每 2 秒保存最新快照，并在非步骤语义屏障和退出时强制落盘。所有步骤先进入有 fencing 的持久事件日志，再更新内存投影；慢 SQL 不阻塞正文推送。
- 原因：state_message_manager 当前思考已节流数据库，但每帧仍重写完整 Redis 历史；新增正文若照抄会放大长历史成本。新增正文以进程内有界投影和增量事件日志推进，完整历史只按窗口/屏障更新。
- 重新考虑条件：250ms 合并仍不能满足预算；只能调节有界批量与投影，不允许丢字或无限队列。

### 决策 3：两个入口共用资源与校验，按来源选择渲染器

- 备选：分别修补现有 Word/LibreOffice/Chromium 三条路径；全部 PDF 都经 Word→LibreOffice；共享资源层并按来源使用渲染器。
- 选定：第三种。智能体工具和“另存为”共用授权、文档结构、资源、布局参数及核验服务；PDF 默认 auto：探测已有 Chromium，存在则离线打印，否则使用已实测的独立 LibreOffice profile 将本次纯渲染器生成的 DOCX 转为 PDF；可显式固定引擎。DOCX 由 python-docx 纯渲染器生成，表格有重复表头、禁止拆行和自适应列宽。
- 原因：工具当前用 Word→LibreOffice，另存为却调用 md_to_pdf_bytes；只修一个入口会留下丢图问题。但 conversation_export_service.py 已明确记录旧 Word→LibreOffice 路径损坏 pandoc 表格、列表及标题排版，因此不能仅为统一引擎而重走已知故障路径。两个入口应统一资源与结果语义，不必让不同格式经过同一个二进制。
- 重新考虑条件：同一来源在 Chromium 的保真或性能不达标时，基于固定图文样例评估替代引擎；auto 回退只使用本次两平台实测过的 LO 路径，manifest 记录实际 engine 和 CJK 字体；显式指定引擎失败时不回退。不会让模型每次安装依赖或临时发明排版脚本。

### 决策 4：工作区资源先解析成二进制，保留全局清理策略

- 备选：允许任意本地 file:// 或相对路径穿过 HTML 清理器；修改进程 cwd；在授权工作区内解析资源并显式交给渲染器。
- 选定：第三种。markdown 解析器提取图片引用，领域服务从可信 WorkspaceBackend 读取二进制；渲染器使用内存资源表，不发起任意 URL 请求、不改变 cwd、不向清理器放行宿主路径。
- 原因：现有 sanitize_html_for_pdf 会去掉无协议图片地址，导出却仍返回成功；这正是两张图消失的原因。放宽全局规则会影响其他 PDF 消费者及文件隔离。
- 重新考虑条件：确需新的受控远端资源类型时，单独设计访问策略与容量限制；不直接复用客户端传入 URL 当信任来源。

### 决策 5：每个产物版本核验一次，成功结果按内容缓存

- 备选：每次下载完整转换/解析；仅检查扩展名；按授权上下文和内容版本核验并复用。
- 选定：第三种，转换成功与结构核验、视觉核验分开记录；只有版本和依赖一致时复用。
- 原因：文件存在不能证明嵌图正确；每次下载解包、渲染又会浪费性能。首次校验成功后可复用，授权检查始终在缓存读取之前。
- 重新考虑条件：发现元数据/依赖变更不能可靠使缓存失效；此时禁用命中，不能返回可能过期的文件。

### 决策 6：显式跨平台适配，重处理在受控子进程内

- 备选：仅在线程内调用系统命令；依赖 bash/apt/fc-list；独立进程加平台适配。
- 选定：用 sys.executable 启动导出 worker，结构化参数、shell=False；Linux 自有进程组，Windows 自有 Job Object；仅清理本次作业拥有的进程树。
- 原因：线程不能可靠取消底层转换；Windows 不具备 Linux 命令且不能按进程名杀全部 Office/浏览器。独立 LibreOffice profile 和 Chromium browser context 避免复用用户已打开的应用进程。
- 重新考虑条件：部署要求集中式转换服务；当前不引入新服务或新的 Celery 队列。

## 4. 系统现状与拟实施数据流

### 4.1 已检查的代码现状

1. task_exec._consume_agent_stream 订阅 updates/messages/values/custom；mapper 只把思考、工具及委派转成 ExecStep，普通正文没有事件。
2. coalesce_stream_events 只合并 thinking；add_execution_task_step 也只拼接 thinking，其他同 call_id 步骤直接替换。
3. EventReplayService 先写 Redis Stream；ReplayCompactor 只压缩相邻 thinking。前端 recoveryProjection 同样只拼接 thinking；最终回答由 final_result 驱动。
4. ExportDocxTool 只给 MarkDocx 传 markdown 文本；图片清理后可能变空，图片渲染错误又被打印后跳过，工具无法发现缺图。
5. download-md-to-pdf-or-docx 端点直接选择转换器，未附带任务资源上下文。DownloadFilesSchema 目前仅有 file_name/file_url。
6. MarkDocx 列表共用 List Number 样式，表格平均列宽且没有跨页重复表头；原会话因这些问题临时补图、修表和修编号。
7. 当前 Windows 后端已有 PyMuPDF、Pillow、openpyxl、python-pptx、python-docx；未发现 LibreOffice 可执行文件。Linux 基础镜像安装 LibreOffice、文泉驿字体与 Playwright Chromium；实际运行环境仍须核对。Windows 的 Chromium 就绪情况也须实际探测。缺依赖提示不能代替双平台成功证据。

### 4.2 正文目标数据流

模型 chunk → 独立文本映射模块 → 有界合并 → 步骤投影/事件补播 → 前端有界 reducer → 执行时间线正文区 → 最终结果收口。

**身份与帧契约**：

| 字段 | 语义 |
|---|---|
| event_type | 保持 task_execute_step |
| data.step_type / name | assistant / assistant |
| data.call_id | `assistant:<svid>:<run_token>:<message_token>`；run_token 每次执行/继续/恢复独立，不从文本哈希推导 |
| data.status | start 表示开放文本段；end 表示该段已结束，不等于整个任务完成 |
| data.output | delta 文本，snapshot 完整文本，或 end 的空文本；保持空格/换行，不套 8000 字符工具输出截断 |
| data.extra_info.text_mode | delta / snapshot / end；不可用 status=end 猜测是不是全文快照 |
| data.extra_info.message_id | 原始 AIMessage ID；缺失时按该模型调用/子图命名空间生成稳定局部身份 |
| data.extra_info.namespace、agent_id、invocation_id | 复用可信委派上下文；不能按事件到达顺序配成员 |
| execution_epoch / event_id | 继续使用既有恢复尝试和 Stream 游标，重放相同事件只应用一次 |
| final_result.data.output_result.answer_step_id | 可选，指向作为最终答案的主 Agent 正文段；旧结果无此字段正常展示 |

client_capabilities 仅声明展示能力，不授予执行权限；服务端只接受已知值。任务创建后沿用快照中的 text_protocol，继续/恢复不得随客户端任意改写协议，旧任务不原地升级。后端先支持可选能力、新前端再启用；回滚前端前禁用新任务能力发布，已运行的新协议任务只由支持该能力的页面订阅。

正文、reasoning、tool_calls 可以在一个 chunk 中共存，分别提取，不沿用遇到 reasoning 立即 return 的互斥写法。只提取 AIMessage/AIMessageChunk 的 text 块，不将 ToolMessage 输出或模型计费等旁路调用当正文。

以 provider message ID 加可信模型调用身份关联同一响应，updates/values 中完整 AIMessage 只作 snapshot 对账，不再当增量追加。已结束的相同 message 不重复开段。对不发送 messages 增量的上游，在节点 updates 中取完整 assistant 消息并发 snapshot。tool call 结束该次响应，不能把工具前发言拼到下一次模型回答。

并行成员复用 F074 namespace→invocation 映射；内置研究成员同样补足可信身份。后台任务沿用父运行的 stream/context，先用锁定版本的测试证明消息能到达；若 detached 调用不传播 messages，则在子模型的流式回调补发带可信 invocation 的 custom 文本帧，映射成同一 assistant 步骤，禁止双路重复发布。无真实增量时只呈现其实际完成快照。

正文末段在工具边界、interrupt、terminate、exception 和正常完成前都刷新。异常退出后的未完成正文按现有 execution_epoch 恢复规则丢弃/替换，已完成历史保留。过期回放按既有 replay_reset 恢复，不伪造全文。执行内身份随 mapper 生命周期释放，不维护跨会话全局文本字典。

### 4.3 前端展示与持久化

- 在 ExecutionTimeline/DeepStepGroup 中提供独立 AssistantText 组件，默认展开正文；成员正文归入该次成员调用，使用既有 Markdown 和主题组件。思考仍走原卡片和旁白，不把正文送入 narration。
- 更新 execTypes、步骤树、recoveryProjection、ReplayCompactor 和数据库历史投影的同身份合并语义；snapshot 替换，delta 追加，end 只收口，保留首帧时间。
- WebSocket hook 把事件先归并到本次连接的 bounded draft，常态 100ms 一次不可变提交；快照历史按批次构建且最终单次提交。以 call_id/epoch 索引定位，仅更新活跃片段，不反复对全部历史做全量 Markdown 解析。
- 本地待处理文本达到 64KiB 或 100ms 立即提交；工具/澄清/恢复/终态是屏障。缓冲满时施加背压，不默默丢帧。推进可恢复游标必须发生在相应事件成功进入展示状态之后。
- 普通主 Agent 发言在时间线保留；final_result 到达时只隐藏 answer_step_id 精确指向的同一段，由结果区呈现最终答案，不能根据“文本看起来相似”删除其他发言。若最终答案附加了系统收尾提示，以最终结果为准；旧任务没有链接则不做正文去重推断。
- 失败/停止保留本尝试仍有效的部分正文；清理缓冲、连接、监听器、计时器。迟到事件由终态/epoch 检查拒绝，不重新激活任务。分享页继续只读授权历史，不新开私有任务订阅。
- 后端新增正文 projection 与缓存写入以窗口/屏障批量持久化；事件本身有界写入同一 Redis Stream。DB 首段、完整 snapshot、结束及任务终态强制刷新；普通 delta 的 DB 全历史改写不超过既有 2 秒窗口。工具与澄清的即时持久化语义不变。

### 4.4 导出目标数据流

智能体 export_docx/export_pdf 或“另存为” → Linsight 导出编排服务 → 授权资源清单 → 共享报告导出服务 → 受控渲染进程 → 结构/资源核验 → 内容缓存 → 原目标文件写入或下载响应。

1. 新调用显式提供 session_version_id/source_path。工具从闭包绑定的 WorkspaceBackend 获取，不让模型指定别的任务。另存为通过包装 API 传入可信任务引用，服务通过已有 AgentConfigService.owned_task 和 PermissionService 授权后读取。
2. 原 file_name/file_url 参数保留。能从已有产物记录映射到真实任务时，通过 repository 定位并做同一授权；不把任意 object key、绝对路径或 URL 当作已授权上下文。无法确认资源归属时明确拒绝。无相对资源的合法历史报告继续支持，私有资源不因兼容旧参数绕过授权。
3. 分享场景保持既有分享服务的只读授权边界，传递服务端解析的已授权产物引用；不能以 share token 打开任意私有任务导出。现有共享页未提供的导出入口不新增。
4. markdown 用现有 parser 构建文档结构，提取行内、引用式及合法图片节点；统一处理 percent 编码、中文/空格、正反斜线。`/output/x` 和 `output/x` 按工作区根定位，`charts/x` 按源报告所在目录定位；路径先规范化再检查允许分区。越界、宿主盘符、UNC、file://、无授权的其他任务路径一律拒绝；不以替换字符串来证明安全。
5. 从 WorkspaceBackend.adownload_files 读取图片二进制，去重后校验体积、类型和尺寸。仅接受已授权工作区或可证明属于该任务的对象存储资源。外部 URL 不直接交给 converter 发请求；无法安全解析的资源返回明确错误。全局 sanitize_html_for_pdf 规则不放宽。
6. common 纯渲染器接收 `source + resource_bytes`；处理文本样式、标题、图片、列表与表格。任务导出启用严格资源模式和显式 resolver，渲染器不静默跳过图片；其他消费者保留原默认行为，避免跨模块改变兼容语义。
7. 每个独立 ol 创建独立编号实例，尊重 start，使用 OOXML numbering，不在带编号样式上再拼文本前缀。表格按内容长度与最小列宽在页面可用宽度内分配，跨页重复表头，长正文允许换行；图片等比限制到页内，不使超大图撑出页面。中文字体设置东亚与西文字体映射，配置缺失时用经过检查的可用字体，不假装系统一定有宋体。
8. Markdown/HTML 的 PDF 从同一文档结构与资源表生成受控 HTML，由隔离 Chromium 打印；禁止渲染期间加载未解析的外部资源，使用明确的字体、分页与表头 CSS，不先生成 DOCX。原生 Office 的转换/预览仍由 LibreOffice 适配器承担。生成后 PyMuPDF 逐页载入，核对页面数、文本、图像及边界指标。程序检查不能证明所有视觉无瑕；固定样例还须逐页人工/图像核对，不把结构成功标成视觉通过。
9. Markdown 保留标准图片引用，页面预览继续按已授权产物索引解析。HTML 交付资源内嵌为受控 data:image 字节，不携带生成机绝对路径或外部脚本；两者核对正文/引用清单。原始 Markdown 单文件不宣称自带二进制图片，界面资源链接与原始文件下载语义分别保持明确。

**内部服务契约（拟新增）**：`ReportExportService.export(source, resources, target_format, options)` 返回 bytes、mime、content_digest、结构核验结果及 renderer_version；不读取权限对象。Linsight 的 `ArtifactExportService` 负责可信任务、WorkspaceBackend、授权、缓存和落盘，API/工具只委托服务。纯转换器位于 common，不反向导入任何 domain。

### 4.5 缓存、完整性与并发

- 缓存键含 tenant、task、源版本摘要、所引用资源的版本/ETag、目标格式、排版设置、renderer_version、实际使用的 Chromium/LibreOffice 与字体指纹。相同字节在不同租户/任务不共享可见缓存。
- 源与依赖清单首次解析后保留；再次请求先复核对象版本信息，版本不变直接复用，不完整读取/解包/渲染全部文件。无法获得可靠版本信息时明确走未命中，不以文件名或 mtime 单独充当内容版本。
- 派生产物与核验 manifest 写在该任务 scratch/.exports/<digest>/，不作为新的 output 产物混入最终清单。成功状态最后写入、临时文件原子替换；失败不缓存成成功。既有 output 目标写入仍经 WorkspaceBackend，不跨任务复用文件。
- 缓存分两级：源/资源指纹定位已解析的共同文档结构与资源清单，目标格式、排版、字体及引擎版本定位成品。先导出 Word 再导出 PDF 复用解析/资源结果，各目标只渲染一次；PDF 不无谓生成中间 DOCX。任一级依赖变化都使其下游失效。
- 缓存有效期默认 24 小时，每节点缓存上限 1GiB，按访问清理；所有命中先授权。源/图/字体/引擎变化均失效。并发同 key 采用有 token 和 TTL 的单飞锁，等待者获取同一成功结果；锁持有者异常退出后可重试，不返回半成品。
- 使用现有 Redis 对部署节点执行原子容量租约，每节点共享 2 个重作业槽和 4 个等待位，不能让 8 个 API 进程各自开 2 个变成 16 个。配置稳定且节点唯一的 instance_id，API/Worker 同节点共用；租约带 token、到期与续租，释放不能删除其他拥有者的新租约。
- 持有槽位的真实转换进程超时/退出才释放；Redis 暂不可用时拒绝新重作业，不能退回无限本地并发。锁/容量等待均有上限，不持有全局 asyncio 锁阻塞聊天。
- 同一内容的一个等待者断开只取消该等待者；仍有其他订阅者时保留共同作业。作业所属任务终止、无订阅者且作业取消、超时或 lease 丢失时清理其进程树；收到错误不写成功 manifest。
- 新生成 XLSX/PPTX 用 ZIP 结构与相应读取器核验，限制解包体积；PDF 用 PyMuPDF，DOCX 用 python-docx，图片用 Pillow，CSV 用编码检测与标准解析器。仅在文件内容版本变化时重新检查；CSV 无预期 schema 时只声明编码/结构可读，不臆断列的业务正确性。
- manifest 区分 generated、structure_checked、visual_checked、failed，记录资源清单、摘要、失败原因和版本。只有结构检查通过的产物进入成功交付列表；坏文件在错误详情中标记，不能把一份坏图隐去后仍声称整份多格式交付完整成功。

结构核验逐项检查图片引用与嵌入资源对应关系、文档关系文件与 OOXML 结构、PDF 页面可载入及资源存在；不将“可打开”单独作为资源完整性结论。视觉核验由固定样例覆盖，常规生产导出不额外调用模型看图，不宣称已人工检查每一份用户报告。

### 4.6 Linux/Windows 平台适配

| 项目 | Linux | Windows | 共同约束 |
|---|---|---|---|
| 工作区路径 | pathlib 下真实根目录 | pathlib + 盘符/UNC 校验 | 虚拟 /output 不等于宿主根；解析后留在授权根内，拒绝链接越界 |
| LibreOffice | 配置绝对路径或 shutil.which | 配置绝对路径、PATH 或标准安装目录 | 启动前验证可执行文件，不执行 shell 字符串 |
| Chromium | 使用 Playwright 配置的浏览器二进制 | 使用同一 Playwright 安装机制 | 独立无界面实例与上下文，渲染不访问任意网络；部署时安装，不在每次导出时下载 |
| 字体 | 检测配置字体与 Noto CJK 可用字体 | 检测配置字体、系统字体目录和微软雅黑等可用字体 | 记录实际字体指纹；不依赖 fc-list；缺中文字形明确报错 |
| 转换进程 | start_new_session，清理自有进程组 | 隐藏启动，Job Object 绑定并在关闭时终止自有进程树 | 独立 LO profile；不按 soffice 名称扫杀，不连接用户 Office |
| 临时资源 | tempfile + finally | tempfile + 关闭句柄后 finally | 中文/空格路径走结构化参数，取消/异常/超时都清理 |
| worker 启动 | sys.executable -m 模块 | 相同 -m 模块入口 | 不依赖 fork、bash、cmd 内置命令或 cwd 变更 |

能力探测按实际目标格式分别执行并按配置版本缓存，进程重启或配置改变重新探测。渲染时不运行 apt、winget、pip 或下载浏览器/字体；缺依赖给出管理员可执行的系统级准备说明。开发/CI 分别准备 Linux 与 Windows 的 Chromium、LibreOffice 和可用 CJK 字体后做完整验证，未准备的平台不能标为通过。缺少 LibreOffice 不应阻断不依赖它的 Markdown→PDF 或纯 DOCX 生成。

### 4.7 主要模块边界（拟实施）

| 模块 | 负责 | 不负责 |
|---|---|---|
| linsight/domain/services/assistant_text_mapper.py | 独立消息身份、混合 chunk、快照对账与文本段生命周期 | I/O、授权、模型执行 |
| stream_event_mapper.py / stream_event_buffer.py | 委派身份接入、文本屏障与有界合并 | 新的传输通道、无限缓存 |
| state_message_manager.py 及抽取的文本投影模块 | 增量投影、窗口落盘、结束刷新与 epoch | 改变工具恢复策略 |
| event_replay.py / replay_compaction.py | 现有事件补播和有界快照压缩 | 重新执行图 |
| hooks/Websocket 中提取的文本 reducer/batcher | 游标、同身份合并、批量 UI 提交和卸载清理 | 直接 HTTP、模型调用 |
| Execution/AssistantText.tsx 与执行树适配 | 正文与成员归属、Markdown 展示和精确最终段隐藏 | 新状态库、思考旁白替代正文 |
| linsight/domain/services/report_export.py | 授权工作区引用、缓存、调用导出服务与结果落盘 | common 中的纯排版细节 |
| tool/domain/services/report_runtime.py 及 worker/平台适配模块 | 容量租约、受控子进程、转换与核验编排 | ORM、任意宿主文件或网络读取 |
| common/utils/report_document.py | 已解析资源到 DOCX、列表/表格/字体布局 | domain/API 导入、授权、对象存储 |
| tool/domain/langchain/linsight_export.py / linsight 端点 | 保留工具/HTTP 外壳并委托领域服务 | 内联转换、内联 ORM |
| 现有产物收集与核验模块 | 格式完整性 manifest、错误呈现与内容版本复用 | XLSX/PPTX 通用生成器、事实校验 |

## 5. 已知坑

| 事实 | 不处理的后果 | 处理位置 |
|---|---|---|
| 同一 chunk 可携带正文、思考与工具调用 | 互斥 return 丢其中一部分 | 文本 mapper 与 stream_event_mapper |
| LangGraph messages 与 updates/values 可包含同一条回答 | snapshot 当 delta 会翻倍 | 消息身份与 text_mode reducer |
| 同一任务澄清/继续会新建 mapper | 从 0 重用 ID 把多轮串在一起 | run_token/epoch 与现有恢复投影 |
| backend 已合并思考，但 Redis 仍每帧重写大历史 | 正文使长会话成本继续放大 | 有界投影、flush 屏障和性能测试 |
| frontend 除 thinking 外同 call_id 默认替换 | 正文只剩最后一小段 | recoveryProjection/stepUtils/ReplayCompactor |
| 相对图片会被共用 sanitizer 去掉 | Word/PDF 少图仍返回成功 | 严格资源 resolver，不改全局清理规则 |
| 工具与另存为目前 PDF 引擎不同 | 一条链路修复不代表另一条正确 | 共享导出服务与双入口 E2E |
| 聊天导出已因 Word→LibreOffice 排版问题改用 Chromium | 强行统一到 LO 会重引已知表格/列表故障 | 共享资源层，按来源选引擎，双格式视觉样例 |
| Windows 缺少 soffice，且 Office 可能已有用户实例 | 假成功、挂住或误关用户程序 | 平台探测、独立 profile/Job Object |
| 单线程取消不等于 subprocess 终止 | 超时后仍耗 CPU/内存 | 自有进程树清理与真实平台测试 |
| 扩展名正确不代表二进制格式正确 | 错误 JSON 被当 PDF/Office 下载 | 一次性实际格式核验与响应错误解析 |

## 6. 对外契约与依赖

### 6.1 提供的契约

- task_execute_step 的 assistant 语义和 final_result 可选 answer_step_id，见 §4.2。旧事件不新增必填字段；历史回答原样展示。
- `/api/v1/linsight/workbench/download-md-to-pdf-or-docx` 保持路径、现有请求字段、二进制成功响应及 Content-Disposition；增加可选任务/源路径上下文，由包装 API 携带。失败沿用 ResourceDownloadError（10003）并附可定位的资源/原因；不增加端点业务 403 分支。
- export_docx/export_pdf 工具参数和成功文本兼容；缺图、依赖缺失、超限、核验和写入失败明确软返回，工具异常不被静默清空。内部保留异常类型与 traceback，日志不输出凭据。
- 不直接由 endpoint 读取模型或拼写数据库查询；旧 URL 到产物的映射放 repository，授权与组合在 service。

### 6.2 依赖与风险

| 依赖 | 风险与约束 |
|---|---|
| F074 Redis Stream、游标和 execution_epoch | 保留已有 TTL/裁剪/安全恢复；不扩大自动重放 |
| LangGraph/deepagents 的 message ID、metadata、子图传播 | 锁定版本做混合帧与后台身份测试；不可只依赖数组位置 |
| WorkspaceBackend/对象存储 stat、二进制下载 | 需要可靠资源版本；没有版本信息不命中缓存；不能把 binary read 的文本错误当图片 |
| PermissionService、任务/分享 repository | 缓存前仍鉴权；旧 file_url 不自动授予访问其他对象的权限 |
| python-docx/Markdown parser/OOXML | 任务导出使用独立纯渲染器；不修改既有 MarkDocx 和全局 sanitizer |
| LibreOffice 与 CJK 字体 | 两平台都需运维准备，记录版本/字体；无依赖不报完整通过 |
| Playwright Chromium | Markdown/HTML PDF 依赖浏览器二进制；需分别探测两平台，限制运行时网络与独立进程生命周期 |
| PyMuPDF/Pillow/python-docx/openpyxl/python-pptx | 使用已有后端依赖；解析不可信产物需限额、隔离和超时 |
| 前端现有 Markdown/共享 UI/i18n | 不引入新 UI/状态库；新增词条三语，触及文件消减旧违规 |

## 7. 测试与可观测

测试按契约层、端到端与性能分开，tasks.md 在设计确认后逐项映射 AC-01–AC-29。

- 后端单测：AIMessageChunk 的混合内容、空白、完整 snapshot、updates 去重、并发身份、后台传播、结束与异常 flush、恢复 epoch。投影测试核对 live/replay/DB 历史一致且写入次数有界。
- 前端测试：混合事件 reducer、最终段精确去重、终态迟到帧、重连游标、历史分批、关闭清理；受影响组件与类型检查覆盖所有 task carriers。
- 导出单元/集成：中文空格/正反斜线路径、引用式图片、缺图/坏图/越界/ZIP 炸弹、编号重启、跨页表头、字体缺失、同 key 并发、缓存失效、依赖变化和故障清理。不能只 monkeypatch 转换器返回任意 bytes。
- 双平台 E2E：Windows 原生 Python 与 LibreOffice；Linux 原生或 Linux Docker 中运行同样例。分别真实验证 PDF 逐页渲染、Word 嵌图和表格编号、超时/取消的子进程消失、无遗留临时目录；不以模拟平台标记代替。
- 浏览器 E2E：3001/workspace/ 新建独立合成任务，观察主/成员正文边生成边出现；短时断网后恢复且不重复，完成后刷新、继续、多端和停止一致；分别使用工具与“另存为”导出相同报告并下载检查。测试注明禁用 GPU 和 CPU 节流环境，不声称等同实际信创浏览器验收。
- 固定性能样例按 §2.1 前后对比。原报告只作为匿名结构参考；不重跑该用户原任务或覆盖其产物。不拿远端共享队列作压力测试。

实施阶段的操作入口：后端在 src/backend 执行 `.venv/Scripts/python.exe -m pytest test/linsight/`（Windows）或 `.venv/bin/python -m pytest test/linsight/`（Linux），导出纯渲染与 worker 测试按任务清单缩小范围；前端在 src/frontend 执行 `pnpm lint`、`pnpm typecheck`、`pnpm check-i18n` 与客户端定向测试。遵循各项目 AGENTS.md，不添加 suppressions 或新 @ts-strict-ignore。

日志使用 task/tenant/run/message/invocation、格式、内容摘要、资源数量、input bytes、queue/render/validate/cache 时长、子进程状态及失败类别，不写完整模型正文、私有路径或签名链接。指标区分上游模型等待、后端合并/持久化、网络、浏览器提交、排队、渲染和校验；记录失效、命中、真实转换次数及清理后进程数量。

## 8. 后续改进与当前边界

转换服务集中化、后台持久任务队列和跨任务全局去重暂不引入，以免新增运维组件与权限边界；达到当前并发预算后才基于实测评估。任意脚本的内容正确性、任意网页资源和所有办公格式互转不属于本次实现。

本机已安装官方 LibreOffice，并使用已存在的 Edge 二进制单独验证 Chromium 打印；Linux 在既有后端镜像中实测两引擎。实际信创浏览器、禁 GPU 的滚动/heap 和全容量压力预算尚未验收，不能据此声称全部性能预算达标。

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-10-19 | 设计初稿，包含正文协议、双入口多格式、性能预算和双平台生命周期 | 用户确认规格并明确 Linux/Windows 兼容 |
| 2026-10-19 | 统一资源层，Markdown/HTML PDF 继续用 Chromium，原生 Office 用 LibreOffice | 核查当前配置时发现既有聊天导出已记录 LO 排版回归，设计尚未确认时修正 |

## 9. 实施补充与运行配置

- 内置角色与自定义成员均由 DelegationMiddleware / SubagentIdentityMiddleware 发出 invocation_id、agent_id、agent_name；finish_text 也保留身份。旧记录仅有 namespace 时保留独立成员编号，不把缺失的目标按到达顺序当作可靠身份。
- 前端按成员卡片组织输出，成员正文与工具片段保持各自先后顺序；主智能体正文有独立标识。同角色并发带编号，完整委派说明默认收起，重复的最终成员正文不再复制到结果区域。计时用当前回合是否运行，不能因已出现 todo 列表就停止；100ms tick 只重绘状态文字。
- 成员卡片的“成员输出”统一折叠正文、工具记录与独立最终结果，避免流式最终正文绕过结果折叠。运行时默认展开，完成/停止及历史记录默认收起；用户手动选择在该卡片生命周期内保留，各成员独立控制。收起时卸载正文，避免长 Markdown 继续占据渲染树。
- 报告列表忽略 li 内块间的格式化空白，首段承载编号，续段不重复编号，嵌套项按原位置渲染；避免 loose list 在序号后插入换行。PDF manifest 增加引用的栅格图片数和最多 3 个含图页码，明确为结构核验，不能用文件大小代替图片或视觉核验。
- 沙箱缺少命令时先检查并复用已有等价库；后端转换器与沙箱依赖分别看待，安装按实际操作系统和最小必需依赖处理。视觉核验首轮最多选择 3 个不同代表页（首页、含图页、表格页），80–96 DPI，发现问题才局部扩查；不默认全报告逐页转图。提示约束影响新组装的 agent，已经执行中的图保持其原提示。
- 实际长会话发现原 coalescer 后的同步历史写入积压可达约 54 秒。step_projection_writer 将每帧写入改为一个写入器保存不可变快照；新帧只修改本回合已有内存投影。非步骤屏障、状态改变和退出 flush；写入异常向主执行传播，不悄悄继续。
- shared DB 的恢复扫描原本不按 queue_namespace 隔离，其他旧 worker 会领取本地私有队列任务。recovery_queue_scope 将 scope 写入既有 JSON；私有队列保留 queue_retry_after 给新版扫描，并将旧 retry_after 设为远期以兼容尚未升级的共享 worker。默认 linsight 队列保留旧语义，不关闭租约或恢复保护。
- 格式核验解析在 report_worker 的 validate 模式执行，复用 Windows Job Object / Linux 独立进程组与 30 秒超时。父进程仅在线程中读取受限输入、提取资源并计算内容摘要；成功 manifest 用进程内 LRU 256 条复用。源/资源变化和缺失每次重新检查。产物收集最多同时处理 2 个文件，避免一个报告的多张图片耗尽节点容量。
- 共享另存为的只读授权集中在 PermissionService.can_read_task_artifact：同租户 owner/admin，或有效且绑定当前会话/版本的分享。缓存命中前仍鉴权；分享不授予执行权限。
- `BISHENG_REPORT_PDF_ENGINE=auto|chromium|libreoffice`；`BISHENG_REPORT_CHROMIUM_PATH`、`BISHENG_SOFFICE_PATH` 可指定已安装程序。Windows 自动探测 Program Files/LibreOffice，Linux 探测 PATH。`BISHENG_REPORT_NODE_ID` 默认主机名；`BISHENG_REPORT_RENDER_REVISION` 可用于运维主动使缓存失效。默认容量 2 个执行 + 4 个等待、15 秒排队、120 秒转换、30 秒核验；Redis 单次协调调用最多等待 5 秒，释放不可用时记录并靠有界租约回收；结果缓存 1 GiB / 24 小时。没有运行时自动安装。
- 独立 worker 不复用用户 Office，取消只清理本次进程树。Linux Docker 验证使用 --init；Windows 子进程/后代真实清理测试通过。文件完整性不等于业务事实核验，也不等于对任意布局的自动视觉验收。

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-10-19 | 回写实际模块、auto PDF 双引擎、成员分组、异步投影、私有队列恢复隔离、受控核验与共享授权 | 真实图文/协作会话验证暴露积压、计时和分组缺陷；用户要求多格式、双平台与性能 |
| 2026-10-19 | 任务代码沙箱延迟创建并复用；过滤缺包旧技能；PDF按实际绘制识别含图页；准备状态局部计时；统一工具依赖策略；修复共享Select透明背景和Excel格式化预览 | 8轮真实任务中发现首字准备成本、工具说明冲突、Excel缓存/格式及成员菜单显示问题；完整证据与残留见real-task-validation.md |
