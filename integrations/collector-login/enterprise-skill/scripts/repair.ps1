# Windows PowerShell 5.1+. Saved as UTF-8 with BOM: 5.1 otherwise decodes the Chinese
# messages with the local code page.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$collectorRoot = Join-Path $env:LOCALAPPDATA 'omnipanelCollectorLogin'
New-Item -ItemType Directory -Force -Path $collectorRoot | Out-Null
$env:UV_INSTALL_DIR = Join-Path $collectorRoot 'bin'
$env:UV_NO_MODIFY_PATH = '1'
$env:UV_CACHE_DIR = Join-Path $collectorRoot 'cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $collectorRoot 'python'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $collectorRoot 'browsers'
$uv = Join-Path $env:UV_INSTALL_DIR 'uv.exe'

function Invoke-Native([scriptblock]$Command) {
  # uv and Playwright print progress on stderr. Under 'Stop', Windows PowerShell 5.1 turns
  # redirected native stderr into a terminating error, so judge success by exit code only.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $Command 2>&1 | ForEach-Object {
      if ($_ -is [System.Management.Automation.ErrorRecord]) { [Console]::Error.WriteLine($_.ToString()) } else { $_ }
    }
  } finally { $ErrorActionPreference = $previous }
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (!(Test-Path $uv)) {
  # Pinned uv 0.13.0 release archive, verified by SHA256. No downloaded script is executed,
  # so this also works where the execution policy only allows signed scripts.
  $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
  $hashes = @{
    'x86_64' = '088962f9e7b7bd9ea740c04c650b2a21c8928c345bd99ac24350dc924dba656c'
    'aarch64' = 'cb54028b59aa87f11cf6dec293ce000420043a49a8aea522e822a671321f8932'
  }
  $archive = Join-Path $collectorRoot 'uv.zip'
  try {
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/download/0.13.0/uv-$arch-pc-windows-msvc.zip" -OutFile $archive
    if ((Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hashes[$arch]) { throw '运行环境校验失败，请联系管理员。' }
    New-Item -ItemType Directory -Force -Path $env:UV_INSTALL_DIR | Out-Null
    Expand-Archive -Path $archive -DestinationPath $env:UV_INSTALL_DIR -Force
    if (!(Test-Path $uv)) { throw '运行环境安装未完成。' }
  } finally { Remove-Item $archive -ErrorAction SilentlyContinue }
}
$python = Join-Path $collectorRoot 'venv\Scripts\python.exe'
if (!(Test-Path $python)) {
  Invoke-Native { & $uv venv --python 3.11 (Join-Path $collectorRoot 'venv') }
}
Invoke-Native { & $uv pip install --python $python '__WHEEL_URL__' }
Invoke-Native { & $python -m playwright install chromium }
Invoke-Native { & $python -m rpa_collector_login.cli repair }
exit 0
