<#
Управление панелью hh-applicant-tool в Docker на Windows.

  hh-panel.ps1 start     включить (поднимет Docker Desktop, если он не запущен)
  hh-panel.ps1 stop      выключить: контейнеры останавливаются и не стартуют
                         сами после перезагрузки, пока не скажете start
  hh-panel.ps1 restart   перезапустить
  hh-panel.ps1 status    что запущено и по какому адресу открывать
  hh-panel.ps1 logs      лог панели в реальном времени (Ctrl+C, чтобы выйти)
  hh-panel.ps1 update    пересобрать из src (после замены исходников)

Настройки лежат в <InstallDir>\hh.env (создаёт setup-docker.ps1).
#>
param(
    [ValidateSet("start", "stop", "restart", "status", "logs", "update")]
    [string]$Command = "status",
    [string]$InstallDir = "D:\hh-applicant-tool"
)

# Continue, а не Stop: Docker пишет прогресс в stderr, и PowerShell 5 при
# перенаправлении вывода (лог, планировщик) считал бы это ошибкой и
# обрывал скрипт. Ошибки проверяем явно по $LASTEXITCODE
$ErrorActionPreference = "Continue"

$Src = Join-Path $InstallDir "src"
$EnvFile = Join-Path $InstallDir "hh.env"
$Compose = @("compose", "-f", (Join-Path $Src "docker-compose.pc.yml"), "--env-file", $EnvFile)

function Say($text, $color = "Cyan") { Write-Host $text -ForegroundColor $color }

function Find-Docker {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in "D:\Docker\resources\bin\docker.exe", "C:\Program Files\Docker\Docker\resources\bin\docker.exe") {
        if (Test-Path $p) { return $p }
    }
    throw "Docker не найден. Установите Docker Desktop."
}

$Docker = Find-Docker

function Wait-Docker {
    & $Docker info *> $null
    if ($LASTEXITCODE -eq 0) { return }
    $desktop = @("D:\Docker\Docker Desktop.exe", "C:\Program Files\Docker\Docker\Docker Desktop.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $desktop) { throw "Не нашёл Docker Desktop.exe" }
    Say "Запускаю Docker Desktop…"
    Start-Process $desktop
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        & $Docker info *> $null
        if ($LASTEXITCODE -eq 0) { Say "Docker готов"; return }
    }
    throw "Docker Desktop не запустился за 3 минуты"
}

function Wait-Panel {
    # Панели нужно несколько секунд после старта контейнера
    $port = (Select-String -Path $EnvFile -Pattern '^HH_PANEL_PORT=(\d+)' -ErrorAction SilentlyContinue).Matches.Groups[1].Value
    if (-not $port) { $port = 8090 }
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $c = New-Object Net.Sockets.TcpClient
            $c.Connect("127.0.0.1", [int]$port); $c.Close()
            Start-Sleep -Seconds 2
            return
        } catch { Start-Sleep -Seconds 1 }
    }
    Write-Warning "Панель не ответила за 30 секунд: посмотрите лог (hh-panel.ps1 logs)"
}

function Show-Status {
    & $Docker @Compose ps --format "table {{.Service}}\t{{.State}}\t{{.Status}}"
    $port = (Select-String -Path $EnvFile -Pattern '^HH_PANEL_PORT=(\d+)' -ErrorAction SilentlyContinue).Matches.Groups[1].Value
    if (-not $port) { $port = 8090 }
    $hosts = (Select-String -Path $EnvFile -Pattern '^HH_PANEL_PUBLIC_HOSTS=(.*)$' -ErrorAction SilentlyContinue).Matches.Groups[1].Value
    Say ""
    Say "Панель: https://localhost:$port" "Green"
    foreach ($h in ($hosts -split ",")) { if ($h) { Say "С ноутбука: https://${h}:$port" "Green" } }
}

switch ($Command) {
    "start" {
        Wait-Docker
        & $Docker @Compose up -d
        if ($LASTEXITCODE -ne 0) { throw "Контейнеры не запустились, подробности выше" }
        Wait-Panel
        Show-Status
    }
    "stop" {
        & $Docker @Compose stop
        Say "Панель выключена. Включить: hh-panel.ps1 start" "Yellow"
    }
    "restart" {
        Wait-Docker
        & $Docker @Compose restart
        Wait-Panel
        Show-Status
    }
    "status" {
        & $Docker info *> $null
        if ($LASTEXITCODE -ne 0) { Say "Docker не запущен: панель выключена" "Yellow"; return }
        Show-Status
    }
    "logs" {
        & $Docker @Compose logs -f --tail 100 panel
    }
    "update" {
        Wait-Docker
        & $Docker @Compose up -d --build
        Wait-Panel
        Show-Status
    }
}
