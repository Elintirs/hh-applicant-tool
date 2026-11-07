<#
Перевод панели hh-applicant-tool на Docker Desktop (Windows). Запускается
один раз после установки Docker Desktop.

Что делает:
  1. пишет настройки в <InstallDir>\hh.env (папка конфига, порт, адреса ПК,
     часовой пояс);
  2. останавливает установку без Docker (задача планировщика, Ollama для
     Windows), чтобы не было двух панелей и двух Ollama на одной видеокарте;
  3. собирает образ и переносит уже скачанные модели Ollama в том Docker,
     чтобы не качать гигабайты заново;
  4. открывает порт панели только для локальных сетей;
  5. кладёт на рабочий стол ярлыки «включить», «выключить», «лог»;
  6. включает автозапуск Docker Desktop при входе в Windows.

Конфиг, вход в hh.ru и токен панели остаются в <InstallDir>\config.

Запускайте в обычной сессии Windows (не по ssh): сборке образа нужен
доступ к хранилищу учётных данных, которого у ssh-сессии нет.

  powershell -ExecutionPolicy Bypass -File scripts\windows\setup-docker.ps1
#>
param(
    [string]$InstallDir = "D:\hh-applicant-tool",
    [int]$Port = 8090,
    [string]$TimeZone = "",
    [switch]$KeepNativeOllama
)

# Continue, а не Stop: Docker пишет прогресс в stderr, и PowerShell 5 при
# перенаправлении вывода (лог, планировщик) считал бы это ошибкой и
# обрывал скрипт. Ошибки проверяем явно по $LASTEXITCODE
$ErrorActionPreference = "Continue"

$Src = Join-Path $InstallDir "src"
$Config = Join-Path $InstallDir "config"
$EnvFile = Join-Path $InstallDir "hh.env"
$Ctl = Join-Path $Src "scripts\windows\hh-panel.ps1"
$NativeModels = Join-Path $InstallDir "ollama-models"

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

# ---- 1. Настройки ---------------------------------------------------------
if (-not $TimeZone) {
    # PowerShell 5 не умеет переводить зоны Windows в IANA, берём частые
    $map = @{
        "Kaliningrad Standard Time" = "Europe/Kaliningrad"; "Russian Standard Time" = "Europe/Moscow"
        "Russia Time Zone 3" = "Europe/Samara"; "Ekaterinburg Standard Time" = "Asia/Yekaterinburg"
        "N. Central Asia Standard Time" = "Asia/Novosibirsk"; "North Asia Standard Time" = "Asia/Krasnoyarsk"
        "FLE Standard Time" = "Europe/Kyiv"; "Belarus Standard Time" = "Europe/Minsk"
    }
    $TimeZone = $map[(Get-TimeZone).Id]
    if (-not $TimeZone) { $TimeZone = "Europe/Moscow" }
}
$lan = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.PrefixOrigin -in @("Dhcp", "Manual") -and $_.InterfaceAlias -notlike "vEthernet*" -and $_.IPAddress -match '^(192\.168|10\.|172\.(1[6-9]|2\d|3[01]))' } |
    Select-Object -ExpandProperty IPAddress
New-Item -ItemType Directory -Force -Path $Config | Out-Null
@(
    "HH_CONFIG_PATH=$($Config -replace '\\','/')"
    "HH_PANEL_PORT=$Port"
    "HH_PANEL_PUBLIC_HOSTS=$($lan -join ',')"
    "TZ=$TimeZone"
) | Set-Content -Encoding ASCII $EnvFile
Step "Настройки записаны в $EnvFile (часовой пояс $TimeZone)"

# ---- 2. Останавливаем установку без Docker --------------------------------
$task = "hh-applicant-tool panel"
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $task -Confirm:$false
    Step "Старый автозапуск панели убран"
}
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*run-panel.ps1*" -or $_.CommandLine -like "*hh_applicant_tool panel*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (-not $KeepNativeOllama) {
    Get-Process -Name "ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget uninstall -e --id Ollama.Ollama --silent --disable-interactivity *> $null
    }
    Step "Ollama для Windows остановлена и удалена: теперь она живёт в контейнере"
}

# Конфиг утилиты указывал на Ollama на этом же ПК, в контейнере она по имени
$toml = Join-Path $Config "config.toml"
if (Test-Path $toml) {
    $text = [IO.File]::ReadAllText($toml)
    $new = $text -replace 'http://(127\.0\.0\.1|localhost):11434', 'http://ollama:11434'
    if ($new -ne $text) {
        Copy-Item $toml "$toml.bak-docker"
        [IO.File]::WriteAllText($toml, $new, (New-Object Text.UTF8Encoding $false))
        Step "Адрес Ollama в config.toml заменён на http://ollama:11434"
    }
}

# ---- 3. Образ и модели ------------------------------------------------------
& powershell -NoProfile -ExecutionPolicy Bypass -File $Ctl stop -InstallDir $InstallDir 2>$null
$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $docker) { $docker = "D:\Docker\resources\bin\docker.exe" }
Step "Собираю образ панели (первый раз несколько минут)"
& $docker compose -f (Join-Path $Src "docker-compose.pc.yml") --env-file $EnvFile build
if ($LASTEXITCODE -ne 0) { throw "Сборка образа не удалась" }

if (Test-Path (Join-Path $NativeModels "manifests")) {
    Step "Переношу скачанные модели в том Docker"
    & $docker volume create hh_ollama | Out-Null
    & $docker run --rm -v "hh_ollama:/root/.ollama" -v "${NativeModels}:/src:ro" alpine sh -c "mkdir -p /root/.ollama/models && cp -a /src/. /root/.ollama/models/"
    if ($LASTEXITCODE -eq 0) { Step "Модели перенесены. Папку $NativeModels можно удалить" }
    else { Write-Warning "Модели не перенеслись, их можно скачать заново в панели" }
}

# ---- 4. Брандмауэр: только локальные сети -----------------------------------
$rule = "hh-applicant-tool panel $Port"
Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
# Профиль любой (домашний Wi-Fi часто помечен как общественная сеть), но
# пускаем только из локальных подсетей: дом и ZeroTier
New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Any -RemoteAddress LocalSubnet | Out-Null
Step "Порт $Port открыт для локальных сетей"

# ---- 5. Ярлыки ------------------------------------------------------------------
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$icon = Join-Path $Src "src\hh_applicant_tool\panel\static\img\favicon.ico"
foreach ($s in @(
        @{ Name = "HH панель - включить"; Cmd = "start"; Keep = $true },
        @{ Name = "HH панель - выключить"; Cmd = "stop"; Keep = $false },
        @{ Name = "HH панель - лог"; Cmd = "logs"; Keep = $true })) {
    $lnk = $shell.CreateShortcut((Join-Path $desktop "$($s.Name).lnk"))
    $lnk.TargetPath = "powershell.exe"
    $noExit = if ($s.Keep) { "-NoExit " } else { "" }
    $lnk.Arguments = "$noExit-NoProfile -ExecutionPolicy Bypass -File `"$Ctl`" $($s.Cmd) -InstallDir `"$InstallDir`""
    $lnk.WorkingDirectory = $InstallDir
    if (Test-Path $icon) { $lnk.IconLocation = $icon }
    $lnk.Save()
}
Step "Ярлыки на рабочем столе: включить, выключить, лог"

# ---- 6. Автозапуск Docker Desktop ----------------------------------------------
$store = Join-Path $env:APPDATA "Docker\settings-store.json"
if (Test-Path $store) {
    $json = Get-Content $store -Raw | ConvertFrom-Json
    $json | Add-Member -NotePropertyName AutoStart -NotePropertyValue $true -Force
    $json | ConvertTo-Json -Depth 20 | Set-Content -Encoding UTF8 $store
    Step "Docker Desktop будет запускаться при входе в Windows"
}

# ---- Запуск -------------------------------------------------------------------
& powershell -NoProfile -ExecutionPolicy Bypass -File $Ctl start -InstallDir $InstallDir
