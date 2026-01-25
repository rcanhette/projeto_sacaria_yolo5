Param(
  [string]$OutDir = "dist"
)

$ErrorActionPreference = 'Stop'

function New-Dir($path) {
  if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path | Out-Null }
}

$root = (Resolve-Path ".").Path
$central = Join-Path $OutDir 'central'
$agent = Join-Path $OutDir 'agent'

New-Dir $OutDir
New-Dir $central
New-Dir $agent

# --- Central package ---
$centralFiles = @(
  'app.py',
  'config.py',
  'service_config.py',
  'central.ini',
  'central.ini.example',
  'windows_service.ini',
  'requirements-central.txt'
)

foreach ($f in $centralFiles) {
  if (Test-Path $f) { Copy-Item $f -Destination $central -Force }
}

Copy-Item -Recurse -Force routes $central\routes
Copy-Item -Recurse -Force templates $central\templates

# Scripts utilitários (instalação NSSM do Central)
New-Dir "$central\scripts"
if (Test-Path 'scripts/install_nssm_central.ps1') {
  Copy-Item 'scripts/install_nssm_central.ps1' -Destination "$central\scripts" -Force
}

# Serviços necessários ao Central (sem visão/tensor)
$centralServices = @(
  'services/db.py',
  'services/__init__.py',
  'services/auth_repository.py',
  'services/session_repository.py',
  'services/agent_repository.py',
  'services/tc_repository.py',
  'services/runtime.py',
  'services/local_queue.py',
  'services/tc_wall_repository.py'
)
foreach ($s in $centralServices) {
  if (Test-Path $s) {
    $dest = Join-Path $central $s
    New-Dir (Split-Path $dest)
    Copy-Item $s -Destination $dest -Force
  }
}

# --- Agent package ---
$agentFiles = @(
  'agent_app.py',
  'agent.ini',
  'agent.ini.example',
  'requirements-agent.txt',
  'sacaria_yolov5n.pt',
  'best.pt'
)
foreach ($f in $agentFiles) {
  if (Test-Path $f) { Copy-Item $f -Destination $agent -Force }
}

# Serviços do Agente (visão / detector / captura)
$agentServices = @(
  'services/__init__.py',
  'services/capture_point.py',
  'services/video_source.py',
  'services/industrial_tag_detector.py',
  'services/local_queue.py',
  'services/runtime.py',
  'services/session_repository.py',
  'services/db.py'
)
foreach ($s in $agentServices) {
  if (Test-Path $s) {
    $dest = Join-Path $agent $s
    New-Dir (Split-Path $dest)
    Copy-Item $s -Destination $dest -Force
  }
}

# Terceiros (ex.: yolov5) se existir
if (Test-Path 'third_party') {
  Copy-Item -Recurse -Force third_party $agent\third_party
}

# Scripts utilitários (instalação NSSM do Agente)
New-Dir "$agent\scripts"
if (Test-Path 'scripts/install_nssm_agent.ps1') {
  Copy-Item 'scripts/install_nssm_agent.ps1' -Destination "$agent\scripts" -Force
}

"Pacotes montados em: $OutDir"
