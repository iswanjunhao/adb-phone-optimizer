# Windows 系统使用说明

本说明面向 **Windows 用户**，补齐在 Windows 上配置环境、运行本项目、以及用 **GitHub 个人访问令牌（PAT）** 把项目推送到远程仓库的完整步骤。Linux / macOS 用户请直接看根目录 `README.md`。

---

## 1. 环境配置

### 1.1 安装 Python
1. 到 https://www.python.org/downloads/windows/ 下载 Python 3.8+ 安装包。
2. 安装时务必勾选 **「Add Python to PATH」**（加到环境变量）。
3. 验证：打开「命令提示符」或「PowerShell」，执行
   ```powershell
   python --version
   ```
   能显示版本号即成功。若提示「不是内部或外部命令」，说明 PATH 没配好，重装并勾选上面的选项，或手动把 `Python 安装目录` 与 `Python 安装目录\Scripts` 加入 PATH。

### 1.2 安装 adb（Android 调试桥）
本项目通过 adb 操作手机 / 墨水屏，Windows 上推荐以下任一方式：

**方式 A（推荐，最省事）：下载官方 platform-tools 并放进项目**
1. 下载 Windows 版：https://dl.google.com/android/repository/platform-tools-latest-windows.zip
2. 解压得到 `platform-tools` 文件夹，里面应有 `adb.exe`。
3. 把整个 `platform-tools` 文件夹**直接放到本项目根目录**（即与 `server.py` 同级）。
   - 启动脚本 `run.bat` 会自动优先使用 `platform-tools\adb.exe`，无需任何额外配置。

**方式 B：把 adb 加入系统 PATH**
1. 同上解压 platform-tools 到任意目录（如 `C:\platform-tools`）。
2. 将该目录加入系统环境变量 PATH（设置 → 系统 → 关于 → 高级系统设置 → 环境变量 → Path → 新建）。
3. 验证：
   ```powershell
   adb version
   ```

> 提示：仓库自带的 `get_adb.sh` 是 bash 脚本，在 **Git Bash** 里也能跑（已适配 Windows 下载链接）；纯 cmd/PowerShell 下请用上面的手动方式。

### 1.3 手机 / 墨水屏准备
1. 进入「设置 → 关于手机 → 版本号」连点 7 次，开启**开发者选项**。
2. 在开发者选项里打开 **USB 调试**（小米 / HyperOS 还建议打开「USB 调试（安全设置）」，否则部分系统设置写不进去）。
3. 用数据线连接电脑，手机上弹出的「允许 USB 调试」点**允许**（建议勾选「一律允许」）。
4. 验证连接：
   ```powershell
   adb devices
   ```
   应能看到你的设备序列号及 `device` 状态。连了多台（如手机 + 墨水屏）会都列出来，网页里可切换。

---

## 2. 启动工具

### 方式一：双击启动（最简单）
直接双击项目里的 **`run.bat`**。它会自动用仓库内的 `platform-tools\adb.exe`（若存在），然后启动服务。
启动后浏览器打开 **http://127.0.0.1:8787** 即可使用。

### 方式二：命令行启动
```powershell
cd D:\path\to\phone-optimizer
python server.py
```
若 adb 不在 PATH 且你放在了别处，可临时指定：
```powershell
$env:ADB_PATH = "C:\platform-tools\adb.exe"
python server.py
```

---

## 3. 常用命令速查（PowerShell / 命令提示符）

| 目的 | 命令 |
| --- | --- |
| 启动服务 | `python server.py` |
| 指定 adb 路径 | `$env:ADB_PATH="C:\platform-tools\adb.exe"; python server.py` |
| 查看已连接设备 | `adb devices` |
| 进入设备 shell | `adb shell` |
| 无线调试开端口 | `adb tcpip 5555` |
| 连接无线设备 | `adb connect 192.168.x.x:5555` |

> 端口默认 `8787`。若被占用，可改环境变量：`$env:PORT="9000"` 后再启动。

---

## 4. 推送到 GitHub（PAT 认证方式）

> 以下以 **Git Bash**（安装 Git for Windows 后自带）为例；PowerShell 原生命令见 4.3。

### 4.1 生成个人访问令牌（PAT）
1. 登录 GitHub → 右上角头像 → **Settings → Developer settings → Personal access tokens**。
2. **Classic 令牌**：选 **Tokens (classic) → Generate new token (classic)**，勾选 `repo`（全选 repo 权限）。
3. **Fine-grained 令牌（细粒度）**：选 **Fine-grained tokens → Generate new token**，在目标仓库授予 **Contents: Read and write** 权限。
4. 生成后**立即复制**令牌（只显示一次）。

> ⚠️ **令牌等同于你的密码**，请勿写进代码、截图外传或提交到仓库。推送完成后可在 GitHub 随时 **Revoke（撤销）** 它。

### 4.2 用仓库自带脚本推送（Git Bash）
项目已提供 `git-init-and-push.sh`，把令牌嵌入仓库地址即可：

```bash
# 经典令牌（ghp_ 开头）：令牌直接作为用户名
./git-init-and-push.sh https://ghp_XXXXXXXXXXXX@github.com/<你的用户名>/<仓库名>.git

# 细粒度令牌（github_pat_ 开头）：用户名用 x-access-token
./git-init-and-push.sh https://x-access-token:github_pat_XXXX@github.com/<你的用户名>/<仓库名>.git
```
脚本会自动：初始化仓库 → 按 `.gitignore` 提交 → 设 `main` 分支 → 添加远端 → 推送。

### 4.3 用 PowerShell 原生命令推送
```powershell
# 1) 进入项目目录
cd D:\path\to\phone-optimizer

# 2) 初始化并提交（.gitignore 已排除二进制与运行时数据）
git init
git add .
git commit -m "Initial commit: ADB 设备优化助手（手机 + 墨水屏）"

# 3) 设分支与远端（把令牌嵌入地址）
git branch -M main
$TOKEN = "ghp_XXXXXXXXXXXX"          # 或细粒度：x-access-token:github_pat_XXXX
git remote add origin "https://${TOKEN}@github.com/<你的用户名>/<仓库名>.git"

# 4) 推送
git push -u origin main
```
推送时若弹出凭据框，用户名任意、密码填令牌即可。

### 4.4 在 GitHub 网页上建空仓库
推送前先在 GitHub 网页 **New repository** 建一个空仓库（仓库名随意，如 `adb-device-optimizer`），**不要**勾选自动生成 README / LICENSE（本项目已自带）。建好后复制它的 HTTPS 地址填入上面的命令。

---

## 5. 注意事项

1. **路径不要含中文或空格**：项目目录尽量放在如 `D:\projects\phone-optimizer` 这样的纯英文路径，避免 adb / Python 出现编码或参数解析问题。
2. **杀毒软件 / Windows Defender**：可能误报 `adb.exe` 或 `server.py`，可将项目目录加入白名单，否则会拦截启动。
3. **防火墙**：首次运行若提示网络访问，允许 Python 的私有网络访问（服务只监听本机 127.0.0.1，不对外暴露）。
4. **端口占用**：若 8787 被占，按 3 节改 `PORT` 环境变量，并用新端口访问。
5. **小米 / HyperOS 特殊权限**：部分系统设置写入需要「USB 调试（安全设置）」已开启；卸载系统预装失败时请用「禁用」按钮（可恢复）。
6. **令牌安全**：切勿把真实令牌写进 `README`、脚本或提交到仓库。`.gitignore` 已排除 `platform-tools/`、`data/*.json` 等运行时文件，但令牌若你手动写进文件就会被提交——保持它只在命令行/凭据管理器里。
7. **多设备**：手机与墨水屏同时连接时，网页顶部「选择设备」面板可随时切换，无需拔线。
8. **墨水屏差异**：强制深色、刷新率、录屏在墨水屏上可能无效（会被安全跳过），其余功能正常。

---

## 6. 常见问题

**Q：`python` 命令无效？**
A：Python 没加入 PATH。重装勾选 Add to PATH，或改用完整路径如 `C:\Python313\python.exe server.py`。

**Q：`adb` 找不到？**
A：确认 `platform-tools\adb.exe` 已放在项目根目录，或已把 platform-tools 加入 PATH；可在 PowerShell 跑 `adb version` 验证。

**Q：手机连上但网页显示未连接？**
A：检查「允许 USB 调试」是否点了允许；换原装数据线；确认 `adb devices` 能看到 `device` 状态。

**Q：git push 报 403 / 鉴权失败？**
A：令牌权限不足或已过期——重新生成带 `repo`（classic）或 Contents 写权限（fine-grained）的令牌；确认地址里令牌格式正确（classic 用 `https://<TOKEN>@...`，fine-grained 用 `https://x-access-token:<TOKEN>@...`）。

**Q：不想用 Git Bash，能用纯 PowerShell 吗？**
A：可以，按 4.3 节执行；前提是已安装 Git for Windows（提供 `git` 命令）。
