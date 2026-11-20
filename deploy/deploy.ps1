<#
.SYNOPSIS
    一键发版：本地构建 → 打包 → 上传 → 远程落盘重启 → 冒烟。

.DESCRIPTION
    Aragon Trading Kanban 生产部署到 47.82.166.177 / aragontrading.online。

    两个要点决定了脚本长这样：
      1. NEXT_PUBLIC_API_BASE 是**构建期内联**的 —— 必须在本机带正确值构建，
         否则浏览器包里还是 http://127.0.0.1:8000，线上接口全红。
      2. 服务器 2 vCPU / 3.4 GiB，next build 会 OOM —— 所以前端在本机构建，
         只上传 standalone 产物。

.PARAMETER SkipBuild      复用本机已有的 web/.next/standalone（不重新构建）
.PARAMETER SkipWeb        只发后端
.PARAMETER SkipSrc        只发前端产物
.PARAMETER Wait           remote 跑完后等待秒数再冒烟（默认 0）

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\deploy.ps1

.EXAMPLE
    .\deploy\deploy.ps1 -SkipWeb          # 只更后端
#>
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$SkipWeb,
    [switch]$SkipSrc,
    [string]$KeyPath = (Join-Path $env:USERPROFILE '.ssh\inkclaw.pem'),
    [string]$SshHost = 'root@47.82.166.177',
    [string]$Domain  = 'aragontrading.online',
    [int]$Wait = 0
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

if (-not (Test-Path $KeyPath)) { throw "SSH 私钥不存在: $KeyPath" }
$sshOpts = @('-i', $KeyPath, '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=20')
$tmp = $env:TEMP

# ── 0. 连通性 ───────────────────────────────────────────────────────────────
Step '检查 SSH 连通性'
& ssh @sshOpts $SshHost 'echo SSH_OK; hostname; df -h / | tail -1'
if ($LASTEXITCODE -ne 0) { throw "SSH 连接失败 ($SshHost)" }
Ok 'SSH ok'

# ── 1. 源码包 ───────────────────────────────────────────────────────────────
if (-not $SkipSrc) {
    Step '打包源码（排除 node_modules / .venv / .next / .git）'
    $srcTar = Join-Path $tmp 'aragon-src.tar.gz'
    if (Test-Path $srcTar) { Remove-Item $srcTar -Force }
    $excludes = @(
        '--exclude=node_modules', '--exclude=.venv', '--exclude=.next', '--exclude=.git',
        '--exclude=.agentmesh', '--exclude=test-results', '--exclude=playwright-report',
        '--exclude=__pycache__', '--exclude=.pytest_cache', '--exclude=.ruff_cache',
        '--exclude=*.pyc', '--exclude=tsconfig.tsbuildinfo', '--exclude=.run',
        '--exclude=coverage', '--exclude=web-dist', '--exclude=.backups',
        '--exclude=.deploy-backups',
        # ★ 运行态数据绝不能进源码包：api/data 是本机开发库，打进去会把生产库**覆盖**。
        #   （实测踩过：一次发版把生产 2 次真实生成换成了本机 18 条 run。）
        #   api/workspace 是生成快照与审计日志，同理只属于那一台机器。
        '--exclude=api/data', '--exclude=api/workspace',
        '--exclude=api/.venv', '--exclude=web-dist', '--exclude=.aragon-home'
    )
    & tar -czf $srcTar @excludes .
    if ($LASTEXITCODE -ne 0) { throw '打包源码失败' }
    $mb = [math]::Round((Get-Item $srcTar).Length / 1MB, 2)
    Ok "aragon-src.tar.gz ($mb MB)"
}

# ── 2. 前端构建 + 产物包 ────────────────────────────────────────────────────
if (-not $SkipWeb) {
    if (-not $SkipBuild) {
        Step "构建前端 (NEXT_PUBLIC_API_BASE=https://$Domain)"
        # 必须清空 .next：Next 会把上一轮的 hashed chunk 留在 static/chunks 里，
        # 而 app-build-manifest 仍可能引用它 —— 于是产物里混进**上一次构建**的代码。
        # 实测踩到：首轮构建没带 NEXT_PUBLIC_API_BASE，清了 .next 之前那条
        # `127.0.0.1:8000` 的旧 chunk 一直跟着产物走。
        if (Test-Path 'web\.next') { Remove-Item 'web\.next' -Recurse -Force }
        Push-Location web
        try {
            $env:NEXT_PUBLIC_API_BASE = "https://$Domain"
            $env:NEXT_TELEMETRY_DISABLED = '1'
            # 必须走 npm.cmd，不能用 `& npm`：Node 装的 npm.ps1 在 "-Command" 分支里
            # 用 `$MyInvocation.Statement.Substring(InvocationName.Length)` 截参数，
            # 配 `& npm run build` 会截成 `pm run build`，报 “Unknown command: pm”。
            $npmExe = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
            if (-not $npmExe) { $npmExe = 'npm.cmd' }
            & $npmExe run build
            if ($LASTEXITCODE -ne 0) { throw 'next build 失败' }
        } finally {
            Remove-Item Env:\NEXT_PUBLIC_API_BASE -ErrorAction SilentlyContinue
            Remove-Item Env:\NEXT_TELEMETRY_DISABLED -ErrorAction SilentlyContinue
            Pop-Location
        }
        Ok 'build ok (clean)'
    }

    Step '组装 standalone 产物（补 .next/static 与 public）'
    $standalone = 'web\.next\standalone'
    if (-not (Test-Path (Join-Path $standalone 'server.js'))) {
        throw "缺少 $standalone\server.js —— 先跑一次 npm run build"
    }
    $stage = Join-Path $tmp 'webdist'
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    Copy-Item -Recurse -Force (Join-Path $standalone '*') $stage
    New-Item -ItemType Directory -Force -Path (Join-Path $stage '.next\static') | Out-Null
    Copy-Item -Recurse -Force 'web\.next\static\*' (Join-Path $stage '.next\static')
    if (Test-Path 'web\public') { Copy-Item -Recurse -Force 'web\public' $stage }

    # 关键校验：烘焙进去的必须是公网 origin，不能是回环地址
    $leak = Select-String -Path (Join-Path $stage '.next\static\chunks\*.js') `
                          -Pattern '127\.0\.0\.1:8000|localhost:8000' -List -ErrorAction SilentlyContinue
    if ($leak) {
        Warn "产物里出现回环 API 地址，重新构建把：`n      $($leak[0].Path)"
        throw 'NEXT_PUBLIC_API_BASE 未生效'
    }
    $pubHit = Select-String -Path (Join-Path $stage '.next\static\chunks\*.js') `
                            -Pattern ([regex]::Escape($Domain)) -List -ErrorAction SilentlyContinue
    if (-not $pubHit) {
        Warn "产物里没找到 $Domain —— 构建可能没带 NEXT_PUBLIC_API_BASE"
        throw 'NEXT_PUBLIC_API_BASE 未内联'
    }
    Ok "烘焙校验通过: $Domain 已内联，无回环地址"

    $webTar = Join-Path $tmp 'aragon-web.tar.gz'
    if (Test-Path $webTar) { Remove-Item $webTar -Force }
    Push-Location $stage
    try { & tar -czf $webTar . } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw '打包前端产物失败' }
    Ok ("aragon-web.tar.gz ({0} MB)" -f [math]::Round((Get-Item $webTar).Length / 1MB, 2))
}

# ── 3. 上传 ─────────────────────────────────────────────────────────────────
Step '上传到 /root/deploy-tmp'
& ssh @sshOpts $SshHost 'mkdir -p /root/deploy-tmp'
if (-not $SkipSrc) {
    & scp -i $KeyPath -o IdentitiesOnly=yes (Join-Path $tmp 'aragon-src.tar.gz') "${SshHost}:/root/deploy-tmp/aragon-src.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw '上传源码包失败' }
    Ok 'src uploaded'
}
if (-not $SkipWeb) {
    & scp -i $KeyPath -o IdentitiesOnly=yes (Join-Path $tmp 'aragon-web.tar.gz') "${SshHost}:/root/deploy-tmp/aragon-web.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw '上传前端包失败' }
    Ok 'web uploaded'
}

# ── 4. 远程落盘 ─────────────────────────────────────────────────────────────
Step '远程执行 ops/deploy.sh'
$flags = @()
if ($SkipSrc) { $flags += '--no-src' }
if ($SkipWeb) { $flags += '--no-web' }
$flagStr = ($flags -join ' ')
& ssh @sshOpts $SshHost "bash /opt/aragon-trading/ops/deploy.sh $flagStr"
if ($LASTEXITCODE -ne 0) { throw '远程部署失败（看上面输出）' }

if ($Wait -gt 0) { Start-Sleep -Seconds $Wait }

# ── 5. 本机视角验收 ─────────────────────────────────────────────────────────
Step "本机验收 https://$Domain"
try {
    $r = Invoke-WebRequest -Uri "https://$Domain/" -UseBasicParsing -TimeoutSec 20
    Ok "首页 $($r.StatusCode) ($([math]::Round($r.RawContentLength/1KB,1)) KB)"
    $h = Invoke-RestMethod -Uri "https://$Domain/api/v1/health" -TimeoutSec 20
    Ok "health status=$($h.status) db=$($h.db)"
    $c = Invoke-RestMethod -Uri "https://$Domain/api/v1/capabilities" -TimeoutSec 30
    Ok "agentMode=$($c.agentMode) canStructured=$($c.aragon.canStructured) degraded=$($c.aragon.degradedReason)"
    if ($r.Content -notmatch 'Aragon Research Terminal') { Warn '页面标题没匹配到，检查 web 产物' }
} catch {
    throw "验收失败: $($_.Exception.Message)"
}

Step '完成'
Write-Host "    https://$Domain" -ForegroundColor Green
Write-Host "    运维手册: /opt/aragon-trading/ops/README.md" -ForegroundColor Gray
