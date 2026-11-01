#!/usr/bin/env bash
# 自动获取 Android platform-tools（含 adb），放到 ./platform-tools/
# 仅在系统未安装 adb 且仓库未带 platform-tools 时使用。
set -e

TARGET="./platform-tools"
if command -v adb >/dev/null 2>&1; then
  echo "✅ 已在 PATH 中找到 adb：$(command -v adb)，无需下载。"
  exit 0
fi
if [ -x "$TARGET/adb" ] || [ -x "$TARGET/adb.exe" ]; then
  echo "✅ 已存在 $TARGET/adb，无需下载。"
  exit 0
fi

OS="$(uname -s)"
case "$OS" in
  Linux)  URL="https://dl.google.com/android/repository/platform-tools-latest-linux.zip" ;;
  Darwin) URL="https://dl.google.com/android/repository/platform-tools-latest-darwin.zip" ;;
  *MINGW*|*MSYS*|*CYGWIN*) URL="https://dl.google.com/android/repository/platform-tools-latest-windows.zip" ;;
  *) echo "❌ 不支持的系统：$OS。请手动下载 platform-tools 并放入 PATH，或放到 ./platform-tools/"; exit 1 ;;
esac

echo "⬇️  下载 platform-tools ($URL) ..."
TMP="$(mktemp -d)"
curl -fL "$URL" -o "$TMP/pt.zip"
echo "📦 解压 ..."
unzip -oq "$TMP/pt.zip" -d "$TMP"
mv "$TMP/platform-tools" "$TARGET"
rm -rf "$TMP"
echo "✅ 已安装到 $TARGET/adb。可运行 ./run.sh 启动。"
