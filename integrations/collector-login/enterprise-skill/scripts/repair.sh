#!/bin/sh
set -eu
umask 077
collector_root="${XDG_DATA_HOME:-$HOME/.local/share}/omnipanel-collector-login"
mkdir -p "$collector_root"
export UV_INSTALL_DIR="$collector_root/bin" UV_NO_MODIFY_PATH=1 UV_CACHE_DIR="$collector_root/cache" UV_PYTHON_INSTALL_DIR="$collector_root/python"
export PLAYWRIGHT_BROWSERS_PATH="$collector_root/browsers"
if [ ! -x "$collector_root/bin/uv" ]; then
  installer=$(mktemp "$collector_root/installer.XXXXXX")
  trap 'rm -f "$installer"' EXIT HUP INT TERM
  curl --proto '=https' --tlsv1.2 -fLsS https://astral.sh/uv/0.13.0/install.sh -o "$installer"
  if command -v shasum >/dev/null 2>&1; then actual=$(shasum -a 256 "$installer" | cut -d ' ' -f 1); else actual=$(sha256sum "$installer" | cut -d ' ' -f 1); fi
  [ "$actual" = '283cbef4bdaca819bd896b0dacb8a085c4c05dbc880642aa5001d97bc4db74ba' ] || { echo '运行环境校验失败，请联系管理员。' >&2; exit 1; }
  sh "$installer"
  rm -f "$installer"
  trap - EXIT HUP INT TERM
fi
if [ ! -x "$collector_root/venv/bin/python" ]; then
  "$collector_root/bin/uv" venv --python 3.11 "$collector_root/venv"
fi
"$collector_root/bin/uv" pip install --python "$collector_root/venv/bin/python" '__WHEEL_URL__'
"$collector_root/venv/bin/python" -m playwright install chromium
exec "$collector_root/venv/bin/python" -m rpa_collector_login.cli repair
