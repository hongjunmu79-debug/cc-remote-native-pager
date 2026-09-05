<# Background operations for the desktop window; stdout is a secret-free report. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot,
      [ValidateSet('start','restart','diagnose','firewall')][string]$Action = 'diagnose')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$python = Join-Path $root 'runtime\.venv\Scripts\python.exe'
$config = Join-Path $root 'config\.env'
$port = 8765
if (Test-Path -LiteralPath $config) {
    foreach ($line in [IO.File]::ReadAllLines($config)) {
        if ($line -match '^\s*RELAY_PORT\s*=\s*(\d+)\s*$') { $port = [int]$matches[1] }
    }
}
try {
    if ($Action -in @('start','restart')) {
        if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $config)) {
            throw '安装不完整。请重新运行安装包并选择同一目录；已有配置会保留。'
        }
        if ($Action -eq 'restart') {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stop.ps1') -InstallRoot $root | Out-Null
            if ($LASTEXITCODE -ne 0) { throw '停止服务失败。请查看日志后重试。' }
        }
        $missing = $false
        foreach ($name in @('cc-remote-relay','cc-remote-wrapper')) {
            $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            if (-not $task) { $missing = $true }
            elseif (-not (($task.Actions.Arguments -join ' ').Contains('"' + $root + '"'))) {
                throw '检测到另一目录的 CC Remote 服务。请先卸载旧目录的版本，再运行本安装包。'
            }
        }
        if ($missing) {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'register-tasks.ps1') -InstallRoot $root | Out-Null
            if ($LASTEXITCODE -ne 0) { throw '无法注册启动服务。请重新运行安装包。' }
        } else {
            foreach ($name in @('cc-remote-relay','cc-remote-wrapper')) {
                Enable-ScheduledTask -TaskName $name | Out-Null
                Start-ScheduledTask -TaskName $name
            }
        }
        Write-Output '已发送启动请求。上方状态会自动更新，变为「已就绪」后即可扫码。'
    } elseif ($Action -eq 'firewall') {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'firewall.ps1') -InstallRoot $root -Port $port | Out-Null
        if ($LASTEXITCODE -ne 0) { throw '防火墙规则未能修复。请接受 Windows 的管理员授权提示后重试。' }
        Write-Output '已修复本机私有局域网访问规则。请确认手机和电脑连接同一 Wi-Fi，再重新扫码。'
    }
    Write-Output ("检查时间：{0}`r`n安装目录：{1}`r`n本机网页：http://127.0.0.1:{2}/" -f (Get-Date),$root,$port)
    Write-Output ("运行环境：{0}；配置文件：{1}" -f (Test-Path -LiteralPath $python),(Test-Path -LiteralPath $config))
    foreach ($name in @('cc-remote-relay','cc-remote-wrapper')) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($task) { Write-Output "$name : $($task.State)" } else { Write-Output "$name : 未注册，请点「启动 / 修复连接」。" }
    }
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object Net.Http.HttpClientHandler
    $handler.UseProxy = $false
    $client = New-Object Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(2)
    try {
        $health = $client.GetStringAsync("http://127.0.0.1:$port/healthz").GetAwaiter().GetResult() | ConvertFrom-Json
        Write-Output "网页服务：正常；电脑服务：$($health.wrapper_connected)；在线客户端：$($health.clients)"
        if (-not $health.wrapper_connected) { Write-Output '电脑服务未连接。请点「启动 / 修复连接」，仍失败时查看 wrapper 日志。' }
    } catch { Write-Output '网页服务未就绪。请点「启动 / 修复连接」；持续失败时查看 relay 日志。' }
    finally { $client.Dispose(); $handler.Dispose() }
    foreach ($profile in @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)) {
        $adapter = Get-NetAdapter -InterfaceIndex $profile.InterfaceIndex -ErrorAction SilentlyContinue
        if ($adapter -and -not $adapter.HardwareInterface) { continue }
        Write-Output "网络：$($profile.Name) [$($profile.NetworkCategory)]"
        if ($profile.NetworkCategory -eq 'Public') { Write-Output '公共网络默认不开放远程控制。仅对你信任的 Wi-Fi，在 Windows 网络设置中改为「专用网络」。' }
    }
    foreach ($address in @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.|198\.(18|19)\.)' })) {
        Write-Output "本机地址：$($address.InterfaceAlias) $($address.IPAddress)"
    }
    Write-Output '手机仍连不上：确认同一 Wi-Fi，点「修复局域网访问」。虚拟机可尝试桥接网络；访客 Wi-Fi 的设备隔离需要由网络管理员关闭。'
} catch {
    Write-Output $_.Exception.Message
    exit 1
}
