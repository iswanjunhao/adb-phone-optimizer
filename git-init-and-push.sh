#!/usr/bin/env bash
# 在本机（已装 git、已登录 GitHub）运行，完成「初始化 → 提交 → 推送」到 GitHub。
#
# 用法：
#   1) 先在 GitHub 网页上新建一个空仓库（不要勾选 README/LICENSE，本仓库已自带）
#   2) 运行：  ./git-init-and-push.sh  https://github.com/<你的用户名>/<仓库名>.git
#      （或SSH地址：git@github.com:<你的用户名>/<仓库名>.git）
#
# 若已安装 GitHub CLI(gh) 想自动建仓，可改用：
#   gh repo create <仓库名> --private -s . -p <描述>   # 然后再 git push -u origin main
set -e

REMOTE="${1:-}"
cd "$(dirname "$0")"

if [ -z "$REMOTE" ]; then
  echo "❌ 用法: $0 <GitHub 仓库地址(git 或 https)>" >&2
  echo "   例: $0 https://github.com/junhao/adb-device-optimizer.git" >&2
  exit 1
fi

# 1) 初始化（若尚未）
if [ ! -d .git ]; then
  git init -q
  echo "✅ git init"
fi

# 2) 提交全部（.gitignore 已排除二进制/运行时数据）
git add .
if git diff --cached --quiet; then
  echo "ℹ️  没有需要提交的改动。"
else
  git commit -q -m "Initial commit: ADB 设备优化助手（手机 + 墨水屏）"
  echo "✅ 已提交"
fi

# 3) 分支 & 远端
git branch -M main
if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "$REMOTE"
fi

# 4) 推送
git push -u origin main
echo "🎉 已推送到 $REMOTE"
