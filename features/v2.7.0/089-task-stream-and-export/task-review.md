# 任务级审查

按仓库 task-review 清单检查 T001–T010。结果：PASS_WITH_NOTES；T011/T012 发布验收门禁保持未关闭。

- 后端 API 外壳委托领域服务；common 渲染器不导入领域、不读取 URL/宿主文件。没有新增模型、迁移、服务层 SQL 或新依赖库。
- 授权集中 PermissionService，分享只允许读取绑定会话/版本；内容缓存不绕过授权。工作区资源限制在既定根内。
- 事件仍使用原 task_execute_step；仅有客户端能力标记的新任务发布 assistant。身份、epoch、终态及重放保持已有契约。
- Client 使用既有 React/本地 hooks/API 包装；不新增 Recoil、状态库或 UI 库，新增词条同改三语，无新增 blur。按根 AGENTS 处理清单中已过时的“Client 用 Zustand”描述。
- 新逻辑拆入小模块；已有大型后端文件只做接线，不将新渲染/状态机继续内联。WebSocket 文件维持 600 行以内。
- 实际模块与性能偏差已回写 design.md §9；测试、任务、运行配置和未完成预算有对应文档。
- 仓库已有 lint/测试问题及未跑的性能/HTTP 矩阵不隐藏、不添加 suppression；详情见 e2e-report.md。
