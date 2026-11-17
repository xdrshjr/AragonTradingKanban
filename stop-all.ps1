#Requires -Version 5.1
<#
.SYNOPSIS
    关闭 Aragon Research Terminal 的全部本地服务（API :8000 + Web :3000）。

.DESCRIPTION
    停止本项目的开发服务，覆盖三种来源，避免“脚本起的停不掉 / 手工起的也停不掉”：

      1. .run\api.pid / .run\web.pid —— start-all.ps1 启动时写下的身份，连子进程树一起结束。
         记录里带 startedAt 与 processName，读回时会核对“这个 PID 还是当初那个进程吗”，
         对不上（典型情形：机器重启后 PID 被系统复用给了 explorer.exe）就跳过并告警；
      2. 端口占用者 —— 监听 API 端口 / Web 端口的进程。手工 `python -m uvicorn`、
         手工 `npm run dev` 起的服务都在这里被认出来。这条是**无条件**的：
         谁占着 :8000 / :3000 就收谁，因为端口本身就属于本项目；
      3. 命令行特征 —— 本项目 `python -m uvicorn app.main:app --port <API 端口>`
         （端口必须显式对得上），以及命令行里带本仓库绝对路径的 `next dev` /
         `next-server` 残留父子进程（`next build`、`next start` 不在范围内）。

    除端口规则外，其余两条都做身份核对，避免误杀同一台机器上其它项目的
    python / node 进程（例如别的仓库同样有 app.main:app 的 uvicorn、
    或者 api\tests\fixtures\fake_aragon.py 这类测试子进程）。

    结束后删除已成功停止的 .run\*.pid，并校验两个端口确实已经释放；
    还有残留则以退出码 1 报警（结束失败的记录会保留，方便下次重试与排查）。

    日志文件保留在 .run\logs\ 里供事后排查，需要清理时直接删掉 .run\ 目录即可。

.PARAMETER DryRun
    只列出会被结束的进程（含判定来源），不真的执行 taskkill。用于确认没有误伤。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\stop-all.ps1

.EXAMPLE
    .\stop-all.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$DryRun
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

# ---------------------------------------------------------------------------
# 1. 探测小工具
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

    $hit = & netstat -ano -p TCP 2>$null |
        Select-String -Pattern "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$" |
        Select-Object -Last 1
    if ($hit -and ($hit.Line -match 'LISTENING\s+(\d+)')) {
        return [int]$Matches[1]
    }
    return 0
}

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

function Get-ProcessSnapshot {
    # 一次性取全部进程信息：既避免逐个 PID 查 WMI（慢、且失败率高），
    # 也让所有判定基于同一时刻的一致视图。
    $snapshot = @{}
    foreach ($cim in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        $procId = [int]$cim.ProcessId
        $snapshot[$procId] = [pscustomobject]@{
            Pid         = $procId
            Name        = if ($cim.Name) { [string]$cim.Name } else { '' }
            CommandLine = if ($cim.CommandLine) { [string]$cim.CommandLine } else { '' }
            StartedAt   = $cim.CreationDate
        }
    }
    return $snapshot
}

function Add-Target {
    param(
        [System.Collections.ArrayList]$List,
        [hashtable]$Snapshot,
        [string]$Label,
        [int]$ProcessId,
        [string]$Source
    )
    if ($ProcessId -le 0) { return }
    foreach ($existing in $List) {
        if ($existing.Pid -eq $ProcessId) { return }
    }
    $procName = '?'
    $cmdLine = ''
    if ($Snapshot.ContainsKey($ProcessId)) {
        $info = $Snapshot[$ProcessId]
        if ($info.Name) { $procName = $info.Name }
        if ($info.CommandLine) { $cmdLine = $info.CommandLine }
    }
    if ($cmdLine.Length -gt 110) { $cmdLine = $cmdLine.Substring(0, 110) + '...' }
    [void]$List.Add([pscustomobject]@{
        Label   = $Label
        Pid     = $ProcessId
        Name    = $procName
        Source  = $Source
        Command = $cmdLine
    })
}

function Test-IsSameProcessGeneration {
    # PID 会被系统复用：机器重启后 .run\*.pid 里的 27188 可能已经是 explorer.exe。
    # 只有“进程名一致”且“启动时间与记录相差不超过 90 秒”才认作当初那个进程；
    # 记录里缺字段（旧版本脚本写的 pid 文件）时退回纯存在性判断并给出提示。
    param(
        [hashtable]$Snapshot,
        [int]$ProcessId,
        [string]$RecordedName,
        [string]$RecordedStartedAt,
        [string]$PidFileName
    )

    if (-not $Snapshot.ContainsKey($ProcessId)) {
        NoteLine "$PidFileName 里的 pid $ProcessId 已经不在了（陈旧记录）"
        return $false
    }
    $info = $Snapshot[$ProcessId]

    if ($RecordedName -and $info.Name -and ($RecordedName -ne $info.Name)) {
        # 归一化再比：Get-Process 给的是 cmd，WMI 给的是 cmd.exe
        $a = $RecordedName.ToLower() -replace '\.exe$', ''
        $b = $info.Name.ToLower() -replace '\.exe$', ''
        if ($a -ne $b) {
            WarnLine "$PidFileName 里的 pid $ProcessId 现在是 $($info.Name)，不是当初的 $RecordedName —— PID 已被复用，跳过"
            return $false
        }
    }

    $recorded = [datetime]::MinValue
    if ($RecordedStartedAt) {
        $parsed = [datetime]::MinValue
        if ([datetime]::TryParse($RecordedStartedAt, [ref]$parsed)) { $recorded = $parsed }
    }
    if ($recorded -ne [datetime]::MinValue -and $info.StartedAt) {
        $delta = [math]::Abs(($info.StartedAt - $recorded).TotalSeconds)
        if ($delta -gt 90) {
            WarnLine ("$PidFileName 里的 pid $ProcessId 启动时间是 {0:yyyy-MM-dd HH:mm:ss}，与记录的 {1} 不符 —— PID 已被复用，跳过" -f $info.StartedAt, $RecordedStartedAt)
            return $false
        }
    }
    return $true
}

# ---------------------------------------------------------------------------
# 2. 主流程
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '  Aragon Research Terminal —— 关闭全部本地服务' -ForegroundColor Cyan
if ($DryRun) {
    Write-Host '  （-DryRun：只列出目标，不会真的结束进程）' -ForegroundColor Yellow
}

Step '1/3 读取配置，定位要结束的进程'

# 端口：API 读 PY_API_PORT（.env），Web 读 web/package.json 的 dev 脚本
$envPath = Join-Path $Root '.env'
$loadedVars = Import-DotEnv -Path $envPath
$ApiPort = 8000
if ($env:PY_API_PORT) {
    $parsedPort = 0
    if ([int]::TryParse(([string]$env:PY_API_PORT).Trim(), [ref]$parsedPort) -and $parsedPort -gt 0 -and $parsedPort -lt 65536) {
        $ApiPort = $parsedPort
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
NoteLine "工作目录：$Root"
NoteLine "目标端口：API=$ApiPort  Web=$WebPort（.env 载入 $loadedVars 个变量）"

$targets = New-Object System.Collections.ArrayList
$snapshot = Get-ProcessSnapshot

# (1) start-all.ps1 写下的 PID 文件（含身份核对：进程名 + 启动时间）
$pidFiles = @()
if (Test-Path -LiteralPath $RunDir) {
    $pidFiles = @(Get-ChildItem -LiteralPath $RunDir -Filter '*.pid' -ErrorAction SilentlyContinue)
    foreach ($pidFile in $pidFiles) {
        try {
            $meta = (Get-Content -LiteralPath $pidFile.FullName -Raw -Encoding UTF8) | ConvertFrom-Json
            $savedPid = [int]$meta.pid
            $savedName = [string]$meta.name
            $recordedName = ''
            if ($meta.PSObject.Properties.Name -contains 'processName') { $recordedName = [string]$meta.processName }
            $recordedStartedAt = ''
            if ($meta.PSObject.Properties.Name -contains 'startedAt') { $recordedStartedAt = [string]$meta.startedAt }

            $sameGeneration = Test-IsSameProcessGeneration -Snapshot $snapshot -ProcessId $savedPid `
                -RecordedName $recordedName -RecordedStartedAt $recordedStartedAt -PidFileName $pidFile.Name
            if ($sameGeneration) {
                Add-Target -List $targets -Snapshot $snapshot -Label $savedName -ProcessId $savedPid -Source "PID 文件 .run\$($pidFile.Name)"
            }
        } catch {
            WarnLine "无法解析 $($pidFile.Name)：$($_.Exception.Message)"
        }
    }
}

# (2) 端口占用者：手工起的服务靠这条兜底。这一条不做身份判断 —— 端口本身就是本项目的
foreach ($servicePort in @(@{ Label = 'api'; Port = $ApiPort }, @{ Label = 'web'; Port = $WebPort })) {
    $owner = Get-PortOwner -Port $servicePort.Port
    if ($owner -gt 0) {
        Add-Target -List $targets -Snapshot $snapshot -Label $servicePort.Label -ProcessId $owner -Source "监听端口 $($servicePort.Port)"
    }
}

# (3) 命令行特征：抓父子链上的残留（每条都要求能定位到本仓库/本项目端口）
$rootPattern = [regex]::Escape($Root)
foreach ($procEntry in @($snapshot.Values)) {
    if (-not $procEntry.CommandLine) { continue }
    $cmdLine = $procEntry.CommandLine
    $procName = $procEntry.Name

    if ($procName -match '^python(w)?\.exe$') {
        # 必须同时满足：本项目入口模块 + 端口显式等于 API 端口。
        # 缺 --port 的 uvicorn 默认就是 8000，交给上面的端口规则收；
        # “没写端口就算本项目”会打到别的仓库同样叫 app.main:app 的服务。
        if ($cmdLine -match '-m\s+uvicorn\s+app\.main:app') {
            $cmdPort = 0
            if ($cmdLine -match '--port[=\s]+(\d+)') { $cmdPort = [int]$Matches[1] }
            if ($cmdPort -eq $ApiPort) {
                Add-Target -List $targets -Snapshot $snapshot -Label 'api' -ProcessId $procEntry.Pid -Source "命令行（uvicorn app.main:app --port $ApiPort）"
            }
        }
    } elseif ($procName -match '^node\.exe$') {
        # next dev 及其 server 子进程的命令行里一定带本仓库绝对路径：
        #   node "<root>\web\node_modules\...\next\dist\bin\next" dev --port 3000
        #   node "<root>\web\node_modules\next\dist\server\lib\start-server.js"
        # 只认这两种形态：next build / next start 不算（它们跑完或属于别的用途）。
        if ($cmdLine -match $rootPattern) {
            $isNextDev = $cmdLine -match 'next\\dist\\bin\\next"?\s+dev\b'
            $isNextServer = $cmdLine -match 'next\\dist\\server\\lib\\(start-server|next-dev)\.js'
            if ($isNextDev -or $isNextServer) {
                Add-Target -List $targets -Snapshot $snapshot -Label 'web' -ProcessId $procEntry.Pid -Source '命令行（next dev / start-server）'
            }
        }
    }
}

if ($targets.Count -eq 0) {
    OkLine '没有发现正在运行的服务（两个端口都空闲，也没有 PID 记录）'
} else {
    NoteLine "命中 $($targets.Count) 个进程："
    foreach ($target in $targets) {
        NoteLine ("  [{0}] pid {1,-6} {2,-12} {3}" -f $target.Label, $target.Pid, $target.Name, $target.Source)
    }
}

# ---------------------------------------------------------------------------
# 3. 结束进程
# ---------------------------------------------------------------------------
$stopped = New-Object System.Collections.ArrayList
$failed = New-Object System.Collections.ArrayList

if ($targets.Count -gt 0 -and $DryRun) {
    Step '2/3 结束进程'
    NoteLine '-DryRun：以上进程一个都没结束。去掉 -DryRun 再跑才会真的关。'
    Write-Host ''
    Write-Host '-DryRun 结束：确认清单无误后执行 .\stop-all.ps1' -ForegroundColor Yellow
    exit 0
}

if ($targets.Count -gt 0) {
    Step '2/3 结束进程（含子进程树 taskkill /T）'
    foreach ($target in $targets) {
        # 用 cmd 包一层：避免原生命令的 stderr 被 $ErrorActionPreference 当成终止错误
        & cmd /c "taskkill /PID $($target.Pid) /T /F >nul 2>&1"
        $killCode = $LASTEXITCODE
        if ($killCode -ne 0) {
            # taskkill 返回非 0 常见于“进程刚好自己退了”（128），用存活状态做终裁
            $stillThere = Get-Process -Id $target.Pid -ErrorAction SilentlyContinue
            if ($null -eq $stillThere) {
                $killCode = 0
            } else {
                try {
                    Stop-Process -Id $target.Pid -Force -ErrorAction Stop
                    $killCode = 0
                } catch {
                    $killCode = 1
                }
            }
        }

        if ($killCode -eq 0) {
            [void]$stopped.Add($target)
            OkLine ("已结束 [{0}] pid {1} ({2})" -f $target.Label, $target.Pid, $target.Name)
        } else {
            [void]$failed.Add($target)
            FailLine ("结束失败 [{0}] pid {1} ({2}) —— 可能需要管理员权限" -f $target.Label, $target.Pid, $target.Name)
        }
    }
} else {
    Step '2/3 结束进程'
    NoteLine '无需操作。'
}

# 清理 PID 记录：已结束或已消失的删掉；结束失败的保留，下次还能按记录重试
if ($pidFiles.Count -gt 0) {
    foreach ($pidFile in $pidFiles) {
        $keep = $false
        try {
            $savedPid = [int](((Get-Content -LiteralPath $pidFile.FullName -Raw -Encoding UTF8) | ConvertFrom-Json).pid)
            foreach ($failedTarget in $failed) {
                if ($failedTarget.Pid -eq $savedPid) { $keep = $true }
            }
        } catch {
            $keep = $false
        }
        if ($keep) {
            NoteLine "保留 $($pidFile.Name)：对应进程没结束成功，便于下次重试"
        } else {
            Remove-Item -LiteralPath $pidFile.FullName -Force -ErrorAction SilentlyContinue
        }
    }
}

# ---------------------------------------------------------------------------
# 4. 校验端口
# ---------------------------------------------------------------------------
Step '3/3 校验端口'

$deadline = (Get-Date).AddSeconds(15)
while ($true) {
    $apiOwner = Get-PortOwner -Port $ApiPort
    $webOwner = Get-PortOwner -Port $WebPort
    if (($apiOwner -eq 0 -and $webOwner -eq 0) -or ((Get-Date) -ge $deadline)) { break }
    Start-Sleep -Milliseconds 500
}

$busy = @()
if ($apiOwner -ne 0) { $busy += [pscustomobject]@{ Label = 'api'; Port = $ApiPort; Pid = $apiOwner } }
if ($webOwner -ne 0) { $busy += [pscustomobject]@{ Label = 'web'; Port = $WebPort; Pid = $webOwner } }

Write-Host ''
foreach ($entry in @(@{ Label = 'api'; Port = $ApiPort }, @{ Label = 'web'; Port = $WebPort })) {
    $stillBusy = @($busy | Where-Object { $_.Port -eq $entry.Port })
    if ($stillBusy.Count -eq 0) {
        Write-Host ("    [{0}] 端口 {1} 已释放" -f $entry.Label, $entry.Port) -ForegroundColor Green
    } else {
        Write-Host ("    [{0}] 端口 {1} 仍被 pid {2} 占用" -f $entry.Label, $entry.Port, $stillBusy[0].Pid) -ForegroundColor Red
    }
}

Write-Host ''
Write-Host ("    共结束 {0} 个进程，失败 {1} 个。" -f $stopped.Count, $failed.Count) -ForegroundColor DarkGray
if (Test-Path -LiteralPath $LogDir) {
    Write-Host "    日志保留在 $LogDir（需要清理时直接删 .run\ 目录）" -ForegroundColor DarkGray
}

if ($busy.Count -gt 0) {
    Write-Host ''
    Write-Host '端口没释放干净：可能需要以管理员身份运行，或进程被其它账户持有。' -ForegroundColor Red
    Write-Host "    排查：Get-NetTCPConnection -LocalPort $ApiPort,$WebPort -State Listen" -ForegroundColor Yellow
    exit 1
}

Write-Host ''
Write-Host '全部服务已停止。' -ForegroundColor Green
exit 0
