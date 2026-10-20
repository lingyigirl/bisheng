#Requires -Version 5.1
<#
.SYNOPSIS
    BiSheng 本地后端一键启动 / 重启(Windows)。

.DESCRIPTION
    把 docs/本地后端启动-Windows.md 里需要手工执行的 5 条启动命令收敛成一条:

        API 服务 + Celery(knowledge / workflow / beat) + Linsight Worker

    所有进程以后台方式运行,stdout + stderr 合并写入 .run/logs/<name>.log,
    进程号记录在 .run/pids/<name>.pid。

    「重复执行 = 全部重启」是本脚本的核心约定:
    每次运行都会先停掉上一轮(含不是本脚本启动、但命令行指向本仓库的实例),
    再重新拉起,因此不需要手工找进程、杀端口。

    文件编码必须是 UTF-8 **带 BOM**。Windows PowerShell 5.1 在中文 Windows 上
    会把无 BOM 的 UTF-8 当 GBK 解码,中文注释里可能还原出 0x60(反引号),
    被当成续行符吃掉下一行代码。改动本文件后请确认 BOM 仍在。

.PARAMETER Stop
    只停止,不启动。

.PARAMETER Status
    显示各服务的运行状态(读 pid 文件 + 探 7860 端口),不做任何变更。

.PARAMETER Logs
    打印指定服务的日志末尾若干行。取值:api / knowledge / workflow / beat / linsight。

.PARAMETER Follow
    配合 -Logs 使用,持续跟踪日志(相当于 tail -f)。

.PARAMETER Only
    只启动指定服务,例如: -Only api,workflow

.PARAMETER HealthTimeoutSec
    等待 /health 返回 200 的最长秒数。冷启动约 55 秒,默认 180 足够。

.PARAMETER SkipNetCheck
    跳过共享中间件连通性探测。

.PARAMETER DryRun
    只打印将要执行的命令,不真的启动 / 停止任何进程。

.PARAMETER SelfTest
    自检:用一对假进程跑完「启动 → 记录 PID → 合并日志 → 停止」闭环,
    不触碰真实后端。改动本脚本后建议先跑这个。

.EXAMPLE
    .\scripts\backend.cmd
    重启全部后端服务。

.EXAMPLE
    .\scripts\backend.cmd -Only api -Logs api -Follow
    只重启 API,然后跟踪它的日志。

.EXAMPLE
    .\scripts\backend.cmd -Stop
    停掉全部后端服务。

.NOTES
    配套包装脚本 scripts\backend.cmd 负责绕开 PowerShell 执行策略。
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Status,
    [string]$Logs,
    [switch]$Follow,
    [string[]]$Only,
    [int]$HealthTimeoutSec = 180,
    [switch]$SkipNetCheck,
    [switch]$DryRun,
    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'

# 控制台按 UTF-8 输出,否则中文提示在 GBK 控制台里会乱码
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# ---------------------------------------------------------------------------
# 路径
#   $PSScriptRoot = <repo>\scripts,所以仓库根是它的上一级。
#   脚本对当前工作目录不敏感,cd 到任何地方执行结果一致。
# ---------------------------------------------------------------------------
$RepoRoot   = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'src\backend'
$VenvPy     = Join-Path $BackendDir '.venv\Scripts\python.exe'
$CfgFile    = Join-Path $BackendDir 'bisheng\config.yaml'
$RunDir     = Join-Path $RepoRoot '.run'
$LogDir     = Join-Path $RunDir 'logs'
$PidDir     = Join-Path $RunDir 'pids'

# ---------------------------------------------------------------------------
# 服务定义
#   Args 直接对应文档 §3 的命令,只是把 `uv run --frozen --no-sync xxx`
#   换成 venv 里的 python 直接跑模块 —— 少一层 uv 中间进程,
#   PID 就是 python 自己的,重启时杀得干净。
#
#   注意:Celery 节点名用 $env:COMPUTERNAME 拼接,不写文档里的 `knowledge@%h`。
#   因为命令要经过 cmd.exe 解析,`%h` 可能被 cmd 的变量展开规则吃掉。
# ---------------------------------------------------------------------------
$NodeName = if ($env:COMPUTERNAME) { $env:COMPUTERNAME } else { 'localhost' }

$ServiceDefs = [ordered]@{
    'api' = [pscustomobject]@{
        Desc = '后端 API 服务 (uvicorn :7860)'
        Args = @('-m', 'uvicorn', 'bisheng.main:app',
                 '--host', '0.0.0.0', '--port', '7860',
                 '--workers', '1', '--no-access-log')
        Port = 7860
    }
    'knowledge' = [pscustomobject]@{
        Desc = 'Celery 知识库任务 (knowledge_celery + graph_celery)'
        Args = @('-m', 'celery', '-A', 'bisheng.worker.main', 'worker',
                 '-l', 'info', '-c', '20', '-P', 'threads',
                 '-Q', 'knowledge_celery,graph_celery', '-n', "knowledge@$NodeName")
        Port = 0
    }
    'workflow' = [pscustomobject]@{
        Desc = 'Celery 工作流任务 (workflow_celery)'
        Args = @('-m', 'celery', '-A', 'bisheng.worker.main', 'worker',
                 '-l', 'info', '-c', '100', '-P', 'threads',
                 '-Q', 'workflow_celery', '-n', "workflow@$NodeName")
        Port = 0
    }
    'beat' = [pscustomobject]@{
        Desc = 'Celery 定时调度 (beat)'
        Args = @('-m', 'celery', '-A', 'bisheng.worker.main', 'beat', '-l', 'info')
        Port = 0
    }
    'linsight' = [pscustomobject]@{
        Desc = 'Linsight Agent Worker'
        Args = @('-m', 'bisheng.linsight.worker', '--worker_num', '4', '--max_concurrency', '5')
        Port = 0
    }
}
$AllNames = @($ServiceDefs.Keys)

# 共享中间件连通性目标,与文档 §2 的表格一致
$NetTargets = @(
    @{ Host = '172.19.70.107'; Port = 3306;  Name = 'MySQL' },
    @{ Host = '172.19.70.107'; Port = 6379;  Name = 'Redis' },
    @{ Host = '172.19.70.107'; Port = 8080;  Name = 'OpenFGA' },
    @{ Host = '172.19.70.107'; Port = 9200;  Name = 'Elasticsearch' },
    @{ Host = '172.19.70.107'; Port = 19530; Name = 'Milvus' },
    @{ Host = '172.19.70.108'; Port = 9000;  Name = 'MinIO' }
)

# ---------------------------------------------------------------------------
# 输出helper。统一前缀,便于在滚动日志里一眼区分。
# ---------------------------------------------------------------------------
function Write-Step { param([string]$Msg) Write-Host "`n== $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host $Msg -ForegroundColor Green }
function Write-Info { param([string]$Msg) Write-Host $Msg -ForegroundColor Gray }
function Write-Warn { param([string]$Msg) Write-Host $Msg -ForegroundColor Yellow }
function Write-Err  { param([string]$Msg) Write-Host $Msg -ForegroundColor Red }

# ---------------------------------------------------------------------------
# 端口 / 进程工具
# ---------------------------------------------------------------------------
function Test-PortListening {
    <# 指定端口是否处于 LISTEN 状态。优先用 Get-NetTCPConnection,失败回退 netstat。 #>
    param([int]$Port)
    if ($Port -le 0) { return $false }
    try {
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
    } catch {
        $hit = netstat -ano 2>$null | Select-String -Pattern ":$Port\s+\S+\s+LISTENING"
        return [bool]$hit
    }
}

function Stop-ProcTree {
    <# 连同子进程一起终止。
       进程树是 cmd.exe -> python.exe(见 Start-BiShengService 的 cmd 包装),
       必须用 taskkill /T,否则只杀掉 cmd 会留下孤儿 python。 #>
    param([int]$ProcId)
    $ti = Start-Process -FilePath 'taskkill.exe' -ArgumentList '/PID', $ProcId, '/T', '/F' `
                        -Wait -NoNewWindow -PassThru
    return ($ti.ExitCode -eq 0)
}

function Test-ProcAlive {
    param([int]$ProcId)
    return [bool](Get-Process -Id $ProcId -ErrorAction SilentlyContinue)
}

function Get-OwnedProcess {
    <# 扫出「命令行里同时含本仓库 backend 路径 + bisheng 启动标识」的 python 进程。
       用于兜底清理那些不是本脚本启动的实例(例如手工 uv run 起来的),
       限定仓库路径是为了不误伤同一台机器上别人的进程。 #>
    $markers = @('bisheng.main:app', 'bisheng.worker.main', 'bisheng.linsight.worker')
    $result  = @()
    foreach ($proc in (Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue)) {
        $cl = $proc.CommandLine
        if (-not $cl) { continue }
        if ($cl -notlike "*$BackendDir*") { continue }
        foreach ($m in $markers) {
            if ($cl -like "*$m*") { $result += $proc; break }
        }
    }
    return $result
}

# ---------------------------------------------------------------------------
# 停止
# ---------------------------------------------------------------------------
function Stop-BiSheng {
    param([string[]]$Names)

    Write-Step '停止旧进程'

    # -DryRun 必须是「只看不动」。这里必须最先拦截:
    # 下面的 taskkill 是真实的破坏性操作,漏掉这个 guard 会让 DryRun 悄悄杀掉正在运行的后端。
    if ($DryRun) {
        Write-Info '  [dry-run] 跳过:不会终止任何进程'
        return
    }

    $killed = 0

    # 1) 按 pid 文件精确清理(本脚本启动的)
    if (Test-Path $PidDir) {
        foreach ($pf in (Get-ChildItem $PidDir -Filter '*.pid' -ErrorAction SilentlyContinue)) {
            $svc = $pf.BaseName
            if ($Names -and ($Names -notcontains $svc)) { continue }
            $procId = (Get-Content $pf.FullName -Raw -ErrorAction SilentlyContinue)
            if ($procId) { $procId = $procId.Trim() }
            if ($procId -match '^\d+$') {
                if (Test-ProcAlive ([int]$procId)) {
                    if (Stop-ProcTree ([int]$procId)) {
                        Write-Ok "  $svc (PID $procId) 已停止"
                        $killed++
                    } else {
                        Write-Warn "  $svc (PID $procId) 停止失败,可能需要管理员权限"
                    }
                }
            }
            Remove-Item $pf.FullName -Force -ErrorAction SilentlyContinue
        }
    }

    # 2) 兜底:按命令行特征扫残留实例
    foreach ($proc in (Get-OwnedProcess)) {
        if (Stop-ProcTree $proc.ProcessId) {
            Write-Ok "  残留 PID $($proc.ProcessId) 已停止(命令行匹配)"
            $killed++
        }
    }

    if ($killed -eq 0) { Write-Info '  没有发现运行中的后端进程' }

    # 3) 等端口释放,否则紧接着启动会报 address already in use
    if (-not $DryRun) {
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $deadline -and (Test-PortListening 7860)) {
            Start-Sleep -Milliseconds 500
        }
        if (Test-PortListening 7860) {
            Write-Warn '  7860 端口仍被占用,启动 API 可能失败'
        } else {
            Write-Info '  7860 端口已释放'
        }
    }
}

# ---------------------------------------------------------------------------
# 环境自检
# ---------------------------------------------------------------------------
function Test-Preflight {
    Write-Step '环境自检'
    $fatal = $false

    if (Test-Path $VenvPy) {
        Write-Ok "  Python 解释器    $VenvPy"
    } else {
        Write-Err "  找不到虚拟环境解释器:$VenvPy"
        Write-Err '  先执行文档 §1.1 / §1.2 创建并安装依赖'
        $fatal = $true
    }

    if (Test-Path $CfgFile) {
        Write-Ok '  配置文件         src\backend\bisheng\config.yaml'
    } else {
        Write-Err '  缺少 src\backend\bisheng\config.yaml'
        $fatal = $true
    }

    # pandoc 是「导入期硬依赖」:缺失时 import bisheng 会 exit() 且退出码为 0,
    # 表现为服务静默死亡,极难排查,因此这里当作致命问题拦住。
    if ($env:PYPANDOC_PANDOC -and (Test-Path $env:PYPANDOC_PANDOC)) {
        Write-Ok "  PYPANDOC_PANDOC  $env:PYPANDOC_PANDOC"
    } else {
        Write-Err '  PYPANDOC_PANDOC 未设置或指向的文件不存在'
        Write-Err '  → 后端会「退出码 0、无 traceback」静默死亡'
        Write-Err '  → 修复见 docs\本地后端启动-Windows.md §1.3,设置后必须重开终端'
        $fatal = $true
    }

    if (-not $SkipNetCheck) {
        $bad = @()
        foreach ($t in $NetTargets) {
            $client = New-Object System.Net.Sockets.TcpClient
            try {
                $iar = $client.BeginConnect($t.Host, $t.Port, $null, $null)
                if ($iar.AsyncWaitHandle.WaitOne(2000, $false) -and $client.Connected) {
                    # 连通,不逐个刷屏
                } else {
                    $bad += "$($t.Name) $($t.Host):$($t.Port)"
                }
            } catch {
                $bad += "$($t.Name) $($t.Host):$($t.Port)"
            } finally {
                $client.Close()
            }
        }
        if ($bad.Count -eq 0) {
            Write-Ok "  共享中间件       $($NetTargets.Count)/$($NetTargets.Count) 可达"
        } else {
            Write-Warn "  共享中间件不可达:$($bad -join '、')"
            Write-Warn '  后端会启动卡住或连不上数据库/Redis(加 -SkipNetCheck 可跳过本项检查)'
        }
    }

    return (-not $fatal)
}

# ---------------------------------------------------------------------------
# 启动单个服务
# ---------------------------------------------------------------------------
function Start-BiShengService {
    # 形参不能叫 $Args —— 那是 PowerShell 的自动变量(未绑定参数),
    # 同名遮蔽后函数体里取到的是空数组,会导致所有服务以「无参数」启动(实测 python 会直接进 REPL)。
    param(
        [string]   $Name,
        [string[]] $ArgumentList,
        [string]   $Desc
    )

    $log = Join-Path $LogDir "$Name.log"
    # 每次重启都从干净日志开始,避免上一次的报错混进来误导排查
    if (Test-Path $log) { Remove-Item $log -Force -ErrorAction SilentlyContinue }

    # 经 cmd.exe 包装,把 stdout 与 stderr 合并进同一个文件。
    # 直接给 Start-Process 传 -RedirectStandardOutput/-RedirectStandardError 会要求两个不同文件,
    # 而 uvicorn / celery 的日志都走 stderr,分文件后主日志会几乎为空,故用 cmd 合并。
    $inner = '"' + $VenvPy + '" ' + ($ArgumentList -join ' ') + ' > "' + $log + '" 2>&1'
    $cmdArg = '/c "' + $inner + '"'

    if ($DryRun) {
        Write-Info "  [dry-run] $Name : $inner"
        return $null
    }

    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList $cmdArg `
                          -WorkingDirectory $BackendDir `
                          -PassThru -WindowStyle Hidden

    Set-Content -Path (Join-Path $PidDir "$Name.pid") -Value $proc.Id -Encoding ascii
    Write-Ok ("  {0,-10} PID {1,-7} -> .run/logs/{0}.log   ({2})" -f $Name, $proc.Id, $Desc)
    return $proc.Id
}

function Show-LogTail {
    <# 打印某个服务日志的尾部。服务静默死亡时用它可以立刻看到原因。 #>
    param([string]$Name, [int]$Lines = 15)
    $log = Join-Path $LogDir "$Name.log"
    if (-not (Test-Path $log)) { return }
    $tail = Get-Content $log -Tail $Lines -ErrorAction SilentlyContinue
    if ($tail) {
        Write-Host "  --- $Name.log 末尾 $Lines 行 ---" -ForegroundColor DarkGray
        $tail | ForEach-Object { Write-Host "  | $_" -ForegroundColor DarkGray }
    }
}

# ---------------------------------------------------------------------------
# 等待各服务站稳 / API 就绪
# ---------------------------------------------------------------------------
function Wait-ServicesAlive {
    <# 启动后短暂观察:有服务秒退说明配置或依赖有问题,立刻把日志尾巴打出来,
       而不是干等 3 分钟的 /health 超时。 #>
    param([hashtable]$Pids)

    if ($DryRun -or $Pids.Count -eq 0) { return $true }

    Start-Sleep -Seconds 8
    $dead = @()
    foreach ($name in $Pids.Keys) {
        if (-not (Test-ProcAlive $Pids[$name])) { $dead += $name }
    }
    if ($dead.Count -eq 0) {
        Write-Info "  $($Pids.Count) 个进程均已站稳"
        return $true
    }
    foreach ($name in $dead) {
        Write-Err "  [$name] 启动后立即退出"
        Show-LogTail -Name $name
    }
    return $false
}

function Wait-Health {
    param([int]$TimeoutSec)

    if ($DryRun) { return $true }

    Write-Host ''
    Write-Host '等待 API /health 就绪 ' -NoNewline -ForegroundColor Cyan
    $url = 'http://127.0.0.1:7860/health'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        try {
            $resp = Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing
            if ($resp.StatusCode -eq 200) {
                $secs = [int]$sw.Elapsed.TotalSeconds
                Write-Host " 就绪(${secs}s)" -ForegroundColor Green
                return $true
            }
        } catch {
            # 冷启动期间连不上是正常的,继续轮询
        }
        Write-Host '.' -NoNewline
        Start-Sleep -Seconds 2
    }
    Write-Host ''
    Write-Err "  超时(${TimeoutSec}s):/health 仍未返回 200"
    Show-LogTail -Name 'api' -Lines 25
    return $false
}

# ---------------------------------------------------------------------------
# 子命令:-Status / -Logs
# ---------------------------------------------------------------------------
function Show-Status {
    Write-Step '后端服务状态'
    $anyRunning = $false
    foreach ($name in $AllNames) {
        $pf = Join-Path $PidDir "$name.pid"
        $state = '未运行'
        $color = 'DarkGray'
        if (Test-Path $pf) {
            # $pf 是 Join-Path 返回的路径字符串,不能按 FileInfo 取 .FullName(会得到空值)
            $procId = (Get-Content $pf -Raw).Trim()
            if ($procId -match '^\d+$' -and (Test-ProcAlive ([int]$procId))) {
                $state = "运行中 (PID $procId)"
                $color = 'Green'
                $anyRunning = $true
            }
        }
        Write-Host ("  {0,-10} {1}" -f $name, $state) -ForegroundColor $color
    }

    # API 端口单独探一下:pid 文件可能因手工启停而失真
    if (Test-PortListening 7860) {
        Write-Host '  7860 端口    LISTENING' -ForegroundColor Green
        $anyRunning = $true
    } else {
        Write-Host '  7860 端口    未监听' -ForegroundColor DarkGray
    }

    Write-Host ''
    if ($anyRunning) { Write-Info '  跟踪日志:.\scripts\backend.cmd -Logs api -Follow' }
    else             { Write-Info '  启动全部:.\scripts\backend.cmd' }
}

function Show-ServiceLog {
    param([string]$Name, [switch]$Follow)

    if ($AllNames -notcontains $Name) {
        Write-Err "未知服务 '$Name'。可选:$($AllNames -join ' / ')"
        return
    }
    $log = Join-Path $LogDir "$Name.log"
    if (-not (Test-Path $log)) {
        Write-Warn "还没有日志:$log"
        Write-Warn '请先启动服务(.\scripts\backend.cmd)'
        return
    }
    if ($Follow) {
        Write-Info "跟踪 $log(按 Ctrl+C 退出)"
        Get-Content $log -Wait -Tail 50
    } else {
        Get-Content $log -Tail 80
    }
}

# ---------------------------------------------------------------------------
# 自检:-SelfTest
#   用假进程走完整链路,验证「启动 → PID 记录 → 日志合并 → 停止」都正常,
#   不触碰真实后端。改动本脚本后先跑它。见文档 §4 的交付说明。
# ---------------------------------------------------------------------------
function Invoke-SelfTest {
    Write-Step '自检:启动 → 记录 PID → 合并日志 → 停止'
    $name = '__selftest'
    $tmpPy = Join-Path $RunDir '_selftest.py'
    $log   = Join-Path $LogDir "$name.log"
    $pf    = Join-Path $PidDir "$name.pid"
    $pass  = $true

    Set-Content -Path $tmpPy -Encoding UTF8 -Value @'
import sys, time
print("SELFTEST-OUT", flush=True)
print("SELFTEST-ERR", file=sys.stderr, flush=True)
time.sleep(120)
'@
    Remove-Item $log, $pf -Force -ErrorAction SilentlyContinue

    # 1) 能启动,并写出 PID 与日志
    $procId = Start-BiShengService -Name $name -ArgumentList @($tmpPy) -Desc '自检假进程'
    if (-not $procId) { Write-Err '  启动失败'; return $false }
    Start-Sleep -Seconds 3

    if (Test-Path $pf) { Write-Ok '  [1/4] PID 文件已写入' }
    else { Write-Err '  [1/4] 未生成 PID 文件'; $pass = $false }

    # 2) 日志里 stdout 与 stderr 都在(验证 cmd 合并重定向)
    $content = if (Test-Path $log) { Get-Content $log -Raw } else { '' }
    if ($content -match 'SELFTEST-OUT' -and $content -match 'SELFTEST-ERR') {
        Write-Ok '  [2/4] stdout + stderr 已合并进同一日志'
    } else {
        Write-Err "  [2/4] 日志内容不符合预期:$content"
        $pass = $false
    }

    # 3) 进程存活
    if (Test-ProcAlive $procId) { Write-Ok "  [3/4] 进程存活 (PID $procId)" }
    else { Write-Err '  [3/4] 进程未存活'; $pass = $false }

    # 4) 能连子进程一起杀掉,不留孤儿
    Stop-ProcTree $procId | Out-Null
    Start-Sleep -Seconds 2
    $leftover = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                  Where-Object { $_.CommandLine -and $_.CommandLine -like "*$tmpPy*" })
    if (-not (Test-ProcAlive $procId) -and $leftover.Count -eq 0) {
        Write-Ok '  [4/4] 进程树已完全终止(无孤儿进程)'
    } else {
        Write-Err '  [4/4] 仍有残留进程'
        $pass = $false
    }

    Remove-Item $tmpPy, $log, $pf -Force -ErrorAction SilentlyContinue

    Write-Host ''
    if ($pass) { Write-Ok '自检通过。' } else { Write-Err '自检失败,请勿用于真实启动。' }
    return $pass
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $RunDir, $LogDir, $PidDir | Out-Null

if ($SelfTest) {
    exit $(if (Invoke-SelfTest) { 0 } else { 1 })
}

if ($Status) {
    Show-Status
    exit 0
}

if ($Logs) {
    Show-ServiceLog -Name $Logs -Follow:$Follow
    exit 0
}

# 解析 -Only
$targets = $AllNames
if ($Only) {
    $invalid = @($Only | Where-Object { $AllNames -notcontains $_ })
    if ($invalid.Count -gt 0) {
        Write-Err "-Only 含未知服务:$($invalid -join '、')。可选:$($AllNames -join ' / ')"
        exit 1
    }
    $targets = @($AllNames | Where-Object { $Only -contains $_ })
}

# 这两个变量会被子进程继承:config 是 bisheng 加载配置的必需项,
# PYTHONPATH 保证以脚本路径方式启动时也能 import bisheng(文档 §3.3)。
$env:config = 'config.yaml'

if ($Stop) {
    Stop-BiSheng -Names $targets
    Write-Host ''
    Write-Ok '已停止。'
    exit 0
}

Write-Host 'BiSheng 本地后端启动器' -ForegroundColor White
Write-Host "  仓库根:$RepoRoot"
Write-Host "  目标  :$($targets -join ', ')"
if ($DryRun) { Write-Warn '  [dry-run] 只打印命令,不实际执行' }

Stop-BiSheng -Names $targets

if (-not (Test-Preflight)) {
    Write-Host ''
    Write-Err '环境自检未通过,已中止启动。'
    exit 1
}

Write-Step '启动服务'
$started = @{}
foreach ($name in $targets) {
    $def = $ServiceDefs[$name]
    $procId = Start-BiShengService -Name $name -ArgumentList $def.Args -Desc $def.Desc
    if ($procId) { $started[$name] = $procId }
}

if ($DryRun) {
    Write-Host ''
    Write-Ok '[dry-run] 完成,未启动任何进程。'
    exit 0
}

$alive = Wait-ServicesAlive -Pids $started
$healthy = $false
if ($targets -contains 'api') {
    $healthy = Wait-Health -TimeoutSec $HealthTimeoutSec
} else {
    Write-Info '本次未启动 api,跳过 /health 检查'
}

Write-Host ''
if (-not $alive) {
    Write-Err '部分服务启动即退出,详见上方日志。'
    exit 1
}
if (($targets -contains 'api') -and (-not $healthy)) {
    exit 1
}

Write-Ok '后端已就绪: http://127.0.0.1:7860'
Write-Info '  跟踪日志  .\scripts\backend.cmd -Logs api -Follow'
Write-Info '  查看状态  .\scripts\backend.cmd -Status'
Write-Info '  停止全部  .\scripts\backend.cmd -Stop'
exit 0
