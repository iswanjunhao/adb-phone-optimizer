#!/bin/sh
# ADB 手机优化助手 —— Linux / macOS 启动脚本
# 用法：先把手机用数据线连上电脑并开启 USB 调试，然后运行本脚本。
cd "$(dirname "$0")"
exec python3 server.py
