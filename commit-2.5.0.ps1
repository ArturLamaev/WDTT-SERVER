#requires -version 5.1
# Одноразовый скрипт: проверка + коммит + пуш 2.5.0.
# Запуск: powershell -ExecutionPolicy Bypass -File C:\My_Data\Scripts\Telegram\WDTT\commit-2.5.0.ps1

$ErrorActionPreference = 'Stop'
$root = 'C:\My_Data\Scripts\Telegram\WDTT'
Set-Location $root

$msg = @'
feat: upstream 1.4.5 compat - 6 VK hashes + ported connection diagnostics (2.5.0)

- kernel: port netdiag.go (QWDTT-DIAG/1 probe, HKDF chacha20poly1305), bot_links.go,
  listenWrapped naming, diag.key load/create, keepalive pong 0xFF (DTLS + raw)
- fork: keep traffic quotas, MaxDevices<=0 unlimited, device-owner binding;
  diagnostics are read-only mirrors (wouldBindDevice / diagOwnerAllowsLocked)
- admin API: diag_key / raw_port / direct_port; bot: /diag command + qwdtt:// links
- panel: VK_HASH_LIMIT=6, ensure_hash_quota, qwdtt:// config links, fleet web links
- tests: 6-hash limit, diag key vector, qwdtt link in fleet web
- version 2.5.0
'@

Write-Host '== pytest ==' -ForegroundColor Cyan
$env:PYTHONPATH = Join-Path $root 'panel'
python -m pytest panel/tests -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed ($LASTEXITCODE)" }

$go = Get-Command go -ErrorAction SilentlyContinue
if ($go) {
    Write-Host '== go build ==' -ForegroundColor Cyan
    Push-Location (Join-Path $root 'src\proxy-turn-vk-android-1.4.3')
    & $go.Source build ./...
    $goExit = $LASTEXITCODE
    Pop-Location
    if ($goExit -ne 0) { throw "go build failed ($goExit)" }
} else {
    Write-Host '== go build SKIPPED (go not installed here; kernel builds on server via build.sh) ==' -ForegroundColor Yellow
}

Write-Host '== git status ==' -ForegroundColor Cyan
git status --porcelain

Write-Host '== commit ==' -ForegroundColor Cyan
git add -A
git commit -m $msg
if ($LASTEXITCODE -ne 0) {
    Write-Host 'commit failed or nothing to commit' -ForegroundColor Yellow
}

Write-Host '== push origin (15s) ==' -ForegroundColor Cyan
git -c http.lowSpeedLimit=1 -c http.lowSpeedTime=15 push origin main
$originOk = ($LASTEXITCODE -eq 0)

if (-not $originOk) {
    Write-Host 'origin FAILED -> pushing mirror only' -ForegroundColor Yellow
}

Write-Host '== push mirror ==' -ForegroundColor Cyan
git push https://git.a9fm.best/etoneya/wdtt-server main
$mirrorOk = ($LASTEXITCODE -eq 0)

Write-Host ''
Write-Host ("origin: " + $(if ($originOk) { 'OK' } else { 'FAIL' })) -ForegroundColor $(if ($originOk) { 'Green' } else { 'Red' })
Write-Host ("mirror: " + $(if ($mirrorOk) { 'OK' } else { 'FAIL' })) -ForegroundColor $(if ($mirrorOk) { 'Green' } else { 'Red' })
git log --oneline -1