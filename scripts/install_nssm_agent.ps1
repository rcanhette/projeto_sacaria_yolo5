Param(
  [string]$ServiceName = "ProjetoSacaria_Agent",
  [string]$Root = (Resolve-Path ".").Path,
  [ValidateSet('python','waitress')][string]$Mode = 'python',
  [int]$Port = 9090,
  [string]$NssmExe = "nssm"
)

$ErrorActionPreference = 'Stop'

function Ensure-Dir($p) { if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null } }

if ($Mode -eq 'python') {
  $App = Join-Path $Root 'venv\Scripts\python.exe'
  $AppParams = 'agent_app.py'
} else {
  $App = Join-Path $Root 'venv\Scripts\waitress-serve.exe'
  if (-not (Test-Path $App)) { throw "Waitress nao encontrado em $App. Ative o venv e instale requirements-agent.txt." }
  $AppParams = "--host 0.0.0.0 --port $Port --call agent_app:create_agent_app"
}

if (-not (Test-Path $App)) { throw "Executavel nao encontrado: $App. Verifique o venv." }

$Logs = Join-Path $Root 'logs'
Ensure-Dir $Logs

# Verifica privilégios e NSSM
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
  throw "Execute este script em um PowerShell como Administrador."
}
try { $null = Get-Command $NssmExe -ErrorAction Stop } catch { throw "NSSM nao encontrado. Informe o caminho com -NssmExe 'C:\\caminho\\nssm.exe'" }

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$exists = $null -ne $svc
if (-not $exists) { & $NssmExe install $ServiceName $App | Out-Null; Start-Sleep -Milliseconds 400 }

& $NssmExe set $ServiceName Application $App | Out-Null
& $NssmExe set $ServiceName AppParameters $AppParams | Out-Null
& $NssmExe set $ServiceName AppDirectory $Root | Out-Null
& $NssmExe set $ServiceName AppStdout (Join-Path $Logs 'agent.out.log') | Out-Null
& $NssmExe set $ServiceName AppStderr (Join-Path $Logs 'agent.err.log') | Out-Null
& $NssmExe set $ServiceName AppRotateFiles 1 | Out-Null
& $NssmExe set $ServiceName AppRotateBytes 10485760 | Out-Null

& $NssmExe set $ServiceName AppEnvironmentExtra "PYTHONIOENCODING=utf-8" "PYTHONUNBUFFERED=1" | Out-Null

Write-Host "Servico configurado: $ServiceName"
try { & $NssmExe stop $ServiceName | Out-Null } catch { }
& $NssmExe start $ServiceName | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Warning "NSSM retornou codigo $LASTEXITCODE ao iniciar. Veja os logs." }
Write-Host "Iniciado. Veja logs em $Logs"
