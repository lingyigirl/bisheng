#Requires -Version 5.1
<#
.SYNOPSIS
    BiSheng 本地前端一键启动 / 重启(Windows)。

.DESCRIPTION
    把 docs/本地前端启动-Windows.md §2 里需要手工执行的两条命令收敛成一条:

        client   dev:client   vite  :4001  base path = /workspace
        platform dev:platform vite  :3001  base path = /

    与 scripts/backend.ps1 同一套约定:进程后台运行,stdout + stderr 合并写入
    .run/logs/<name>.log,进程号记录在 .run/pids/<name>.pid;
    「重复执行 = 全部重启」,会先停掉上一轮(含手工 pnpm dev 起来的实例)。

    文件编码必须是 UTF-8 **带 BOM**。Windows PowerShell 5.1 在中文 Windows 上
    会把无 BOM 的 UTF-8 当 GBK 解码,中文注释里可能还原出 0x60(反引号),
    被当成续行符吃掉下一行代码。改动本文件后请确认 BOM 仍在。

.PARAMETER Stop
    只停止,不启动。

.PARAMETER Status
    显示各 dev server 的运行状态(读 pid 文件 + 探端口),不做任何变更。

.PARAMETER Logs
    打印指定服务的日志末尾若干行。取值:client / platform / ui。

.PARAMETER Follow
    配合 -Logs 使用,持续跟踪日志(相当于 tail -f)。

.PARAMETER Only
    只启动指定服务,例如: -Only client。默认 client,platform。
    文档站(ui)不在默认集合里,需要时显式 -Only ui。

.PARAMETER HealthTimeoutSec
    等待页面返回 200 的最长秒数。首次启动要先跑 locales 构建,默认 300 秒。

.PARAMETER SkipBackendCheck
    跳过「后端 :7860 是否可达」的检查。后端不起并不影响 dev server 启动,
    只影响页面里的接口请求,因此这里默认只告警、不中止。

.PARAMETER DryRun
    只打印将要执行的命令,不真的启动 / 停止任何进程。

.EXAMPLE
    .\scripts\frontend.cmd
    重启 client + platform。

.EXAMPLE
    .\scripts\frontend.cmd -Only client -Logs client -Follow
    只重启 client,然后跟踪它的日志。

.EXAMPLE
    .\scripts\frontend.cmd -Stop
    停掉全部前端 dev server。

.NOTES
    配套包装脚本 scripts\frontend.cmd 负责绕开 PowerShell 执行策略。
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Status,
    [string]$Logs,
    [switch]$Follow,
    [string[]]$Only,
    [int]$HealthTimeoutSec = 300,
    [switch]$SkipBackendCheck,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# 控制台按 UTF-8 输出,否则中文提示在 GBK 控制台里会乱码
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# ---------------------------------------------------------------------------
# 路径
#   $PSScriptRoot = <repo>\scripts,所以仓库根是它的上一级。
# ---------------------------------------------------------------------------
$RepoRoot    = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $RepoRoot 'src\frontend'
$RunDir      = Join-Path $RepoRoot '.run'
$LogDir      = Join-Path $RunDir 'logs'
$PidDir      = Join-Path $RunDir 'pids'

# ---------------------------------------------------------------------------
# pnpm 解析
#   本机 PATH 上的 pnpm.CMD 是独立安装的 shim,会尝试联网下载 pnpm 12.11.2
#   (公司 npm 代理已失效 → 直接失败)。corepack 读 package.json 的
#   packageManager(pnpm@9.15.9)且已缓存,是可靠入口,优先使用。
# ---------------------------------------------------------------------------
$PnpmCmd = $null
if (Get-Command corepack -ErrorAction SilentlyContinue) {
    $PnpmCmd = 'corepack pnpm'
} elseif (Get-Command pnpm -ErrorAction SilentlyContinue) {
    $PnpmCmd = 'pnpm'
}

# ---------------------------------------------------------------------------
# 服务定义
#   Script 是 src/frontend/package.json 的根脚本;ProbeUrl 是启动完成的判据。
# ---------------------------------------------------------------------------
$ServiceDefs = [ordered]@{
    'client' = [pscustomobject]@{
        Desc     = '终端用户聊天端 (vite :4001, base /workspace)'
        Script   = 'dev:client'
        Port     = 4001
        ProbeUrl = 'http://127.0.0.1:4001/workspace/'
    }
    'platform' = [pscustomobject]@{
        Desc     = '管理端 (vite :3001, base /)'
        Script   = 'dev:platform'
        Port     = 3001
        ProbeUrl = 'http://127.0.0.1:3001/'
    }
    'ui' = [pscustomobject]@{
        Desc     = '设计系统文档站 (rspress, 端口按提示)'
        Script   = 'dev:ui'
        Port     = 0
        ProbeUrl = ''
    }
}
$DefaultNames = @('client', 'platform')
$AllNames     = @($ServiceDefs.Keys)

# ---------------------------------------------------------------------------
# 输出 helper。统一前缀,便于在滚动日志里一眼区分。
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
    param([int]$Port)
    if ($Port -le 0) { return $false }
    try {
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
    } catch {
        $hit = netstat -ano 2>$null | Select-String -Pattern ":$Port\s+\S+\s+LISTENING"
        return [bool]$hit
    }
}

function Get-PortOwner {
    <# 返回监听指定端口的进程 PID;取不到返回 0。 #>
    param([int]$Port)
    if ($Port -le 0) { return 0 }
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                Select-Object -First 1
        if ($conn) { return [int]$conn.OwningProcess }
    } catch { }
    $line = netstat -ano 2>$null | Select-String -Pattern ":$Port\s+\S+\s+LISTENING" |
            Select-Object -First 1
    if ($line) {
        $parts = ($line.Line -replace '^\s+', '') -split '\s+'
        if ($parts.Count -ge 5 -and $parts[-1] -match '^\d+$') { return [int]$parts[-1] }
    }
    return 0
}

function Stop-ProcTree {
    <# 连同子进程一起终止。
       进程树是 cmd.exe -> corepack -> node(vite),必须用 taskkill /T,
       否则只杀掉 cmd 会留下孤儿 node。 #>
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
    <# 扫出「命令行里含本仓库 frontend 路径 + vite/rspress 标识」的 node 进程。
       用于兜底清理不是本脚本启动的实例(例如手工 pnpm dev:client)。 #>
    $markers = @('vite', 'rspress')
    $result  = @()
    foreach ($proc in (Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue)) {
        $cl = $proc.CommandLine
        if (-not $cl) { continue }
        if ($cl -notlike "*$FrontendDir*") { continue }
        foreach ($m in $markers) {
            if ($cl -like "*$m*") { $result += $proc; break }
        }
    }
    return $result
}

# ---------------------------------------------------------------------------
# 停止
# ---------------------------------------------------------------------------
function Stop-Frontend {
    param([string[]]$Names)

    Write-Step '停止旧进程'

    # -DryRun 必须是「只看不动」。taskkill 是真实破坏性操作,最先拦截。
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

    # 2) 兜底:按命令行特征扫残留 node 实例
    foreach ($proc in (Get-OwnedProcess)) {
        if (Stop-ProcTree $proc.ProcessId) {
            Write-Ok "  残留 PID $($proc.ProcessId) 已停止(命令行匹配)"
            $killed++
        }
    }

    if ($killed -eq 0) { Write-Info '  没有发现运行中的前端进程' }

    # 3) 等端口释放。client 配了 strictPort,4001 被别的进程占着会直接启动失败,
    #    因此这里额外按端口反查一次;只有确认属于本仓库才杀,避免误伤。
    foreach ($name in $Names) {
        $port = $ServiceDefs[$name].Port
        if ($port -le 0) { continue }
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline -and (Test-PortListening $port)) {
            Start-Sleep -Milliseconds 500
        }
        if (Test-PortListening $port) {
            $owner = Get-PortOwner $port
            if ($owner -gt 0) {
                $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue).CommandLine
                if ($cl -and $cl -like "*$FrontendDir*") {
                    if (Stop-ProcTree $owner) {
                        Write-Ok "  端口 $port 占用者 PID $owner 已停止"
                        Start-Sleep -Seconds 1
                        continue
                    }
                }
            }
            Write-Warn "  端口 $port 仍被占用(占用者不是本仓库进程),$name 可能启动失败"
        } else {
            Write-Info "  端口 $port 已释放"
        }
    }
}

# ---------------------------------------------------------------------------
# 环境自检
# ---------------------------------------------------------------------------
function Test-Preflight {
    Write-Step '环境自检'
    $fatal = $false

    if (Test-Path $FrontendDir) {
        Write-Ok "  前端目录         src\frontend"
    } else {
        Write-Err "  找不到前端目录:$FrontendDir"
        $fatal = $true
    }

    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-Ok "  Node.js          $(node -v)"
    } else {
        Write-Err '  找不到 node,请先安装 Node.js'
        $fatal = $true
    }

    if ($PnpmCmd) {
        Write-Ok "  pnpm 入口        $PnpmCmd"
    } else {
        Write-Err '  找不到 corepack / pnpm,请先执行文档 §1.1'
        $fatal = $true
    }

    if (Test-Path (Join-Path $FrontendDir 'node_modules')) {
        Write-Ok '  前端依赖         node_modules 已存在'
    } else {
        Write-Err '  缺少 src\frontend\node_modules,请先执行 pnpm install(文档 §1.2)'
        $fatal = $true
    }

    # env 文件缺失不会阻止 dev server 启动,但会让代理全挂,因此只告警
    $envFiles = @(
        (Join-Path $FrontendDir '.env.development'),
        (Join-Path $FrontendDir 'platform\.env.development.local')
    )
    $missingEnv = @($envFiles | Where-Object { -not (Test-Path $_) })
    if ($missingEnv.Count -eq 0) {
        Write-Ok '  环境变量文件     已就位'
    } else {
        foreach ($f in $missingEnv) { Write-Warn "  缺少环境变量文件:$f(见文档 §3)" }
    }

    if (-not $SkipBackendCheck) {
        if (Test-PortListening 7860) {
            Write-Ok '  后端 :7860       可达'
        } else {
            Write-Warn '  后端 :7860 未监听:页面能打开,但所有 /api 请求会 502'
            Write-Warn '  先启动后端:.\scripts\backend.cmd(或加 -SkipBackendCheck 跳过本项)'
        }
    }

    return (-not $fatal)
}

# ---------------------------------------------------------------------------
# 启动单个服务
# ---------------------------------------------------------------------------
function Start-FrontendService {
    # 形参不能叫 $Args —— 那是 PowerShell 的自动变量(未绑定参数),同名遮蔽后
    # 函数体里取到的是空数组,会导致服务以「无参数」启动。
    param(
        [string] $Name,
        [string] $ScriptName,
        [string] $Desc
    )

    $log = Join-Path $LogDir "$Name.log"
    # 每次重启都从干净日志开始,避免上一次的报错混进来误导排查
    if (Test-Path $log) { Remove-Item $log -Force -ErrorAction SilentlyContinue }

    # 经 cmd.exe 包装,把 stdout 与 stderr 合并进同一个文件(vite 的启动日志
    # 大量走 stderr,分文件后主日志会几乎为空)。
    $inner  = "$PnpmCmd $ScriptName" + ' > "' + $log + '" 2>&1'
    $cmdArg = '/c "' + $inner + '"'

    if ($DryRun) {
        Write-Info "  [dry-run] $Name : $inner"
        return $null
    }

    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList $cmdArg `
                          -WorkingDirectory $FrontendDir `
                          -PassThru -WindowStyle Hidden

    Set-Content -Path (Join-Path $PidDir "$Name.pid") -Value $proc.Id -Encoding ascii
    Write-Ok ("  {0,-10} PID {1,-7} -> .run/logs/{0}.log   ({2})" -f $Name, $proc.Id, $Desc)
    return $proc.Id
}

function Show-LogTail {
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
# 等待各 dev server 站稳 / 页面就绪
# ---------------------------------------------------------------------------
function Wait-ServicesAlive {
    <# 启动后短暂观察:有服务秒退说明端口被占、依赖缺失或 env 配置有误,
       立刻把日志尾巴打出来,而不是干等超时。 #>
    param([hashtable]$Pids)

    if ($DryRun -or $Pids.Count -eq 0) { return $true }

    Start-Sleep -Seconds 10
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

function Wait-Pages {
    <# 逐轮轮询各服务的 ProbeUrl,全部 200 才算就绪。
       首次启动要先跑 locales 构建 + Vite 预打包,耗时明显长于二次启动。 #>
    param([string[]]$Names, [int]$TimeoutSec)

    if ($DryRun) { return $true }

    $pending = @($Names | Where-Object { $ServiceDefs[$_].ProbeUrl })
    if ($pending.Count -eq 0) { return $true }

    Write-Host ''
    Write-Host ("等待页面就绪 ({0}) " -f ($pending -join ', ')) -NoNewline -ForegroundColor Cyan
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $ready = @{}
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec -and $ready.Count -lt $pending.Count) {
        foreach ($name in $pending) {
            if ($ready.ContainsKey($name)) { continue }
            try {
                $resp = Invoke-WebRequest -Uri $ServiceDefs[$name].ProbeUrl -TimeoutSec 5 -UseBasicParsing
                if ($resp.StatusCode -eq 200) { $ready[$name] = $true }
            } catch {
                # 启动期间连不上是正常的,继续轮询
            }
        }
        if ($ready.Count -lt $pending.Count) {
            Write-Host '.' -NoNewline
            Start-Sleep -Seconds 2
        }
    }
    $secs = [int]$sw.Elapsed.TotalSeconds
    Write-Host ''
    foreach ($name in $pending) {
        if ($ready.ContainsKey($name)) {
            Write-Ok ("  {0,-10} 就绪  {1}({2}s)" -f $name, $ServiceDefs[$name].ProbeUrl, $secs)
        } else {
            Write-Err ("  {0,-10} 超时  {1}" -f $name, $ServiceDefs[$name].ProbeUrl)
            Show-LogTail -Name $name -Lines 25
        }
    }
    return ($ready.Count -eq $pending.Count)
}

# ---------------------------------------------------------------------------
# 子命令:-Status / -Logs
# ---------------------------------------------------------------------------
function Show-Status {
    Write-Step '前端服务状态'
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
        $def = $ServiceDefs[$name]
        if ($def.Port -gt 0) {
            if (Test-PortListening $def.Port) { $state += "  :$($def.Port) LISTENING" }
            else                              { $state += "  :$($def.Port) 未监听" }
        }
        Write-Host ("  {0,-10} {1}" -f $name, $state) -ForegroundColor $color
    }

    Write-Host ''
    if ($anyRunning) { Write-Info '  跟踪日志:.\scripts\frontend.cmd -Logs client -Follow' }
    else             { Write-Info '  启动全部:.\scripts\frontend.cmd' }
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
        Write-Warn '请先启动服务(.\scripts\frontend.cmd)'
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
# 主流程
# ---------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $RunDir, $LogDir, $PidDir | Out-Null

if ($Status) {
    Show-Status
    exit 0
}

if ($Logs) {
    Show-ServiceLog -Name $Logs -Follow:$Follow
    exit 0
}

# 解析 -Only,默认启动 client + platform(文档站 ui 需显式指定)
$targets = $DefaultNames
if ($Only) {
    $invalid = @($Only | Where-Object { $AllNames -notcontains $_ })
    if ($invalid.Count -gt 0) {
        Write-Err "-Only 含未知服务:$($invalid -join '、')。可选:$($AllNames -join ' / ')"
        exit 1
    }
    $targets = @($AllNames | Where-Object { $Only -contains $_ })
}

if ($Stop) {
    Stop-Frontend -Names $targets
    Write-Host ''
    Write-Ok '已停止。'
    exit 0
}

Write-Host 'BiSheng 本地前端启动器' -ForegroundColor White
Write-Host "  仓库根:$RepoRoot"
Write-Host "  目标  :$($targets -join ', ')"
if ($DryRun) { Write-Warn '  [dry-run] 只打印命令,不实际执行' }

Stop-Frontend -Names $targets

if (-not (Test-Preflight)) {
    Write-Host ''
    Write-Err '环境自检未通过,已中止启动。'
    exit 1
}

Write-Step '启动服务'
$started = @{}
foreach ($name in $targets) {
    $def = $ServiceDefs[$name]
    $procId = Start-FrontendService -Name $name -ScriptName $def.Script -Desc $def.Desc
    if ($procId) { $started[$name] = $procId }
}

if ($DryRun) {
    Write-Host ''
    Write-Ok '[dry-run] 完成,未启动任何进程。'
    exit 0
}

$alive = Wait-ServicesAlive -Pids $started
$ready = Wait-Pages -Names $targets -TimeoutSec $HealthTimeoutSec

Write-Host ''
if (-not $alive) {
    Write-Err '部分服务启动即退出,详见上方日志。'
    exit 1
}
if (-not $ready) {
    exit 1
}

Write-Ok '前端已就绪:'
foreach ($name in $targets) {
    $url = $ServiceDefs[$name].ProbeUrl
    if ($url) { Write-Info "  $name  $url" }
}
Write-Info '  跟踪日志  .\scripts\frontend.cmd -Logs client -Follow'
Write-Info '  查看状态  .\scripts\frontend.cmd -Status'
Write-Info '  停止全部  .\scripts\frontend.cmd -Stop'
exit 0
