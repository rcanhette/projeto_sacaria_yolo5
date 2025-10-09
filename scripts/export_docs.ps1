param(
  [string]$Html = "docs/ARCHITECTURE.html",
  [string]$OutPdf = "docs/ARCHITECTURE.pdf"
)

function Find-BrowserExe {
  $candidates = @(
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe"
  )
  foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
  return $null
}

$browser = Find-BrowserExe
if (-not $browser) {
  Write-Error "Não encontrei Edge/Chrome instalados. Abra o HTML e exporte manualmente para PDF."
  exit 1
}

$htmlPath = Resolve-Path $Html
$pdfPath = Resolve-Path (Split-Path $OutPdf -Parent) | Out-String
$pdfDir = $pdfPath.Trim()
if (-not (Test-Path $pdfDir)) { New-Item -ItemType Directory -Force -Path $pdfDir | Out-Null }

$args = @('--headless','--disable-gpu',"--print-to-pdf=$OutPdf", $htmlPath)
Write-Host "Exportando $Html para $OutPdf usando $browser ..."

$p = Start-Process -FilePath $browser -ArgumentList $args -PassThru -WindowStyle Hidden
$p.WaitForExit()
if ($p.ExitCode -ne 0) {
  Write-Warning "A exportação pode ter falhado (exit $($p.ExitCode)). Verifique se a página carregou scripts (Mermaid)."
} else {
  Write-Host "PDF gerado em $OutPdf"
}

