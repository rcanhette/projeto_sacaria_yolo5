Param(
  [string]$ServiceName = "ProjetoSacaria_v1",
  [string]$Root = (Resolve-Path ".").Path,
  [int]$Port = 80,
  [string]$NssmExe = "nssm"
)

$ErrorActionPreference = 'Stop'

function Ensure-Dir($p) { if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null } }

# Verifica privilégios de administrador
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
  throw "Execute este script em um PowerShell como Administrador."
}

# Resolve caminho do NSSM (sem depender do subcomando 'version')
try { $null = Get-Command $NssmExe -ErrorAction Stop } catch { throw "NSSM nao encontrado. Informe o caminho com -NssmExe 'C:\\caminho\\nssm.exe'" }

$Waitress = Join-Path $Root 'venv\Scripts\waitress-serve.exe'
if (-not (Test-Path $Waitress)) { throw "Waitress nao encontrado em $Waitress. Ative o venv e instale requirements-central.txt." }

$Logs = Join-Path $Root 'logs'
Ensure-Dir $Logs

$Args = @('--host','0.0.0.0','--port',"$Port",'--threads','16','--backlog','2048','--connection-limit','200','--channel-timeout','90','--call','app:create_app')

# Cria o serviço se ainda não existir (verifica via Service Control Manager)
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$exists = $null -ne $svc
if (-not $exists) {
  Write-Host "Criando servico $ServiceName..."
  & $NssmExe install $ServiceName $Waitress
  if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar servico via NSSM (exit $LASTEXITCODE)." }
  Start-Sleep -Milliseconds 400
}

# Configura parâmetros principais
& $NssmExe set $ServiceName Application $Waitress | Out-Null
& $NssmExe set $ServiceName AppParameters ("{0}" -f ($Args -join ' ')) | Out-Null
& $NssmExe set $ServiceName AppDirectory $Root | Out-Null
& $NssmExe set $ServiceName AppStdout (Join-Path $Logs 'service.out.log') | Out-Null
& $NssmExe set $ServiceName AppStderr (Join-Path $Logs 'service.err.log') | Out-Null

# Rotacao compatível com versões recentes do NSSM
& $NssmExe set $ServiceName AppRotateFiles 1 | Out-Null
& $NssmExe set $ServiceName AppRotateBytes 10485760 | Out-Null

# Ambiente essencial para evitar problemas de encoding/pgpass
& $NssmExe set $ServiceName AppEnvironmentExtra "PYTHONIOENCODING=utf-8" "PYTHONUNBUFFERED=1" "YOLOV5_NO_AUTOINSTALL=1" "PGPASSFILE=NUL" | Out-Null

Write-Host "Servico configurado: $ServiceName"

# Reinicia com tolerância a erro quando ainda não existe/para primeira execução
try { & $NssmExe stop $ServiceName | Out-Null } catch { }
& $NssmExe start $ServiceName | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Warning "NSSM retornou codigo $LASTEXITCODE ao iniciar. Veja os logs." }
Write-Host "Iniciado. Veja logs em $Logs"
