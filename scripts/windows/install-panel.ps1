<#
Установка панели hh-applicant-tool на Windows без Docker.

Всё ставится в одну папку (по умолчанию D:\hh-applicant-tool), чтобы не
забивать системный диск: модели Ollama весят по 5-8 ГБ.

  <InstallDir>\venv            Python с утилитой
  <InstallDir>\config          конфиг, токен hh, история панели (CONFIG_DIR)
  <InstallDir>\ollama-models   модели Ollama (OLLAMA_MODELS)
  <InstallDir>\panel.log       лог панели

Что делает:
  1. ставит Ollama для Windows (через winget), если её нет: она сама
     использует видеокарту NVIDIA; модели складывает в InstallDir;
  2. создаёт venv и ставит утилиту из папки с исходниками (по умолчанию:
     родитель этой папки scripts);
  3. открывает порт панели в брандмауэре только для частной (домашней) сети;
  4. регистрирует задачу планировщика: панель стартует при входе в Windows
     и перезапускается при падении.

Запуск (PowerShell от имени пользователя, правило брандмауэра попросит
права администратора):

  powershell -ExecutionPolicy Bypass -File scripts\windows\install-panel.ps1

Параметры:
  -InstallDir D:\...     куда ставить (по умолчанию D:\hh-applicant-tool)
  -Port 8090             порт панели
  -SourceDir <путь>      где лежат исходники утилиты
  -NoOllama              не ставить Ollama
  -NoAutostart           не регистрировать автозапуск
#>
param(
    [string]$InstallDir = "D:\hh-applicant-tool",
    [int]$Port = 8090,
    [string]$SourceDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [switch]$NoOllama,
    [switch]$NoAutostart
)

$ErrorActionPreference = "Stop"
$Home_ = $InstallDir
$Venv = Join-Path $Home_ "venv"
$ConfigDir = Join-Path $Home_ "config"
$ModelsDir = Join-Path $Home_ "ollama-models"
$Python = Join-Path $Venv "Scripts\python.exe"
$Runner = Join-Path $Home_ "run-panel.ps1"
$TaskName = "hh-applicant-tool panel"

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

# ---- 1. Ollama ----------------------------------------------------------
if (-not $NoOllama) {
    # Модели на тот же диск, что и всё остальное. Переменную читает Ollama
    # при старте, поэтому ставим её до установки
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
    [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $ModelsDir, "User")
    $env:OLLAMA_MODELS = $ModelsDir
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Step "Ollama уже установлена, модели будут в $ModelsDir"
    } else {
        Step "Ставлю Ollama в $Home_\Ollama, модели в $ModelsDir"
        winget install --id Ollama.Ollama -e --location (Join-Path $Home_ "Ollama") --accept-source-agreements --accept-package-agreements --disable-interactivity
    }
}

# ---- 2. Утилита в venv --------------------------------------------------
Step "Готовлю окружение Python в $Venv"
New-Item -ItemType Directory -Force -Path $Home_, $ConfigDir | Out-Null
if (-not (Test-Path $Python)) {
    python -m venv $Venv
}
& $Python -m pip install --upgrade pip --quiet
& $Python -m pip install -e $SourceDir --quiet
& $Python -m hh_applicant_tool --help | Out-Null
Step "Утилита установлена из $SourceDir"

# ---- 3. Адрес ПК в сети --------------------------------------------------
$lan = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.PrefixOrigin -in @("Dhcp", "Manual") -and $_.InterfaceAlias -notlike "vEthernet*" -and $_.IPAddress -match '^(192\.168|10\.|172\.(1[6-9]|2\d|3[01]))' } |
    Select-Object -ExpandProperty IPAddress
$hosts = ($lan -join ",")
Step "Адреса ПК в сети: $hosts"

# Если панель уже запущена (повторная установка), останавливаем её
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*run-panel.ps1*" -or $_.CommandLine -like "*hh_applicant_tool panel*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Скрипт запуска: поднимает Ollama, если она не отвечает, и перезапускает
# панель, если та упала
$OllamaExe = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $OllamaExe) { $OllamaExe = Join-Path $Home_ "Ollama\ollama.exe" }
@"
`$env:HH_PANEL_PUBLIC_HOSTS = "$hosts"
`$env:CONFIG_DIR = "$ConfigDir"
`$env:OLLAMA_URL = "http://127.0.0.1:11434"
`$env:OLLAMA_MODELS = "$ModelsDir"
`$env:PYTHONIOENCODING = "utf-8"
function Start-OllamaIfDown {
    try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3 | Out-Null }
    catch { if (Test-Path "$OllamaExe") { Start-Process -WindowStyle Hidden -FilePath "$OllamaExe" -ArgumentList "serve" } }
}
while (`$true) {
    Start-OllamaIfDown
    & "$Python" -m hh_applicant_tool panel --host 0.0.0.0 --port $Port *>> "$Home_\panel.log"
    Start-Sleep -Seconds 5
}
"@ | Set-Content -Encoding UTF8 $Runner

# ---- 4. Брандмауэр: только частная сеть ---------------------------------
Step "Открываю порт $Port для домашней сети"
$rule = "hh-applicant-tool panel $Port"
$cmd = "if (-not (Get-NetFirewallRule -DisplayName '$rule' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName '$rule' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private | Out-Null }"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($admin) { Invoke-Expression $cmd }
else { Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -Command $cmd" }

# ---- 5. Автозапуск ------------------------------------------------------
if (-not $NoAutostart) {
    Step "Регистрирую автозапуск при входе в Windows"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Runner`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
}

Start-Sleep -Seconds 4
Write-Host ""
Write-Host "Готово. Откройте на ноутбуке:" -ForegroundColor Green
foreach ($ip in $lan) { Write-Host "  https://${ip}:$Port" }
$tokenFile = Join-Path $ConfigDir "panel-token.txt"
if (Test-Path $tokenFile) { Write-Host "Токен для входа: $(Get-Content $tokenFile)" }
else { Write-Host "Токен появится в $Home_\panel.log после первого запуска" }
