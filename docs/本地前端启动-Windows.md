# 本地前端启动（Windows）

> 本文是 [`architecture/09-development-guide.md`](architecture/09-development-guide.md)「前端」章节的 **Windows 版**启动手册。
> 与 [`本地后端启动-Windows.md`](本地后端启动-Windows.md) 同一套路：**只写 Windows / 本机环境的差异**，
> 命令主体（端口、代理规则）以 09 号文档为准，改参数时两边同步。
>
> 实测环境：Windows 10 Pro 19045 + Node `v24.16.0` + pnpm `9.15.9`，两个前端服务 + 后端 + MinIO 全链路跑通。

---

## 0. 本机现状速查

| 组件 | 状态 | 说明 |
|---|---|---|
| Node.js `v24.16.0` | ✅ 已装 | |
| pnpm `9.15.9` | ✅ 已激活 | 由 corepack 提供，版本被 workspace 根 `package.json` 的 `packageManager` 字段锁定 |
| 前端依赖（约 1700 包） | ✅ 已装 | 在 `src/frontend/` 执行，耗时约 13.5 分钟 |
| 后端 `:7860` | ⚠️ 需先启动 | 前端所有 `/api` 请求经 Vite 代理打到它，**后端不起则前端代理全挂** |
| 共享中间件 | ✅ 全部可达 | 见 [`本地后端启动-Windows.md §2`](本地后端启动-Windows.md) |
| MinIO `172.19.70.108:9000` | ✅ 可达 | 前端对象代理目标，**必须与后端 `sharepoint` 一致**，见 §3.3 |

---

## 1. 前置：一次性环境搭建

### 1.1 激活 pnpm（corepack）

仓库根用 `packageManager` 字段锁定 pnpm，不需要也不能用 `npm install -g pnpm`：

```bash
cd src/frontend
corepack enable
corepack prepare pnpm@9.15.9 --activate
pnpm -v          # 期望 9.15.9
```

**本机坑**：全局 `~/.npmrc` 里配置的公司 npm 代理 `http://27.154.242.142:18690/repository/npm-group/`
**已经不可达**，而 corepack 下载 pnpm 本体走的是它 → 首次激活会挂住。加环境变量绕开：

```bash
COREPACK_NPM_REGISTRY=https://registry.npmmirror.com corepack prepare pnpm@9.15.9 --activate
```

> pnpm 本体一旦进了 corepack 缓存就不再联网，之后正常 `corepack enable` 即可。
> `pnpm install` 本身**不受**这个死代理影响——`src/frontend/.npmrc` 里已把 registry 指到 npmmirror，
> 项目级配置覆盖全局配置。

### 1.2 安装依赖

```bash
cd src/frontend        # 必须在这里，pnpm workspace 根
pnpm install
```

- 约 1700 个包，首次约 **13～14 分钟**（公司网络下）。
- 结束时会有 **6 条 `unmet peer vite@6.4.1: found 5.4.21`** 警告——这是**既有噪音，不是本次引入的**：
  workspace 内 platform（Vite 5.4.21）与 client（Vite 6.4.1）大版本不同，而根 `pnpm.overrides`
  把 `vite@>=6.4.2` 钉在 6.4.1。不影响运行，**不要**为了消警告去改 overrides。

### 1.3 后端（必需）

前端本身不依赖 Python，但**必须先起后端**（详见 [`本地后端启动-Windows.md §3.1`](本地后端启动-Windows.md)）：

```bash
cd src/backend
export config=config.yaml
uv run --frozen --no-sync uvicorn bisheng.main:app --host 0.0.0.0 --port 7860 --workers 1 --no-access-log
```

冷启动约 55 秒；探活 `curl http://127.0.0.1:7860/health` 得到 `{"status":"OK"}` 再进行下一步。

---

## 2. 启动命令

### 2.1 一键启动（推荐）

仓库提供了 `scripts/frontend.cmd`，与后端 `scripts/backend.cmd` 同一套约定：
后台启动、stdout + stderr 合并写入 `.run\logs\<name>.log`、PID 记录在 `.run\pids\<name>.pid`，
**重复执行 = 全部重启**（会先清理上一轮，包括手工 `pnpm dev` 起来的实例）。

```powershell
.\scripts\frontend.cmd
```

| 命令 | 作用 |
|---|---|
| `.\scripts\frontend.cmd` | 重启 client + platform |
| `.\scripts\frontend.cmd -Status` | 查看状态（PID + 端口） |
| `.\scripts\frontend.cmd -Stop` | 停止全部 dev server |
| `.\scripts\frontend.cmd -Logs client -Follow` | 跟踪 client 日志 |
| `.\scripts\frontend.cmd -Only client` | 只启动一个（`client` / `platform` / `ui`） |
| `.\scripts\frontend.cmd -Only ui` | 只启动文档站（`ui` 不在默认集合里） |
| `.\scripts\frontend.cmd -DryRun` | 只打印将要执行的命令 |

**说明**：

- 脚本通过 `corepack pnpm` 调用，遵循根 `package.json` 的 `packageManager`（9.15.9）。
  本机 PATH 上独立安装的 `pnpm` shim 会尝试联网下载 pnpm 12.11.2（公司 npm 代理已失效），
  不要直接依赖它；脚本已规避。
- 就绪判据：client `http://127.0.0.1:4001/workspace/`、platform `http://127.0.0.1:3001/` 返回 200。
  超时默认 300 秒（首次启动要先跑 locales 构建，明显更慢）。
- 后端 `:7860` 不可达时**只告警、不中止**：dev server 能正常启动，但页面里的 `/api` 请求会 502。
  加 `-SkipBackendCheck` 可跳过该检查。
- 两个 dev server 的启动日志在 `.run\logs\client.log` / `.run\logs\platform.log`，
  启动失败时先看这两个文件。

### 2.2 手工启动（脚本内部执行的就是这两条）

两个 app 都在 **workspace 根 `src/frontend/`** 下用根 `package.json` 的脚本启动：

```bash
cd src/frontend

pnpm dev:client      # 终端用户聊天端  :4001   base path = /workspace
pnpm dev:platform    # 管理端          :3001   base path = /
```

对应关系（根脚本 → 实际命令）：

| 根脚本 | 实际执行 | 端口 | Vite |
|---|---|---|---|
| `pnpm dev:client` | `pnpm --filter bishengchat dev` → `cross-env NODE_ENV=development vite` | **4001** | 6.4.1 |
| `pnpm dev:platform` | `pnpm --filter bisheng start` → `vite` | **3001** | 5.4.21 |
| `pnpm dev:ui` | `rspress dev`（设计系统文档站） | 按提示 | — |

**注意事项**：

- 两个 app 的 `predev` / `prestart` 都会先跑 `pnpm --filter @bisheng/locales build` 生成 locale 产物，
  **首次启动因此更慢**；产物已存在时会打印 `[locales] artifacts already up to date` 并秒过。
- client 的 dev server 配了 `strictPort: true`：**4001 被占用会直接报错退出**，不会自动漂到 4002。
  换端口要同时改 `client/vite.config.ts` 与相关 env，不建议。
- 启动成功的标志之一是这行（两个 app 都会打印）：

  ```
  [bisheng:minio-proxy] dev object proxy -> http://172.19.70.108:9000/ (host must equal backend sharepoint; 127.0.0.1 ≠ localhost)
  ```

  **这行的地址必须等于后端 `config.yaml` 的 `object_storage.minio.sharepoint`**，见 §3.3。

---

## 3. 环境变量（本机最关键的一节）

### 3.1 文件分工

| 文件 | 服务的 app | 是否入库 | 作用 |
|---|---|---|---|
| `src/frontend/.env.development` | client | ✅ 入库 | client 的 env 目录是 **`src/frontend/` 根**（不是 `client/`），故文件放在这里 |
| `src/frontend/platform/.env.development.local` | platform | ⚠️ 意外入库（`.gitignore` 本意是忽略 `*.local`） | platform 的 env 目录是 `platform/` |
| `src/frontend/.env.development.local` | client | ❌ 忽略 | 需要本机私有覆盖时建这个，优先级高于 `.env.development` |
| `src/frontend/env.development.gateway.example` | — | ✅ | 走「本机 Gateway」方案时的模板，**默认不用** |

> Vite 加载顺序 `.env` → `.env.local` → `.env.[mode]` → `.env.[mode].local`，后者覆盖前者；
> 两个 vite config 都用 `loadEnv()` 显式加载，**只读 `process.env` 读不到**。

### 3.2 本机当前取值

`src/frontend/.env.development`（client）：

```ini
VITE_DEV_API_TARGET=http://127.0.0.1:7860          # 业务 API → 本机后端
VITE_DEV_MINIO_TARGET=http://172.19.70.108:9000/   # 对象存储 → 共享 MinIO（见 §3.3）
VITE_PLATFORM_ORIGIN=http://127.0.0.1:3001         # 管理端源，登录跳转用
VITE_PLATFORM_PREVIEW_TARGET=http://127.0.0.1:3001 # /admin、/login 反代回管理端
```

`src/frontend/platform/.env.development.local`（platform）：

```ini
VITE_PROXY_TARGET=http://127.0.0.1:7860/           # 业务 API → 本机后端
VITE_MINIO_PROXY_TARGET=http://172.19.70.108:9000/ # 对象存储 → 共享 MinIO（见 §3.3）
VITE_WORKSPACE_PROXY_TARGET=http://127.0.0.1:4001  # /workspace 反代到 client，必须 http
```

### 3.3 ⚠️ MinIO 代理目标：本机最容易踩的坑

MinIO 预签名 URL 用 **SigV4** 签名，**Host 头是签名内容的一部分**。
Vite 代理转发时会把 `Host` 改写成代理目标（`changeOrigin: true` / 手工改写 `host`），
因此 **`VITE_DEV_MINIO_TARGET` / `VITE_MINIO_PROXY_TARGET` 的 `host:port`
必须与后端 `config.yaml` 的 `object_storage.minio.sharepoint` 逐字节一致**，否则：

- 每个对象请求返回 **403 `SignatureDoesNotMatch`** → 现象是「页面能开，但所有文档/图片预览全挂」；
- 两个 vite config 都内置了这条 403 的一次性告警（`[bisheng:minio-proxy] ⚠️ ...`），见到它直接查这里。

本机对应的后端配置是：

```yaml
object_storage:
  type: minio
  minio:
    endpoint:   '172.19.70.108:9000'
    sharepoint: '172.19.70.108:9000'   # 必须与 endpoint 完全一致
```

**为什么原来写的是 `127.0.0.1:9100`，会挂？**

`docker/docker-compose.yml` 把 MinIO 容器端口映射成宿主 `9100`（`"9100:9000"`），
但那是**本地 docker 栈里给 Milvus 用的 MinIO**，不是业务对象存储；
本机后端连的是共享 MinIO `172.19.70.108:9000`，且**没有**任何进程在本地 `9100` 上监听
（`netstat` 无该端口，连接被拒）→ proxy 直接 502，图片/文档全裂。

> 判断方法：代理目标的 host:port 与 `sharepoint` 不同，就一定是错的；
> `127.0.0.1` 与 `localhost` 在签名里**也不等价**，不能混用。

**从 `local` 存储切回**：若把后端 `object_storage.type` 改成 `local`（对象由后端自身在 `:7860` 提供），
则两个前端目标都要改成 `http://127.0.0.1:7860/`（文件里已保留注释行）。

### 3.4 platform 的 `/workspace` 反代必须是 http

platform 把 `/workspace`（及其下 `/workspace/api`、`/workspace/bisheng` 等）整段反代给 client，
默认目标是 `https://127.0.0.1:4001`（为「同源化/单端口入口」的生产形态服务）。
**本机 client 是纯 HTTP**，用默认的 https 目标会握手失败 → **500**。
所以 `platform/.env.development.local` 里必须写：

```ini
VITE_WORKSPACE_PROXY_TARGET=http://127.0.0.1:4001
```

---

## 4. 验证

后端已起、两个 dev server 已起之后：

```bash
# 后端
curl http://127.0.0.1:7860/health                 # {"status":"OK"}

# 两个前端页面
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:4001/workspace/   # 200
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3001/             # 200

# 代理链路
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3001/api/v1/env              # 200 platform → 后端
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:4001/workspace/api/v1/env    # 200 client   → 后端
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3001/workspace/             # 200 platform → client
```

**对象链路（MinIO）一定要单独验**，它是最容易静默坏掉的一环：

```bash
cd src/backend
./.venv/Scripts/python.exe - <<'PY'
from minio import Minio
import urllib.request, urllib.error
c = Minio("172.19.70.108:9000", access_key="<access_key>", secret_key="<secret_key>", secure=False)
url = c.presigned_get_object("bisheng", "definitely/not/a/real/key.txt")  # 故意用不存在的 key
path_q = url.split("172.19.70.108:9000", 1)[1]                            # /bisheng/<key>?<sig>
try:
    urllib.request.urlopen("http://127.0.0.1:4001/workspace" + path_q)
except urllib.error.HTTPError as e:
    print(e.code, e.read(200).decode())   # 期望 404 NoSuchKey
PY
```

判定标准：**`NoSuchKey`（404）= 签名通过、链路正常**；
`SignatureDoesNotMatch`（403）= 目标与 `sharepoint` 不一致；`502` = 代理目标根本没人监听。
（key 换成真实对象应返回 200 + 对象内容。）

---

## 5. 常见问题排查

| 症状 | 原因 | 处理 |
|---|---|---|
| 浏览器页面上所有文档/图片预览裂，控制台 403 | `VITE_DEV_MINIO_TARGET` / `VITE_MINIO_PROXY_TARGET` 的 host:port ≠ 后端 `sharepoint` | 改成 `http://172.19.70.108:9000/`（§3.3） |
| 对象请求 502 / 连接被拒 | 代理目标（如 `127.0.0.1:9100`）本机无人监听 | 同上；9100 是 docker 栈里 Milvus 的 MinIO，别用 |
| 文档预览 404，body 是 FastAPI 的 `{"detail":"Not Found"}` | **platform 没配** `VITE_MINIO_PROXY_TARGET`，对象路径被代理到了后端（后端没有该路由） | 配 `platform/.env.development.local`（§3.2） |
| 从 `:3001` 打开的工作台相关请求 500 | platform 的 `/workspace` 目标是 https，本机 client 是 http | 设 `VITE_WORKSPACE_PROXY_TARGET=http://127.0.0.1:4001`（§3.4） |
| 前端接口全部 502 / ECONNREFUSED | 后端没起 | 先起后端 `:7860`（§1.3） |
| 登录后跳回工作台是**白屏** | `VITE_PLATFORM_ORIGIN` 为空，`/admin` 落到 client 的 origin，client 路由匹配不到 | 设 `VITE_PLATFORM_ORIGIN=http://127.0.0.1:3001`（§3.2） |
| 启动报端口被占用（client 不会自动换端口） | `strictPort: true` | 释放 4001 / 3001 |
| 升级 `bisheng-icons` 后页面报 `Element type is invalid` | Vite 预打包缓存过期（该包 git 源 `exports` 字段让 Vite 检测不到变更） | `pnpm dev:client -- --force`，或删 `node_modules/.vite` 后重启 |
| `corepack prepare pnpm` 卡住 / 超时 | 全局 `~/.npmrc` 里的公司 npm 代理已失效 | 加 `COREPACK_NPM_REGISTRY=https://registry.npmmirror.com`（§1.1） |
| 改 env 不生效 | dev server 只在**启动时**读 env | 改完必须重启对应 dev server |

---

## 6. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-19 | 新增一键启动脚本 `scripts/frontend.cmd`（§2.1）：后台启动 client + platform、PID/日志落在 `.run\`、支持 `-Stop/-Status/-Logs/-Only/-DryRun`；同时修复 `-Status` 读取 PID 文件时的空路径 bug |
| 2026-10-14 | 首次编写。修正 §3.3 的两个 MinIO 代理目标（`127.0.0.1:9100` → `http://172.19.70.108:9000/`）。实测通过：后端 `/health` 200、两个前端页面 200、两条代理链路 200、经 `:4001` 与 `:3001` 代理的 MinIO 预签名 URL 校验通过（真实对象 200 且字节一致，不存在对象返回 `NoSuchKey` 而非 `SignatureDoesNotMatch`） |
