"""F077 定时/事件触发与定时任务生成（scheduled_task）。

分层遵循仓库约定（`src/backend/AGENTS.md` §Quick Map）：

- ``api/``    —— HTTP 面：``management_router``（挂 ``/api/v1``，应用级 ``edit_app`` 校验）
                 与 ``rpc_router``（挂 ``/api/v2``，继承 F076 密钥鉴权依赖）。
- ``domain/`` —— ``models``（SQLModel + DAO）、``services``（纯函数 + 编排）。

设计与已知坑见 ``features/v2.7.0/077-scheduled-triggers/design.md``。
"""
