#Requires -Version 5.1
<#
.SYNOPSIS
    启动 Aragon Research Terminal 的全部本地服务（API :8000 + Web :3000）。

.DESCRIPTION
    一条命令拉起后端 FastAPI（uvicorn，必须单 worker）和前端 Next.js 开发服务器：

      * 自动读取仓库根目录的 .env（缺失时从 .env.example 复制一份），
        已经存在的同名环境变量优先，不会被 .env 覆盖；
      * 端口已被占用时不重复启动：探活通过就复用（幂等），
        否则提示用 -Restart 先停再起；
      * 两个服务都在后台运行（隐藏窗口），stdout/stderr 落到 .run\logs\，
        PID 与启动信息落到 .run\api.pid / .run\web.pid；
      * 必须等到端口真正监听、且 API 的 /api/v1/health 返回 200 才算“启动成功”，
        失败会直接把日志尾巴打到屏幕上，而不是让你自己去翻文件；
      * 关闭服务用 .\stop-all.ps1。

    运行目录 .run\ 只是本机运行态（日志 + PID），随时可以删掉。

.PARAMETER NoBrowser
    启动完成后不自动打开浏览器。

.PARAMETER Mock
    本次进程内强制 AGENT_MODE=mock：不调用 aragon CLI，走离线示例生成。

.PARAMETER Restart
    先调用 stop-all.ps1 停掉已经在跑的服务，再重新启动（用于“改完配置重开”）。

.PARAMETER NoReload
    API 不带 --reload（关闭热重载：启动更快、进程树更简单）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\start-all.ps1

.EXAMPLE
    .\start-all.ps1 -Restart -Mock -NoBrowser
#>
[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$Mock,
    [switch]$Restart,
    [switch]$NoReload
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# ---------------------------------------------------------------------------
# 0. 路径与输出小工具
# ---------------------------------------------------------------------------
if ($PSScriptRoot) {
    $Root = $PSScriptRoot
} else {
    $Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
}
$Root = (Resolve-Path -LiteralPath $Root).Path
Set-Location -LiteralPath $Root

$RunDir = Join-Path $Root '.run'
$LogDir = Join-Path $RunDir 'logs'
foreach ($dir in @($RunDir, $LogDir)) {
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

function Step {
    param([string]$Title)
    Write-Host ''
    Write-Host "==> $Title" -ForegroundColor Cyan
}

function OkLine {
    param([string]$Text)
    Write-Host "    [OK]   $Text" -ForegroundColor Green
}

function WarnLine {
    param([string]$Text)
    Write-Host "    [WARN] $Text" -ForegroundColor Yellow
}

function NoteLine {
    param([string]$Text)
    Write-Host "           $Text" -ForegroundColor DarkGray
}

function FailLine {
    param([string]$Text)
    Write-Host "    [FAIL] $Text" -ForegroundColor Red
}

function Exit-WithError {
    param([string]$Message, [string[]]$Hints = @())
    Write-Host ''
    Write-Host "启动失败：$Message" -ForegroundColor Red
    foreach ($hint in $Hints) {
        Write-Host "  - $hint" -ForegroundColor Yellow
    }
    Write-Host ''
    exit 1
}

function Show-LogTail {
    param([string]$Path, [int]$Lines = 15)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $tail = Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue
    if (-not $tail) { return }
    Write-Host "    ---------- $([System.IO.Path]::GetFileName($Path)) 末尾 $Lines 行 ----------" -ForegroundColor DarkYellow
    foreach ($line in $tail) {
        Write-Host "    $line" -ForegroundColor DarkYellow
    }
}

# ---------------------------------------------------------------------------
# 1. 探测小工具（端口 / TCP / 进程）
# ---------------------------------------------------------------------------
function Get-PortOwner {
    param([int]$Port)

    if ($null -ne (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        try {
            $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
                Select-Object -First 1
            if ($conn) { return [int]$conn.OwningProcess }
        } catch {
            return 0
        }
        return 0
    }

    # 老系统 / 精简系统没有 Get-NetTCPConnection 时退回 netstat
    $hit = & netstat -ano -p TCP 2>$null |
        Select-String -Pattern "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$" |
        Select-Object -Last 1
    if ($hit -and ($hit.Line -match 'LISTENING\s+(\d+)')) {
        return [int]$Matches[1]
    }
    return 0
}

function Test-TcpPort {
    param([int]$Port, [int]$TimeoutMs = 700)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-HttpOk {
    # 只认 200：401/403/404 也“有响应”，但它们不说明这是我们自己的服务，
    # 用它们当就绪判据会把别人的 dev server 认成本项目服务并打印“已就绪”。
    param([string]$Url, [int]$TimeoutSec = 5)
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        if ($null -eq $resp.StatusCode) { return $false }
        return ([int]$resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-PortOwnerInfo {
    # 端口的占用者：pid + 进程名 + 命令行。用来判断“占用者到底是不是本项目的服务”。
    param([int]$Port)
    $owner = Get-PortOwner -Port $Port
    if ($owner -le 0) {
        return [pscustomobject]@{ Pid = 0; Name = ''; CommandLine = '' }
    }
    $name = ''
    $cmdLine = ''
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue
    if ($cim) {
        if ($cim.Name) { $name = [string]$cim.Name }
        if ($cim.CommandLine) { $cmdLine = [string]$cim.CommandLine }
    }
    if (-not $name) {
        $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
        if ($proc) { $name = $proc.ProcessName }
    }
    return [pscustomobject]@{ Pid = $owner; Name = $name; CommandLine = $cmdLine }
}

function Test-LooksLikeOurs {
    # 端口被占用时，判断占用者是不是本项目的服务：
    #   api —— 命令行是 `-m uvicorn app.main:app ... --port <API 端口>`
    #   web —— 命令行里带本仓库路径，且是 next dev / start-server 的形态
    # 判断不出身份时一律按“外来占用”处理：宁可停下提示，也不冒用别人的进程，
    # 因为 stop-all.ps1 会把占着这两个端口的进程全部收掉。
    param([string]$Name, [string]$CommandLine, [int]$Port, [string]$RootPath)
    if (-not $CommandLine) { return $false }
    if ($Name -eq 'api') {
        if ($CommandLine -notmatch '-m\s+uvicorn\s+app\.main:app') { return $false }
        $cmdPort = 0
        if ($CommandLine -match '--port[=\s]+(\d+)') { $cmdPort = [int]$Matches[1] }
        return ($cmdPort -eq $Port)
    }
    if ($CommandLine -notmatch [regex]::Escape($RootPath)) { return $false }
    return (($CommandLine -match 'next\\dist\\bin\\next"?\s+dev\b') -or
            ($CommandLine -match 'next\\dist\\server\\lib\\(start-server|next-dev)\.js'))
}

# ---------------------------------------------------------------------------
# 2. .env 解析（不覆盖已存在的真实环境变量）
# ---------------------------------------------------------------------------
function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    $count = 0
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $trimmed = "$line".Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $key = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2) {
                $firstChar = $value.Substring(0, 1)
                $lastChar = $value.Substring($value.Length - 1, 1)
                if (($firstChar -eq '"' -and $lastChar -eq '"') -or ($firstChar -eq "'" -and $lastChar -eq "'")) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
            }
            if (-not [Environment]::GetEnvironmentVariable($key, 'Process')) {
                [Environment]::SetEnvironmentVariable($key, $value, 'Process')
                $count++
            }
        }
    }
    return $count
}

# ---------------------------------------------------------------------------
# 3. 后台启动 + 就绪等待
# ---------------------------------------------------------------------------
function Start-ManagedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [int]$Port,
        [hashtable]$ExtraEnv,
        [string]$Url
    )

    $outLog = Join-Path $LogDir "$Name.out.log"
    $errLog = Join-Path $LogDir "$Name.err.log"
    $pidFile = Join-Path $RunDir "$Name.pid"
    foreach ($logFile in @($outLog, $errLog)) {
        if (Test-Path -LiteralPath $logFile) { Remove-Item -LiteralPath $logFile -Force }
    }

    # 临时注入子进程环境变量，启动完立刻恢复
    $backup = @{}
    foreach ($key in $ExtraEnv.Keys) {
        $backup[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $ExtraEnv[$key], 'Process')
    }
    try {
        $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
            -WindowStyle Hidden -PassThru
    } finally {
        foreach ($key in $ExtraEnv.Keys) {
            [Environment]::SetEnvironmentVariable($key, $backup[$key], 'Process')
        }
    }

    # startedAt / processName 是 stop-all.ps1 用来核对“这个 PID 还是当初那个进程吗”的凭据：
    # PID 会被系统复用，只按 PID 杀进程迟早会误伤（典型：重启后 .run\api.pid 的 27188 变成 explorer.exe）。
    $startedAt = Get-Date
    if ($proc.StartTime) { $startedAt = $proc.StartTime }
    $processName = ''
    try { $processName = [string]$proc.ProcessName } catch { $processName = '' }

    $info = [ordered]@{
        name             = $Name
        pid              = $proc.Id
        processName      = $processName
        port             = $Port
        url              = $Url
        startedAt        = $startedAt.ToString('yyyy-MM-dd HH:mm:ss')
        workingDirectory = $WorkingDirectory
        commandLine      = (@($FilePath) + $Arguments) -join ' '
        stdout           = $outLog
        stderr           = $errLog
    }
    ($info | ConvertTo-Json -Depth 3) | Set-Content -LiteralPath $pidFile -Encoding UTF8

    return [pscustomobject]@{
        Process = $proc
        Stdout  = $outLog
        Stderr  = $errLog
        PidFile = $pidFile
    }
}

function Wait-ServiceReady {
    param(
        [int]$Port,
        $Proc,
        [string]$HealthUrl,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $listening = $false
    while ((Get-Date) -lt $deadline) {
        if ($Proc -and $Proc.HasExited) { return 'exited' }
        if (Test-TcpPort -Port $Port) { $listening = $true; break }
        Start-Sleep -Milliseconds 400
    }
    if (-not $listening) {
        if ($Proc -and $Proc.HasExited) { return 'exited' }
        return 'timeout'
    }
    if (-not $HealthUrl) { return 'ready' }

    # 端口已监听，再等业务健康检查（首启要建引擎、跑迁移，会慢几秒）
    $healthDeadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $healthDeadline) {
        if ($Proc -and $Proc.HasExited) { return 'exited' }
        if (Test-HttpOk -Url $HealthUrl -TimeoutSec 5) { return 'ready' }
        Start-Sleep -Milliseconds 500
    }
    return 'unhealthy'
}

function Ensure-Service {
    param(
        [string]$Name,
        [string]$Label,
        [int]$Port,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [hashtable]$ExtraEnv,
        [string]$HealthUrl,
        [string]$Url,
        [int]$TimeoutSeconds
    )

    $result = [pscustomobject]@{
        Name    = $Name
        Label   = $Label
        Port    = $Port
        Url     = $Url
        Pid     = 0
        State   = ''
        Message = ''
    }

    $ownerInfo = Get-PortOwnerInfo -Port $Port
    if ($ownerInfo.Pid -ne 0) {
        $owner = $ownerInfo.Pid
        $ownerName = if ($ownerInfo.Name) { $ownerInfo.Name } else { '?' }
        $result.Pid = $owner

        $probeUrl = if ($HealthUrl) { $HealthUrl } else { $Url }
        $looksOurs = Test-LooksLikeOurs -Name $Name -CommandLine $ownerInfo.CommandLine -Port $Port -RootPath $Root

        if (-not $looksOurs) {
            # 占着端口但不是本项目形态的进程：既不能复用（它提供不了本项目的接口），
            # 也不能让用户以为“已经起好了”（stop-all.ps1 会把这个端口上的进程收掉）。
            $result.State = 'conflict'
            $result.Message = "端口 $Port 被 pid $owner / $ownerName 占用，命令行不像本项目服务"
            WarnLine "$Name 的端口 $Port 已被 pid $owner / $ownerName 占用，且不像是本项目的服务 —— 跳过启动"
            if ($ownerInfo.CommandLine) {
                $shown = $ownerInfo.CommandLine
                if ($shown.Length -gt 120) { $shown = $shown.Substring(0, 120) + '...' }
                NoteLine "占用者命令行：$shown"
            }
            NoteLine '换端口（.env 的 PY_API_PORT）或用 -Restart 收掉本项目的旧进程后重试'
        } elseif (Test-HttpOk -Url $probeUrl -TimeoutSec 8) {
            $result.State = 'already'
            $result.Message = "端口已被 pid $owner / $ownerName 占用且探活通过，复用，不重复启动"
            OkLine "$Name 已在运行（pid $owner / $ownerName），直接复用 → $Url"
        } else {
            $result.State = 'conflict'
            $result.Message = "端口 $Port 被 pid $owner / $ownerName 占用（是本项目进程），但探活失败"
            WarnLine "$Name 的端口 $Port 已被 pid $owner / $ownerName 占用（是本项目进程），但探活不通 —— 跳过启动"
            NoteLine '这是本项目遗留的旧进程：先执行 .\stop-all.ps1，或加上 -Restart 重跑本脚本'
        }
        return $result
    }

    NoteLine "启动 $Label ..."
    try {
        $handle = Start-ManagedProcess -Name $Name -FilePath $FilePath -Arguments $Arguments `
            -WorkingDirectory $WorkingDirectory -Port $Port -ExtraEnv $ExtraEnv -Url $Url
    } catch {
        $result.State = 'failed'
        $result.Message = "无法创建进程：$($_.Exception.Message)"
        FailLine "$Name 启动失败：$($result.Message)"
        return $result
    }

    $result.Pid = $handle.Process.Id
    $status = Wait-ServiceReady -Port $Port -Proc $handle.Process -HealthUrl $HealthUrl -TimeoutSeconds $TimeoutSeconds

    switch ($status) {
        'ready' {
            $result.State = 'started'
            $result.Message = '已就绪'
            OkLine "$Name 已就绪 → $Url（pid $($handle.Process.Id)）"
        }
        'exited' {
            $result.State = 'failed'
            $result.Message = '进程启动后立即退出'
            FailLine "$Name 启动失败：进程已退出（pid $($handle.Process.Id)）"
            Show-LogTail -Path $handle.Stdout -Lines 15
            Show-LogTail -Path $handle.Stderr -Lines 25
            Remove-Item -LiteralPath $handle.PidFile -Force -ErrorAction SilentlyContinue
        }
        default {
            $result.State = 'failed'
            if ($status -eq 'timeout') {
                $result.Message = "等待端口 $Port 就绪超时（$TimeoutSeconds 秒）"
            } else {
                $result.Message = "端口 $Port 已监听，但 $HealthUrl 一直没返回 200"
            }
            FailLine "$Name：$($result.Message)"
            NoteLine "进程仍在运行（pid $($handle.Process.Id)），日志在上面两段里；可先 .\stop-all.ps1 再排查"
            Show-LogTail -Path $handle.Stdout -Lines 15
            Show-LogTail -Path $handle.Stderr -Lines 25
        }
    }
    return $result
}

# ---------------------------------------------------------------------------
# 4. 主流程
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '  Aragon Research Terminal —— 启动全部本地服务' -ForegroundColor Cyan
Write-Host '  API :8000（uvicorn, workers=1）   Web :3000（next dev）' -ForegroundColor DarkGray

Step '1/4 准备环境'

$envPath = Join-Path $Root '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    $examplePath = Join-Path $Root '.env.example'
    if (-not (Test-Path -LiteralPath $examplePath)) {
        Exit-WithError '缺少 .env，也找不到 .env.example' @('从版本库恢复 .env.example，或手工创建 .env')
    }
    Copy-Item -LiteralPath $examplePath -Destination $envPath
    OkLine '.env 不存在，已从 .env.example 生成'
}
$loadedVars = Import-DotEnv -Path $envPath
OkLine "已加载 .env（$loadedVars 个变量；已存在的同名环境变量保持原值）"

if ($Mock) {
    $env:AGENT_MODE = 'mock'
    WarnLine 'AGENT_MODE=mock：本次不调用 aragon CLI，走离线示例生成'
}

# 端口：API 读 PY_API_PORT，Web 读 web/package.json 的 dev 脚本
$ApiPort = 8000
if ($env:PY_API_PORT) {
    $parsedPort = 0
    if ([int]::TryParse(([string]$env:PY_API_PORT).Trim(), [ref]$parsedPort) -and $parsedPort -gt 0 -and $parsedPort -lt 65536) {
        $ApiPort = $parsedPort
    } else {
        WarnLine "PY_API_PORT=$($env:PY_API_PORT) 不是合法端口，回退 8000"
    }
}
$WebPort = 3000
$webPackagePath = Join-Path $Root 'web\package.json'
if (Test-Path -LiteralPath $webPackagePath) {
    $webPackageRaw = Get-Content -LiteralPath $webPackagePath -Raw -Encoding UTF8
    if ($webPackageRaw -match '"dev"\s*:\s*"[^"]*--port\s+(\d+)') {
        $WebPort = [int]$Matches[1]
    }
}
OkLine "端口：API=$ApiPort  Web=$WebPort"

# 前端拿到的 API 地址来自 NEXT_PUBLIC_API_BASE，脚本不会替它改端口：
# 只改 PY_API_PORT 会让前端继续请求 8000，表现为整站 ERR_CONNECTION_REFUSED。
$declaredBase = [Environment]::GetEnvironmentVariable('NEXT_PUBLIC_API_BASE', 'Process')
if ($declaredBase -and ($declaredBase -notmatch ":$ApiPort(/|\?|#|$)")) {
    WarnLine "NEXT_PUBLIC_API_BASE=$declaredBase 与 PY_API_PORT=$ApiPort 不一致：前端会请求错端口"
    NoteLine '请在 .env 里把两者改成同一个端口，或把 PY_API_PORT 改回'
}

# Node / npm
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    Exit-WithError '没有找到 node' @('安装 Node.js 20 或更高版本（https://nodejs.org），然后重新运行')
}
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) { $npmCommand = Get-Command npm -ErrorAction SilentlyContinue }
if (-not $npmCommand) {
    Exit-WithError '没有找到 npm' @('npm 随 Node.js 一起安装：装好 Node 20+ 后重开终端再试')
}
OkLine "node $(& $nodeCommand.Source --version) / npm $(& $npmCommand.Source --version)"

if (-not (Test-Path -LiteralPath (Join-Path $Root 'web\node_modules'))) {
    Exit-WithError 'web\node_modules 不存在，前端依赖还没装' @(
        '在仓库根执行：npm run setup',
        '或只装前端：cd web; npm install'
    )
}

# Python：优先 api\.venv，退回 PATH 上的 python（与 scripts/dev.mjs 的策略一致）
$venvPython = Join-Path $Root 'api\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $pythonExe = $venvPython
    OkLine 'Python 解释器：api\.venv\Scripts\python.exe'
} else {
    $pathPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pathPython) {
        Exit-WithError '没有找到 Python 解释器' @(
            '安装 Python 3.11+ 并加入 PATH',
            '或执行 npm run setup 创建 api\.venv'
        )
    }
    $pythonExe = $pathPython.Source
    WarnLine "api\.venv 不存在，回退到 PATH 上的 python：$pythonExe"
    WarnLine '建议执行 npm run setup 建立隔离虚拟环境，避免污染全局 Python'
}

$probe = & $pythonExe -c "import uvicorn, fastapi" 2>&1
if ($LASTEXITCODE -ne 0) {
    foreach ($line in ($probe | Select-Object -First 4)) { NoteLine "$line" }
    Exit-WithError '当前 Python 缺少运行 API 的依赖（uvicorn / fastapi）' @(
        '执行 npm run setup（会建 api\.venv 并安装依赖）',
        '或手动：cd api; python -m venv .venv; .\.venv\Scripts\python -m pip install -e .'
    )
}
OkLine "Python $(& $pythonExe --version 2>&1)"

# 数据库：不存在就先跑迁移（schema 唯一真源是 Alembic）
$dataDir = Join-Path $Root 'api\data'
if (-not (Test-Path -LiteralPath $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
}
if (-not (Test-Path -LiteralPath (Join-Path $dataDir 'app.db'))) {
    Step '1b/4 数据库不存在，先执行 alembic upgrade head'
    Push-Location (Join-Path $Root 'api')
    try {
        & $pythonExe -m alembic upgrade head
        $migrateCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($migrateCode -ne 0) {
        Exit-WithError 'alembic upgrade head 失败' @(
            '在 api 目录手动执行：python -m alembic upgrade head，看完整报错',
            '确认 api\alembic.ini 与 api\app\db\migrations 存在'
        )
    }
    OkLine '数据库已初始化（api\data\app.db）'
}

if ($Restart) {
    Step '1c/4 -Restart：先停掉正在运行的服务'
    $stopScript = Join-Path $Root 'stop-all.ps1'
    if (Test-Path -LiteralPath $stopScript) {
        # 用子进程调用，避免 stop-all 的 exit code 直接打断本脚本 —— 但要自己检查它
        & powershell -NoProfile -ExecutionPolicy Bypass -File $stopScript
        $stopCode = $LASTEXITCODE
        if ($stopCode -ne 0) {
            Exit-WithError "stop-all.ps1 退出码 $stopCode：端口可能没释放干净" @(
                '看上面 stop-all 的 [FAIL] 行（通常是权限不足）',
                '用管理员终端重试 .\stop-all.ps1，然后再启动',
                '端口被别人占用时，换端口（.env 的 PY_API_PORT）或先查 Get-NetTCPConnection'
            )
        }
        Start-Sleep -Milliseconds 1500
    } else {
        WarnLine '找不到 stop-all.ps1，跳过'
    }
}

Step '2/4 启动服务（后台运行，日志写 .run\logs）'

$apiArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$ApiPort", '--workers', '1')
if (-not $NoReload) { $apiArgs += '--reload' }

$results = @()
$results += Ensure-Service `
    -Name 'api' `
    -Label 'API（FastAPI / uvicorn）' `
    -Port $ApiPort `
    -FilePath $pythonExe `
    -Arguments $apiArgs `
    -WorkingDirectory (Join-Path $Root 'api') `
    -ExtraEnv @{ } `
    -HealthUrl "http://127.0.0.1:$ApiPort/api/v1/health" `
    -Url "http://127.0.0.1:$ApiPort/docs" `
    -TimeoutSeconds 120

$results += Ensure-Service `
    -Name 'web' `
    -Label 'Web（Next.js dev server）' `
    -Port $WebPort `
    -FilePath $npmCommand.Source `
    -Arguments @('run', 'dev') `
    -WorkingDirectory (Join-Path $Root 'web') `
    -ExtraEnv @{ NODE_ENV = 'development' } `
    -HealthUrl '' `
    -Url "http://localhost:$WebPort" `
    -TimeoutSeconds 180

Step '3/4 汇总'
foreach ($item in $results) {
    switch ($item.State) {
        'started' {
            Write-Host ("    [{0}] 已启动    {1}    pid {2}" -f $item.Name, $item.Url, $item.Pid) -ForegroundColor Green
        }
        'already' {
            Write-Host ("    [{0}] 已在运行  {1}    pid {2}（端口 {3} 已被占用，未重复启动）" -f $item.Name, $item.Url, $item.Pid, $item.Port) -ForegroundColor Yellow
        }
        default {
            Write-Host ("    [{0}] 未就绪    {1}    {2}" -f $item.Name, $item.Url, $item.Message) -ForegroundColor Red
        }
    }
}

Write-Host ''
Write-Host "    日志：$LogDir（uvicorn 的 INFO 日志在 api.err.log，Next 的在 web.out.log）" -ForegroundColor DarkGray
Write-Host '    关闭：powershell -NoProfile -ExecutionPolicy Bypass -File .\stop-all.ps1' -ForegroundColor DarkGray
Write-Host '    重启：.\start-all.ps1 -Restart' -ForegroundColor DarkGray

Step '4/4 提示'
$webReady = $false
foreach ($item in $results) {
    if ($item.Name -eq 'web' -and ($item.State -eq 'started' -or $item.State -eq 'already')) { $webReady = $true }
}
if ($webReady -and -not $NoBrowser) {
    Start-Process "http://localhost:$WebPort" | Out-Null
    NoteLine "已打开浏览器：http://localhost:$WebPort"
} else {
    NoteLine "浏览器入口：http://localhost:$WebPort"
}
NoteLine '首屏 / 首次生成报告时 Next 会按需编译，浏览器要等几秒才出画面，属正常。'

$bad = @($results | Where-Object { $_.State -eq 'failed' -or $_.State -eq 'conflict' })
if ($bad.Count -gt 0) {
    # 一半起来一半没起来是最容易误判的状态：说清哪个还活着，别让人直接 -Restart 把好的那个也推倒
    $alive = @($results | Where-Object { $_.State -eq 'started' -or $_.State -eq 'already' })
    Write-Host ''
    if ($alive.Count -gt 0) {
        $aliveText = ($alive | ForEach-Object { "$($_.Name)(pid $($_.Pid), $($_.Url))" }) -join '、'
        WarnLine "仍在运行：$aliveText"
        NoteLine '只想重试没起来的那个：先 \.\stop-all.ps1 全收，再 .\start-all.ps1；或直接重跑本脚本（已就绪的会被复用）'
    }
    Write-Host '有服务没起来，按上面的 [FAIL]/[WARN] 提示处理后重跑：.\start-all.ps1 -Restart' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '全部服务已就绪。' -ForegroundColor Green
if ($Mock) {
    $reused = @($results | Where-Object { $_.State -eq 'already' })
    if ($reused.Count -gt 0) {
        NoteLine '注意：-Mock 只作用于本次新起的进程；上面被“复用”的服务仍是它们启动时的 AGENT_MODE。'
        NoteLine '想确保示例模式生效：先 .\stop-all.ps1，再 .\start-all.ps1 -Mock。'
    }
}
exit 0
