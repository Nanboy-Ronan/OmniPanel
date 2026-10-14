$ErrorActionPreference = 'Stop'
$collectorRoot = Join-Path $env:LOCALAPPDATA 'omnipanelCollectorLogin'
New-Item -ItemType Directory -Force -Path $collectorRoot | Out-Null
$env:UV_INSTALL_DIR = Join-Path $collectorRoot 'bin'
$env:UV_NO_MODIFY_PATH = '1'
$env:UV_CACHE_DIR = Join-Path $collectorRoot 'cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $collectorRoot 'python'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $collectorRoot 'browsers'
$uv = Join-Path $env:UV_INSTALL_DIR 'uv.exe'
if (!(Test-Path $uv)) {
  $installer = Join-Path $collectorRoot 'uv-installer.ps1'
  try {
    Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/0.13.0/install.ps1' -OutFile $installer
    if ((Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne '6d7ef89ba04838a03d1ebf7f84be4ba2b98b848a0a9ab8c2855658f13106c2c2') { throw '运行环境校验失败，请联系管理员。' }
    & $installer
    if (!(Test-Path $uv)) { throw '运行环境安装未完成。' }
  } finally { Remove-Item $installer -ErrorAction SilentlyContinue }
}
$python = Join-Path $collectorRoot 'venv\Scripts\python.exe'
if (!(Test-Path $python)) {
  & $uv venv --python 3.11 (Join-Path $collectorRoot 'venv')
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $uv pip install --python $python '__WHEEL_URL__'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m rpa_collector_login.cli repair
exit $LASTEXITCODE
