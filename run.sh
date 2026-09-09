#!/usr/bin/env bash
# ============================================================
#  NovelFound 一键启动脚本（macOS / Linux）
#  首次运行会自动创建虚拟环境并安装依赖
# ============================================================
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "[1/3] 创建虚拟环境 .venv ..."
    python3 -m venv .venv
    echo "[2/3] 安装依赖 ..."
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

echo "[3/3] 启动 NovelFound ..."
exec .venv/bin/python main.py
