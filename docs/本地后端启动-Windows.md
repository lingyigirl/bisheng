# 本地后端启动（Windows）

> 本文是 [`architecture/09-development-guide.md`](architecture/09-development-guide.md) 的 **Windows 版**启动手册。
> 那份文档针对 Linux / macOS（`.venv/bin/...`、`apt install`、`$(which python)`），命令在 Windows 上不可用；
> **两份文档的分工是「平台差异」，不是「内容重复」**——命令主体（端口、worker 队列、参数）以 09 号文档为准，
> 本文只写 Windows 上不同的部分。改启动参数时两个平台都要同步。

适用环境：Windows 10/11 + Git Bash 或 PowerShell。已验证环境为 Windows 10 Pro 19045。

---

## 0. 本机现状速查

| 组件 | 状态 | 说明 |
|---|---|---|
| Python 3.12.9 | ✅ 已装 | venv 在 `src\backend\.venv` |
| Python 依赖（291 包） | ✅ 已装 | 安装时必须带 `--no-install-package sync`，见 §1.2 |
| `pandoc` 3.12.1 | ✅ 已装 | 装在 `%LOCALAPPDATA%\Programs\pandoc\`，靠环境变量 `PYPANDOC_PANDOC` 发现；**不在 PATH 上**（所以 `which pandoc` 查不到是正常的），见 §1.3 |
| 共享中间件 | ✅ 全部可达 | MySQL / Redis / OpenFGA / ES / Milvus @ `172.19.70.107`，MinIO @ `172.19.70.108:9000` |
| `ffmpeg` | ❌ 未装 | 影响知识库音视频解析（§1.4） |
| LibreOffice | ❌ 未装 | 影响 `.doc` / `.ppt` / `.xls` 解析（§1.4） |
| Playwright Chromium | ❌ 未装 | 影响会话/消息导出 PDF（§1.4） |

> 后三项**不影响后端启动**，只在用到对应功能时报错。按需安装即可。

---

## 1. 前置：一次性环境搭建

### 1.1 虚拟环境

已创建，位于 `src\backend\.venv`，解释器为：

```
D:\kinsGitLab\jinbao\jinbao\src\backend\.venv\Scripts\python.exe
```

### 1.2 安装 Python 依赖

```bash
cd src/backend
uv sync --frozen --python 3.12.9 --no-install-package sync
```

**为什么必须带 `--no-install-package sync`？**

`sync==1.0.0` 是本仓库的一个**僵尸依赖**（全仓零引用），其 `setup.py` 内含 `import imp`；
而 `imp` 模块在 **Python 3.12 已被移除**，导致它的源码构建必然失败：

```
ModuleNotFoundError: No module named 'imp'
```

`--no-install-package sync` 只是跳过这一个包，其余 290 个正常安装，对运行无影响。

**想根治**（二选一，均需改仓库，改前请评估对 CI/其他开发者的影响）：

- **方案 A**：从 `src/backend/pyproject.toml` 删除 `"sync>=1.0.0"`，执行 `uv lock` 后提交。
- **方案 B**：改用 Python 3.11（AGENTS.md 的要求）。`imp` 在 3.11 仍存在，`sync` 可正常构建，
  不再需要任何额外参数。**若团队统一到 3.11，本节可整段删除**——这也是本文档与 AGENTS.md
  「Python must be 3.11.x」之间唯一的偏差来源。

### 1.3 安装 pandoc（**必装**，不是可选项）

**这不是「导出 Word 才需要」的可选依赖，而是导入期硬依赖。**

`bisheng/knowledge/rag/pipeline/loader/utils/md_from_docx.py:27` 在**模块导入时**调用 `exit()`：
找不到 pandoc → `pypandoc` 尝试联网下载 → 内网连不上 GitHub 超时 → **直接结束进程**。

失败症状极具误导性：`import bisheng.main` **退出码为 0、无任何 traceback**，
看起来像是成功，实际上一行业务代码都没跑。

**安装步骤**（GitHub 在内网不可达，需走代理；以下均在 **PowerShell** 中执行）：

```powershell
# 1. 下载（ghfast.top / ghproxy.net 代理可用；官方直连超时）
curl.exe -sSL -o "$env:TEMP\pandoc.zip" `
  "https://ghfast.top/https://github.com/jgm/pandoc/releases/download/3.12.1/pandoc-3.12.1-windows-x86_64.zip"

# 2. 解压到临时目录，再把文件移到用户级 Programs 目录（不需要管理员权限）
#    注意 Expand-Archive 会保留 zip 里的 pandoc-3.12.1/ 一层目录，这里去掉它，
#    使最终路径固定为 %LOCALAPPDATA%\Programs\pandoc\pandoc.exe
Expand-Archive -LiteralPath "$env:TEMP\pandoc.zip" -DestinationPath "$env:TEMP\pandoc_x" -Force
New-Item -ItemType Directory -Force -Path "$env:LOCALAPPDATA\Programs\pandoc" | Out-Null
Move-Item "$env:TEMP\pandoc_x\pandoc-3.12.1\*" "$env:LOCALAPPDATA\Programs\pandoc\" -Force

# 3. 设置持久化用户环境变量（pypandoc 用它作为唯一查找入口）
setx PYPANDOC_PANDOC "$env:LOCALAPPDATA\Programs\pandoc\pandoc.exe"
```

> 第 1 步必须写 `curl.exe` 而不是 `curl`：PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名，
> 参数格式不兼容，直接用 `curl` 会失败。

**验证**：

```powershell
& "$env:LOCALAPPDATA\Programs\pandoc\pandoc.exe" --version
# 期望输出：pandoc 3.12.1 / Features: +server +lua
```

> 校验说明：官方 `SHA256SUMS` 在内网同样不可达，**无法比对官方哈希**。
> 可自行核对的替代信号：压缩包 CRC 全通过、二进制自报版本 `3.12.1`、
> `pandoc.exe` 大小 `235,555,080` 字节（与官方一致）。介意的话请从能科学上网的机器取一份官方包替换。

**注意**：

- `setx` 写入的是注册表用户环境变量，**对已经打开的进程（含 Trae）不生效**，必须重开终端 / 重启 Trae。
- 仓库根 `src/backend/` 下的 `pandoc-3.12.1-windows-x86_64.msi` 是**损坏的截断文件**
  （大小正好 8 MiB，MSI 的 SAT 表无法读取），**不要使用**，请按上面步骤重新下载。
- 回退：`setx PYPANDOC_PANDOC ""` 删除该变量，再删掉 `%LOCALAPPDATA%\Programs\pandoc` 目录。

### 1.4 可选系统依赖

| 依赖 | 用途 | 缺失时的表现 | Windows 安装 |
|---|---|---|---|
| `ffmpeg` | 知识库音视频解析（转 16kHz 单声道 wav 再送 ASR） | 上传音视频报 `ffmpeg is not installed` | `winget install Gyan.FFmpeg`，或解压官方 zip 后把 `bin` 加进 PATH |
| LibreOffice | 知识库解析 `.doc` / `.ppt` / `.xls` | 对应格式解析失败 | `winget install TheDocumentFoundation.LibreOffice` |
| Playwright Chromium | 会话/消息导出 PDF | `BrowserType.launch: Executable doesn't exist` | `cd src/backend && uv run --frozen --no-sync playwright install chromium` |

> `uv run playwright install chromium` **不要用管理员身份运行**，否则浏览器会装进
> Administrator 的缓存目录，普通用户进程读不到。

---

## 2. 中间件

本项目本地开发**不需要起 Docker**，`config.yaml` 已指向团队的共享中间件栈：

| 服务 | 地址 | 配置键 |
|---|---|---|
| MySQL | `172.19.70.107:3306` | `database_url` |
| Redis | `172.19.70.107:6379`（库 1 缓存 / 库 2 Celery broker） | `redis_url` / `celery_redis_url` |
| OpenFGA | `172.19.70.107:8080` | `openfga.api_url` |
| Elasticsearch | `172.19.70.107:9200` | `vector_stores.elasticsearch.url` |
| Milvus | `172.19.70.107:19530` | `vector_stores.milvus.connection_args` |
| MinIO | `172.19.70.108:9000` | `object_storage.minio.endpoint` |

配置文件位置：`src/backend/bisheng/config.yaml`。

> ⚠️ `config.yaml` 里的密码是 **Fernet 密文**，`Settings` 加载时会无条件调用 `decrypt_token()`，
> **改成明文会直接抛 `InvalidToken` 导致启动失败**。加密工具：`bisheng/core/config/settings.py::encrypt_token`。

**连通性自检**（Git Bash，任选其一排查）：

```bash
for hp in 172.19.70.107:3306 172.19.70.107:6379 172.19.70.107:8080 \
          172.19.70.107:9200 172.19.70.107:19530 172.19.70.108:9000; do
  h=${hp%:*}; p=${hp#*:}
  timeout 5 bash -c "</dev/tcp/$h/$p" 2>/dev/null && echo "OK   $hp" || echo "FAIL $hp"
done
```

---

## 3. 启动命令

> **共同前提**：以下命令的**工作目录必须是 `src/backend`**。
> `bisheng` 包并没有安装到 venv 里，是依靠工作目录进入 `sys.path` 解析的；
> 在别的目录执行会报 `ModuleNotFoundError: No module named 'bisheng'`。
>
> 另外 `config=config.yaml` 是**必需的环境变量**（指相对 `bisheng` 包目录的文件名）。

### 3.1 后端 API 服务（必需）

**Git Bash：**

```bash
cd src/backend
export config=config.yaml
uv run --frozen --no-sync uvicorn bisheng.main:app \
  --host 0.0.0.0 --port 7860 --workers 1 --no-access-log
```

**PowerShell：**

```powershell
cd src\backend
$env:config = "config.yaml"
uv run --frozen --no-sync uvicorn bisheng.main:app --host 0.0.0.0 --port 7860 --workers 1 --no-access-log
```

- 冷启动约 **55 秒**（要加载 LangChain / Milvus / ES 等重型依赖），请耐心等待。
- 本地调试建议 `--workers 1`；如需热重载追加 `--reload`。
- 生产容器为 `--workers 8`。

**探活验证**：

```bash
curl http://127.0.0.1:7860/health          # 期望 {"status":"OK"}
curl http://127.0.0.1:7860/api/v1/env      # 期望 status_code=200，返回 env / uns_support 等
```

> PowerShell 里请把 `curl` 写成 `curl.exe`（原因同 §1.3），或用 `Invoke-RestMethod http://127.0.0.1:7860/health`。

### 3.2 Celery Workers

每个 Worker 需要**独立的终端窗口**（参数与 09 号文档一致，仅可执行文件路径不同）：

```bash
cd src/backend
export config=config.yaml

# 知识库任务（文档解析、Embedding、向量写入）
uv run --frozen --no-sync celery -A bisheng.worker.main worker -l info -c 20 -P threads -Q knowledge_celery -n knowledge@%h

# 工作流任务（DAG 执行）
uv run --frozen --no-sync celery -A bisheng.worker.main worker -l info -c 100 -P threads -Q workflow_celery -n workflow@%h

# 定时任务调度器（遥测统计、情报同步）
uv run --frozen --no-sync celery -A bisheng.worker.main beat -l info
```

> ⚠️ `-P threads` 在 Windows 上是**必需**的。Windows 不支持 Celery 默认的 prefork 模型。

### 3.3 Linsight Worker（可选）

灵思 Agent 框架走独立 Redis 队列，不是 Celery 任务。

⚠️ **这一节和 §3.1 / §3.2 不同：不能照抄 `09` 号文档的原命令形式，必须用 `-m` 或设 `PYTHONPATH`**，原因见下。

```bash
cd src/backend
export config=config.yaml

# 推荐：以「模块」方式启动（会把 cwd 放进 sys.path，不依赖环境变量）
uv run --frozen --no-sync python -m bisheng.linsight.worker --worker_num 4 --max_concurrency 5

# 或：保留原命令形式，但必须补 PYTHONPATH
export PYTHONPATH=.
uv run --frozen --no-sync python bisheng/linsight/worker.py --worker_num 4 --max_concurrency 5
```

**为什么原命令在本机必挂**：

`python 路径/脚本.py` 这种写法，Python 放进 `sys.path[0]` 的是**脚本所在目录**
（`src/backend/bisheng/linsight/`），**不是当前工作目录**；而 `bisheng` 包并未安装进 venv
（`pip list` 里没有），本地完全靠 `sys.path` 上有 `src/backend` 才能 `import bisheng`。于是报
`ModuleNotFoundError: No module named 'bisheng'`。

对照：`uvicorn`、`celery` 自己会把 cwd 插进 `sys.path`，所以 §3.1 / §3.2 的命令不受影响；
`data/manage.sh`、`docker/bisheng/entrypoint.sh` 里的同一命令之所以能用，是因为它们都显式
`export PYTHONPATH=.`（容器 entrypoint 第 3 行、`data/backend.env` 里各一份）。

以上 `-m` 与 `PYTHONPATH=.` 两种写法均已在本机实测通过（导入成功、`config.yaml` 正常加载、退出码 0）。

### 3.4 前端开发服务器（可选，只看后端可跳过）

```bash
cd src/frontend
pnpm install          # 首次
pnpm dev:platform     # 管理端 :3001
pnpm dev:client       # 终端用户聊天端 :4001（base path /workspace）
```

Vite 会把 `/api/`、`/health` 代理到后端 `localhost:7860`。

> 前端的环境变量分工（尤其是 **MinIO 对象代理目标必须与本文 §2 的 `sharepoint` 完全一致**，
> 否则所有文档/图片预览 403）、验证清单与常见坑，见 [`本地前端启动-Windows.md`](本地前端启动-Windows.md)。

---

## 4. 在 Trae 里启动

### 4.1 选择解释器

`Ctrl+Shift+P` → `Python: Select Interpreter` → 选择：

```
src\backend\.venv\Scripts\python.exe
```

### 4.2 调试配置（推荐）

在**项目根目录**新建 `.vscode/launch.json`（该目录已被 `.gitignore` 忽略，不会进版本库）：

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "BiSheng Backend (FastAPI)",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": [
        "bisheng.main:app",
        "--host", "0.0.0.0",
        "--port", "7860",
        "--workers", "1",
        "--no-access-log",
        "--reload"
      ],
      "cwd": "${workspaceFolder}/src/backend",
      "python": "${workspaceFolder}/src/backend/.venv/Scripts/python.exe",
      "env": {
        "config": "config.yaml"
      },
      "justMyCode": false
    }
  ]
}
```

三处配置的作用，改动前请留意：

- `cwd` **必须**指向 `src/backend`——原因见 §3 开头的共同前提。
- `env.config` 对应 §3 的 `export config=config.yaml`，缺了它服务起不来。
- `--reload` 便于调试；跑性能相关场景时去掉。

### 4.3 首次使用必须重启 Trae

`PYPANDOC_PANDOC` 是用 `setx` 写入的用户环境变量，**已经在运行的 Trae 进程读不到它**。
不重启就会重现 §1.3 描述的「退出码 0 静默死亡」。重启后新开的终端会自动继承。

---

## 5. 常见问题排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'imp'`（构建 `sync` 时） | Python 3.12 移除了 `imp`，`sync` 是僵尸依赖 | 安装命令加 `--no-install-package sync`（§1.2） |
| `import bisheng.main` **退出码 0、无 traceback、无输出** | pandoc 缺失触发导入期 `exit()` | 装 pandoc 并设 `PYPANDOC_PANDOC`，然后**重启终端/Trae**（§1.3） |
| `ModuleNotFoundError: No module named 'bisheng'` | ① 工作目录不是 `src/backend`；② **目录对了**但用的是 `python 路径/脚本.py` 形式（Linsight Worker 就是这个情况） | ① `cd src/backend`；② 改用 `python -m bisheng.xxx` 或先 `export PYTHONPATH=.`（§3.3） |
| 启动报 `InvalidToken` | `config.yaml` 里数据库/Redis 密码被改成了明文 | 改回 Fernet 密文（§2） |
| 启动卡住 / 连不上数据库、Redis | 内网不通或共享中间件未启动 | 跑 §2 的连通性自检脚本 |
| `BrowserType.launch: Executable doesn't exist` | 未装 Playwright Chromium | `uv run --frozen --no-sync playwright install chromium`（§1.4） |
| 导出 Word 失败 | pandoc 未装或 `PYPANDOC_PANDOC` 失效 | 重新执行 §1.3 验证步骤 |
| 音视频上传报 `ffmpeg is not installed` | 未装 ffmpeg | §1.4 |
| 改了 `config.yaml` 但不生效 | DB 层配置在 Redis 里有 **100 秒 TTL 缓存** | 等 100 秒再试，别急着排查 |

---

## 6. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-13 | 首次编写。基于 Windows 10 Pro 19045 + Python 3.12.9 实测通过（`/health` 与 `/api/v1/env` 均返回 200） |
| 2026-10-14 | 修正 §3.3 Linsight Worker 的启动命令：原文照抄了 09 号文档的 `python bisheng/linsight/worker.py`，**该写法在本机必然报 `ModuleNotFoundError: No module named 'bisheng'`**（脚本方式的 `sys.path[0]` 是脚本目录而非 cwd，且 `bisheng` 未装进 venv）。改为 `python -m bisheng.linsight.worker`，并补上 `PYTHONPATH=.` 备选与原理说明；§5 排查表同步更新 |
