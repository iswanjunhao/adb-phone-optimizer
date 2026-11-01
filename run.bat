@echo off
REM ADB 手机优化助手 —— Windows 启动脚本
REM 用法：先把手机用数据线连上电脑并开启 USB 调试，然后双击本文件。
cd /d "%~dp0"
REM 若仓库内自带 platform-tools\adb.exe 则优先使用（无需额外配置）
if exist "platform-tools\adb.exe" set "ADB_PATH=%~dp0platform-tools\adb.exe"
python server.py
if errorlevel 1 (
  echo.
  echo 如果提示 "python 不是内部或外部命令"，请先安装 Python 3 并勾选 "Add to PATH"。
  echo 另外请确保已安装 adb（Android Platform Tools）并加入 PATH。
  pause
)
