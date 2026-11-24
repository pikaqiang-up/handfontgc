#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v potrace >/dev/null 2>&1; then
  echo "缺少 potrace。Debian/Ubuntu: sudo apt install potrace"
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo
echo "安装完成。运行： ./run.sh"
