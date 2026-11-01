#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ADB 手机优化助手 —— 后端服务（零依赖，仅用 Python 标准库）
提供 Web GUI 所需的 JSON API，封装 adb 命令，对小米/Android 手机做安全优化。

部署：在你自己的电脑上运行（手机通过 USB 连接并开启调试）。
依赖：电脑已安装 adb（在 PATH 中），或通过环境变量 ADB_PATH 指定。
"""

import os
import sys
import re
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))

# ---- adb 路径解析 ----
def resolve_adb():
    if os.environ.get("ADB_PATH"):
        return os.environ["ADB_PATH"]
    found = shutil.which("adb")
    if found:
        return found
    # 兜底：使用随附的 Linux platform-tools（仅当你的电脑是 Linux 时适用）
    bundled = os.path.join(BASE_DIR, "platform-tools", "adb")
    if os.path.isfile(bundled):
        return bundled
    return "adb"  # 最后尝试 PATH

ADB = resolve_adb()

# ---- 包名安全库（不可卸载的关键系统包 / 可卸载预装 / 常见应用对照）----
PKG_DB = {}

def load_pkg_db():
    global PKG_DB
    p = os.path.join(BASE_DIR, "packages_db.json")
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            PKG_DB = {e["pkg"]: e for e in data.get("packages", [])}
        except Exception:
            PKG_DB = {}

load_pkg_db()

_lock = threading.Lock()  # 串行化 adb 调用，避免多请求冲突

# 当前选中的设备序列号（多设备时由前端选择）
_selected = {"serial": None}


def run_adb(args, serial=None, timeout=40):
    """执行一条 adb 命令，返回 (returncode, stdout, stderr)。"""
    if serial is None:
        serial = _selected.get("serial")
    cmd = [ADB]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return -1, "", "adb 未找到：%s（请在 PATH 中安装 adb，或用 ADB_PATH 指定）" % ADB
    except subprocess.TimeoutExpired:
        return -2, "", "命令执行超时（%ss）" % timeout


def run_sh(cmdline, serial=None, timeout=40):
    """在设备上执行 shell 命令（接收整条命令字符串）。"""
    return run_adb(["shell", cmdline], serial=serial, timeout=timeout)


# ---------- 业务逻辑 ----------

def get_devices():
    rc, out, err = run_adb(["devices", "-l"])
    if rc != 0:
        return []
    devices = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial = parts[0]
        state = parts[1] if len(parts) > 1 else "unknown"
        model = ""
        for p in parts[2:]:
            if p.startswith("model:"):
                model = p.split(":", 1)[1]
        if state == "device":
            devices.append({"serial": serial, "state": state, "model": model})
    return devices


def pick_serial():
    """确定要操作的设备序列号；无设备/多设备未选时返回 None。"""
    serial = _selected.get("serial")
    if serial:
        return serial
    devs = get_devices()
    if len(devs) == 1:
        return devs[0]["serial"]
    return None


def device_info(serial):
    info = {"serial": serial}
    # 型号 / 厂商 / Android 版本 / SDK
    for prop, key in [
        ("ro.product.model", "model"),
        ("ro.product.brand", "brand"),
        ("ro.build.version.release", "android"),
        ("ro.build.version.sdk", "sdk"),
        ("ro.product.cpu.abi", "cpu"),
    ]:
        rc, out, _ = run_sh("getprop %s" % prop, serial=serial)
        info[key] = out.strip() if rc == 0 else ""
    # 电池
    rc, out, _ = run_sh("dumpsys battery", serial=serial)
    if rc == 0:
        level = scale = None
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.startswith("level:"):
                level = ln.split(":", 1)[1].strip()
            elif ln.startswith("scale:"):
                scale = ln.split(":", 1)[1].strip()
            elif ln.startswith("temperature:"):
                info["battery_temp"] = str(int(ln.split(":", 1)[1].strip()) / 10) + "°C"
            elif ln.startswith("status:"):
                info["battery_status"] = ln.split(":", 1)[1].strip()
        if level:
            info["battery_level"] = level + ("%" if scale else "")
    # 内存
    rc, out, _ = run_sh("cat /proc/meminfo", serial=serial)
    if rc == 0:
        for ln in out.splitlines():
            if ln.startswith("MemTotal:"):
                kb = int(ln.split()[1])
                info["mem_total"] = "%.1f GB" % (kb / 1024 / 1024)
            elif ln.startswith("MemAvailable:"):
                kb = int(ln.split()[1])
                info["mem_available"] = "%.1f GB" % (kb / 1024 / 1024)
    # 存储（/data）
    rc, out, _ = run_sh("df -h /data /storage/emulated", serial=serial)
    if rc == 0:
        lines = [l for l in out.splitlines() if "/" in l]
        if lines:
            parts = lines[0].split()
            if len(parts) >= 4:
                info["storage_used"] = parts[2]
                info["storage_avail"] = parts[3]
    return info


def list_user_apps(serial):
    rc, out, _ = run_adb(["shell", "pm", "list", "packages", "-3"], serial=serial)
    if rc != 0:
        return []
    pkgs = []
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("package:"):
            pkgs.append(ln.split("package:", 1)[1].strip())
    return sorted(pkgs)


def get_all_packages(serial):
    """列出全部已安装包，并标注 system(系统) / cat(风险分类) / name(友好名)。"""
    res = {}
    for flag in ("-s", "-3"):
        rc, out, _ = run_adb(["shell", "pm", "list", "packages", flag], serial=serial)
        if rc == 0:
            for ln in out.splitlines():
                ln = ln.strip()
                if ln.startswith("package:"):
                    pkg = ln.split("package:", 1)[1].strip()
                    res.setdefault(pkg, {})["system"] = (flag == "-s")
    out_list = []
    for pkg, meta in res.items():
        entry = PKG_DB.get(pkg, {})
        out_list.append({
            "pkg": pkg,
            "name": entry.get("name", ""),
            "cat": entry.get("cat", "unknown"),
            "system": meta.get("system", False),
            "note": entry.get("note", ""),
        })
    return out_list


def list_removed_apps(serial):
    """列出已被当前用户卸载、但可用 install-existing 恢复的包。
    原理：pm list packages -u 含未安装项，与已安装集合取差集即得。"""
    rc1, out1, _ = run_adb(["shell", "pm", "list", "packages"], serial=serial)
    rc2, out2, _ = run_adb(["shell", "pm", "list", "packages", "-u"], serial=serial)
    if rc1 != 0 or rc2 != 0:
        return []
    installed = set()
    for ln in out1.splitlines():
        ln = ln.strip()
        if ln.startswith("package:"):
            installed.add(ln.split("package:", 1)[1].strip())
    removed = set()
    for ln in out2.splitlines():
        ln = ln.strip()
        if ln.startswith("package:"):
            pkg = ln.split("package:", 1)[1].strip()
            if pkg not in installed:
                removed.add(pkg)
    return sorted(removed)


# pm 常见失败原因（英文代号 → 中文说明），用于把底层报错翻译成用户能看懂的话
_PM_FAIL_CN = {
    "DELETE_FAILED_INTERNAL_ERROR": "系统/厂商保护，无法卸载（建议改用「禁用」）",
    "DELETE_FAILED_DEVICE_POLICY_MANAGER": "受设备策略保护（如设备管理员/工作资料），无法卸载",
    "DELETE_FAILED_OWNER_BLOCKED": "被设备所有者或管理应用阻止，无法卸载",
    "DELETE_FAILED_UNABLE_TO_UNINSTALL": "系统不允许卸载此应用（建议改用「禁用」）",
    "DELETE_FAILED_USER_RESTRICTED": "受用户限制，无法卸载（建议改用「禁用」）",
    "DELETE_FAILED_APP_UNAVAILABLE": "应用当前不可用，无法卸载",
    "DELETE_FAILED_ABORTED": "操作被中止",
    "DELETE_FAILED_INVALID_OWNER": "设备所有者校验失败",
    "-1000": "系统底层拒绝（多为厂商/系统保护，建议改用「禁用」）",
    "-1": "操作被拒绝（权限或策略限制）",
}


def _is_system_pkg(pkg, serial):
    """判断包是否为系统预装（不可卸载，只能禁用）。查询失败返回 None。"""
    rc, out, _ = run_adb(["shell", "pm", "list", "packages", "-s"], serial=serial)
    if rc != 0:
        return None
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("package:"):
            if ln.split("package:", 1)[1].strip() == pkg:
                return True
    return False


def _pm_failure_reason(out, err):
    """从 pm 的输出/stderr 中解析失败原因。

    关键点：Android 的 pm 在失败时常把 'Failure [...]' 打到 stdout，
    且退出码为 0，所以不能只看 rc。这里同时检查 stdout/stderr 并翻译常见原因。
    """
    text = (out or "").strip()
    if not text:
        text = (err or "").strip()
    low = text.lower()
    if "failure" in low:
        m = re.search(r"\[(.*?)\]", text)
        reason = m.group(1).strip() if m else text
    else:
        reason = text or "未知错误"
    if reason in _PM_FAIL_CN:
        return _PM_FAIL_CN[reason]
    return reason



def get_label(pkg, serial):
    """尽力获取单个包的显示名（best-effort，失败返回空串）。"""
    rc, out, _ = run_sh("pm dump %s" % pkg, serial=serial)
    if rc != 0:
        return ""
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("nonLocalizedLabel="):
            return ln.split("=", 1)[1].strip()
    return ""


def get_setting(namespace, key, serial):
    rc, out, _ = run_sh("settings get %s %s" % (namespace, key), serial=serial)
    return out.strip() if rc == 0 else ""


def put_setting(namespace, key, value, serial):
    return run_sh("settings put %s %s %s" % (namespace, key, value), serial=serial)


# ---------- HTTP 处理 ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默日志，避免刷屏

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            return self._serve_file("index.html")
        if path.startswith("/web/"):
            return self._serve_file(path[len("/web/"):])
        if path == "/api/devices":
            return self._send_json({"ok": True, "data": get_devices()})
        if path == "/api/status":
            return self._api_status()
        if path == "/api/info":
            return self._api_info()
        if path == "/api/apps":
            return self._api_apps()
        if path == "/api/anim":
            return self._api_get_anim()
        if path == "/api/battery_whitelist":
            return self._api_battery_whitelist()
        if path == "/api/packages":
            return self._api_packages()
        if path == "/api/tweak":
            return self._api_tweak_state()
        if path == "/api/label":
            q = parse_qs(parsed.query)
            pkg = (q.get("pkg") or [""])[0]
            serial = pick_serial()
            if not serial:
                return self._send_json({"ok": False, "error": "无可用设备"}, 400)
            name = get_label(pkg, serial) if pkg else ""
            return self._send_json({"ok": True, "pkg": pkg, "label": name})
        if path == "/api/fs":
            q = parse_qs(parsed.query)
            p = (q.get("path") or ["/sdcard"])[0]
            serial = pick_serial()
            if not serial:
                return self._send_json({"ok": False, "error": "无可用设备"}, 400)
            entries, real = fs_list(p, serial)
            if entries is None:
                return self._send_json({"ok": False, "error": real}, 500)
            return self._send_json({"ok": True, "path": real, "data": entries})
        if path == "/api/fs/dl":
            q = parse_qs(parsed.query)
            name = (q.get("name") or [""])[0]
            fp = os.path.join(FILE_DIR, os.path.basename(name))
            if not os.path.isfile(fp):
                return self._send_json({"ok": False, "error": "文件不存在"}, 404)
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % os.path.basename(name))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/wireless":
            return self._send_json(wireless_status())
        if path == "/api/battery":
            serial = pick_serial()
            if not serial:
                return self._send_json({"ok": False, "error": "无可用设备"}, 400)
            return self._send_json({"ok": True, "data": battery_info(serial)})
        if path == "/api/perf":
            serial = pick_serial()
            if not serial:
                return self._send_json({"ok": False, "error": "无可用设备"}, 400)
            return self._send_json({"ok": True, "data": perf_info(serial)})
        if path == "/api/perms":
            serial = pick_serial()
            if not serial:
                return self._send_json({"ok": False, "error": "无可用设备"}, 400)
            return self._send_json({"ok": True, "data": perm_audit(serial)})
        if path == "/api/report":
            serial = pick_serial()
            if not serial:
                return self._send_json({"ok": False, "error": "无可用设备"}, 400)
            return self._send_json({"ok": True, "data": device_report(serial)})
        if path == "/api/macro":
            return self._send_json({"ok": True, "data": macro_list()})
        if path == "/api/schedule":
            return self._send_json({"ok": True, "data": schedule_load()})
        if path == "/api/preset":
            return self._send_json({"ok": True, "data": preset_list()})
        return self._send_json({"ok": False, "error": "未知接口"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        body = {}
        if path != "/api/fs/upload":
            try:
                if raw:
                    body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = {}
        if path == "/api/fs/upload":
            name, data = _parse_upload(self.headers, raw)
            if not name:
                return self._send_json({"ok": False, "error": "未找到文件"}, 400)
            safe = os.path.basename(name)
            with open(os.path.join(UPLOAD_DIR, safe), "wb") as f:
                f.write(data)
            return self._send_json({"ok": True, "name": safe, "size": len(data)})
        if path == "/api/select":
            serial = body.get("serial")
            devs = get_devices()
            if any(d["serial"] == serial for d in devs):
                _selected["serial"] = serial
                return self._send_json({"ok": True, "serial": serial})
            return self._send_json({"ok": False, "error": "设备不存在或未授权"}, 400)
        if path == "/api/boost":
            return self._api_boost()
        if path == "/api/anim":
            return self._api_set_anim(body)
        if path == "/api/setting":
            return self._api_setting(body)
        if path == "/api/app/action":
            return self._api_app_action(body)
        if path == "/api/tweak":
            return self._api_tweak_action(body)
        if path == "/api/battery_whitelist":
            return self._api_battery_whitelist_action(body)
        if path == "/api/shell":
            return self._api_shell(body)
        if path == "/api/fs":
            serial = pick_serial()
            if not serial:
                return self._send_json({"ok": False, "error": "无可用设备"}, 400)
            op = body.get("op")
            if op == "pull":
                nm, err = fs_pull(body.get("src", ""), serial)
                if not nm:
                    return self._send_json({"ok": False, "error": err}, 500)
                return self._send_json({"ok": True, "name": nm, "message": "已拉取到电脑：%s" % nm})
            elif op == "push":
                ok, msg = fs_push(body.get("name", ""), body.get("dst", ""), serial)
                return self._send_json({"ok": ok, "error": None if ok else msg, "message": msg}, 200 if ok else 500)
            elif op == "mkdir":
                rc, _, err = run_sh("mkdir -p %s" % shell_quote(body.get("path", "")))
                return self._send_json({"ok": rc == 0, "error": err}, 500 if rc != 0 else 200)
            return self._send_json({"ok": False, "error": "未知 op"}, 400)
        if path == "/api/wireless":
            serial = pick_serial()
            op = body.get("op")
            if op == "enable":
                ok, msg = wireless_enable(int(body.get("port", 5555) or 5555), serial)
            elif op == "connect":
                ok, msg = wireless_connect("%s:%s" % (body.get("ip", ""), body.get("port", 5555)), serial)
            elif op == "disconnect":
                ok, msg = wireless_disconnect(body.get("endpoint", ""), serial)
            else:
                return self._send_json({"ok": False, "error": "未知 op"}, 400)
            return self._send_json({"ok": ok, "error": None if ok else msg, "message": msg}, 200 if ok else 500)
        if path == "/api/macro/save":
            macro_save(body.get("name", ""), body.get("steps", []), body.get("desc", ""))
            return self._send_json({"ok": True, "message": "已保存宏"})
        if path == "/api/macro/run":
            serial = pick_serial()
            if not serial:
                return self._send_json({"ok": False, "error": "无可用设备"}, 400)
            log = macro_run(body.get("name", ""), serial)
            return self._send_json({"ok": True, "log": log})
        if path == "/api/macro/delete":
            macro_delete(body.get("name", ""))
            return self._send_json({"ok": True, "message": "已删除"})
        if path == "/api/schedule/add":
            schedule_add(body)
            return self._send_json({"ok": True, "message": "已添加定时任务"})
        if path == "/api/schedule/delete":
            schedule_delete(body.get("name", ""))
            return self._send_json({"ok": True, "message": "已删除"})
        if path == "/api/preset/apply":
            serial = pick_serial()
            if not serial:
                return self._send_json({"ok": False, "error": "无可用设备"}, 400)
            log = preset_apply(body.get("steps", []), serial)
            return self._send_json({"ok": True, "log": log})
        if path == "/api/preset/save":
            preset_save(body.get("name", ""), body.get("steps", []))
            return self._send_json({"ok": True, "message": "已保存方案"})
        if path == "/api/preset/delete":
            preset_delete(body.get("name", ""))
            return self._send_json({"ok": True, "message": "已删除"})
        return self._send_json({"ok": False, "error": "未知接口"}, 404)

    # ---- 具体接口实现 ----
    def _serve_file(self, name):
        fp = os.path.join(WEB_DIR, name)
        if not os.path.isfile(fp):
            self.send_response(404)
            self.end_headers()
            return
        ctype = "text/html; charset=utf-8"
        if name.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif name.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api_status(self):
        devs = get_devices()
        serial = pick_serial()
        if not serial:
            return self._send_json({
                "ok": True, "connected": False,
                "devices": devs,
                "message": "未检测到已授权的设备。请确认：① 手机已用数据线连接电脑；② 已开启「USB 调试」；③ 手机弹窗中点击「允许调试」。" if not devs else "发现多台设备，请在界面中选择一台。"
            })
        return self._send_json({"ok": True, "connected": True, "serial": serial, "devices": devs})

    def _api_info(self):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        try:
            info = device_info(serial)
        except Exception as e:
            return self._send_json({"ok": False, "error": str(e)}, 500)
        return self._send_json({"ok": True, "data": info})

    def _api_apps(self):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        apps = list_user_apps(serial)
        return self._send_json({"ok": True, "data": apps})

    def _api_packages(self):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        try:
            pkgs = get_all_packages(serial)
            removed = list_removed_apps(serial)
        except Exception as e:
            return self._send_json({"ok": False, "error": str(e)}, 500)
        pkgs.sort(key=lambda x: (x["system"], x["name"] or x["pkg"]))
        removed_list = []
        for pkg in removed:
            entry = PKG_DB.get(pkg, {})
            removed_list.append({
                "pkg": pkg,
                "name": entry.get("name", ""),
                "cat": entry.get("cat", "unknown"),
                "system": entry.get("system", False),
                "note": entry.get("note", ""),
                "removed": True,
            })
        return self._send_json({"ok": True, "data": pkgs, "removed": removed_list})

    def _api_boost(self):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        rc, out, err = run_sh("am kill-all", serial=serial)
        if rc != 0:
            return self._send_json({"ok": False, "error": err or "清理失败"}, 500)
        return self._send_json({"ok": True, "message": "已清理后台进程，释放内存。"})

    def _api_get_anim(self):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        keys = ["window_animation_scale", "transition_animation_scale", "animator_animation_scale"]
        data = {k: get_setting("global", k, serial) for k in keys}
        return self._send_json({"ok": True, "data": data})

    def _api_set_anim(self, body):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        value = body.get("value", "1")
        keys = ["window_animation_scale", "transition_animation_scale", "animator_animation_scale"]
        results = []
        for k in keys:
            rc, _, err = put_setting("global", k, value, serial)
            results.append((k, rc, err))
        failed = [k for k, rc, _ in results if rc != 0]
        if failed:
            return self._send_json({"ok": False, "error": "设置失败: " + ",".join(failed)}, 500)
        return self._send_json({"ok": True, "message": "动画缩放已设为 %s" % value})

    def _api_setting(self, body):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        ns = body.get("namespace", "global")
        key = body.get("key")
        value = body.get("value")
        if not key or value is None:
            return self._send_json({"ok": False, "error": "缺少 key 或 value"}, 400)
        rc, _, err = put_setting(ns, key, value, serial)
        if rc != 0:
            return self._send_json({"ok": False, "error": err or "设置失败"}, 500)
        return self._send_json({"ok": True, "message": "已设置 %s/%s = %s" % (ns, key, value)})

    def _api_app_action(self, body):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        pkg = body.get("pkg")
        action = body.get("action")
        if not pkg or not action:
            return self._send_json({"ok": False, "error": "缺少 pkg 或 action"}, 400)
        entry = PKG_DB.get(pkg, {})
        # 关键系统包保护：卸载/禁用需显式 force 二次确认
        if action in ("uninstall", "disable") and entry.get("cat") == "critical" and not body.get("force"):
            return self._send_json({
                "ok": False, "blocked": True,
                "name": entry.get("name", pkg),
                "reason": entry.get("note", "该包为系统关键组件，误操作可能导致系统无法使用。")
            }, 400)
        if action == "force_stop":
            rc, out, err = run_sh("am force-stop %s" % pkg, serial=serial)
        elif action == "clear":
            rc, out, err = run_adb(["shell", "pm", "clear", pkg], serial=serial)
        elif action == "disable":
            rc, out, err = run_adb(["shell", "pm", "disable-user", "--user", "0", pkg], serial=serial)
        elif action == "enable":
            rc, out, err = run_adb(["shell", "pm", "enable", pkg], serial=serial)
        elif action == "uninstall":
            # -k 保留数据；--user 0 仅对当前用户移除（无需 root，可恢复）
            rc, out, err = run_adb(["shell", "pm", "uninstall", "-k", "--user", "0", pkg], serial=serial)
        elif action == "restore":
            rc, out, err = run_adb(["shell", "pm", "install-existing", pkg], serial=serial)
        else:
            return self._send_json({"ok": False, "error": "未知 action"}, 400)
        # pm 失败时常常 stdout 含 'Failure [...]' 且退出码为 0，必须解析输出而非只看 rc
        combined = (out or "") + "\n" + (err or "")
        if (rc != 0) or ("failure" in combined.lower()):
            reason = _pm_failure_reason(out, err)
            if action == "uninstall" and _is_system_pkg(pkg, serial):
                reason = (reason + " —— 该应用为系统预装，无法卸载；请改用「禁用」按钮停用（可随时恢复）。") if reason else "该应用为系统预装，无法卸载；请改用「禁用」。"
            return self._send_json({"ok": False, "error": reason or "操作失败"}, 500)
        return self._send_json({"ok": True, "message": "%s 操作成功" % action})

    def _api_battery_whitelist(self):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        rc, out, _ = run_sh("dumpsys deviceidle whitelist", serial=serial)
        if rc != 0:
            return self._send_json({"ok": False, "error": "获取失败"}, 500)
        items = [l.strip().lstrip("+").lstrip("-") for l in out.splitlines() if l.strip()]
        return self._send_json({"ok": True, "data": items})

    def _api_battery_whitelist_action(self, body):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        pkg = body.get("pkg")
        action = body.get("action")
        if not pkg:
            return self._send_json({"ok": False, "error": "缺少 pkg"}, 400)
        sign = "+" if action == "add" else "-"
        rc, _, err = run_sh("dumpsys deviceidle whitelist %s%s" % (sign, pkg), serial=serial)
        if rc != 0:
            return self._send_json({"ok": False, "error": err or "操作失败"}, 500)
        return self._send_json({"ok": True, "message": "电池优化白名单已%s %s" % ("加入" if action == "add" else "移除", pkg)})

    def _api_shell(self, body):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        cmd = body.get("command")
        if not cmd:
            return self._send_json({"ok": False, "error": "缺少 command"}, 400)
        rc, out, err = run_sh(cmd, serial=serial, timeout=60)
        return self._send_json({
            "ok": rc == 0,
            "code": rc,
            "stdout": out,
            "stderr": err,
        })

    def _api_tweak_state(self):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        try:
            state = tweak_state(serial)
        except Exception as e:
            return self._send_json({"ok": False, "error": str(e)}, 500)
        return self._send_json({"ok": True, "data": state})

    def _api_tweak_action(self, body):
        serial = pick_serial()
        if not serial:
            return self._send_json({"ok": False, "error": "无可用设备"}, 400)
        action = body.get("action")
        if not action:
            return self._send_json({"ok": False, "error": "缺少 action"}, 400)
        ok, msg = do_tweak(action, body, serial)
        if ok is True:
            return self._send_json({"ok": True, "message": msg})
        elif ok == "screencap":
            return self._send_json({"ok": True, "message": "已截图，保存在手机：%s" % msg, "path": msg})
        elif ok == "screenrecord":
            return self._send_json({"ok": True, "message": "已录屏，保存在手机：%s" % msg, "path": msg})
        else:
            return self._send_json({"ok": False, "error": msg}, 500)


# ---------- 系统调优（Tweak）：无需 root 的隐藏设置 ----------
def tweak_state(serial):
    """读取一组常用、可经 ADB 调整的设置当前值，用于初始化 UI。"""
    def g(ns, k):
        return get_setting(ns, k, serial)
    return {
        "force_dark": g("global", "force_dark"),
        "sensors_off": g("secure", "sensors_off"),
        "font_scale": g("system", "font_scale"),
        "background_limit": g("global", "background_process_limit"),
        "ota_disable": g("global", "ota_disable_automatic_update"),
        "power_mode": g("system", "power_mode"),
        "absolute_volume": g("global", "disable_absolute_volume"),
        "private_dns_mode": g("global", "private_dns_mode"),
        "private_dns_spec": g("global", "private_dns_specifier"),
        "min_refresh": g("system", "min_refresh_rate"),
        "peak_refresh": g("system", "peak_refresh_rate"),
        "personalized_ad": g("global", "personalized_ad_enabled"),
        "density": (run_sh("wm density", serial=serial)[1].strip() if run_sh("wm density", serial=serial)[0] == 0 else ""),
    }


def do_tweak(action, params, serial):
    """执行一个调优动作，返回 (ok, message)。ok 为 'screencap' 等特殊情况时 message 为设备内路径。"""
    def sh(cmd):
        return run_sh(cmd, serial=serial)
    def sp(ns, k, v):
        return put_setting(ns, k, v, serial)
    def perm(op, pkg, permission):
        cmd = ["shell", "pm", op, pkg, permission]
        rc, _, err = run_adb(cmd, serial=serial)
        return rc, err
    try:
        if action == "force_dark":
            sp("global", "force_dark", "1" if params.get("enable") else "0")
        elif action == "sensors_off":
            sp("secure", "sensors_off", "1" if params.get("enable") else "0")
        elif action == "font_scale":
            sp("system", "font_scale", str(params.get("value", 1.0)))
        elif action == "background_limit":
            v = params.get("value", 3)
            sp("global", "background_process_limit", "null" if (v in (0, "0", None)) else str(v))
        elif action == "ota_disable":
            sp("global", "ota_disable_automatic_update", "1" if params.get("disable") else "0")
        elif action == "personalized_ad":
            sp("global", "personalized_ad_enabled", "0" if params.get("disable") else "1")
        elif action == "power_mode":
            sp("system", "power_mode", params.get("mode", "middle"))
        elif action == "absolute_volume":
            sp("global", "disable_absolute_volume", "1" if params.get("disable") else "0")
        elif action == "private_dns":
            mode = params.get("mode", "off")
            sp("global", "private_dns_mode", mode)
            if mode == "hostname":
                sp("global", "private_dns_specifier", params.get("specifier", ""))
        elif action == "refresh_rate":
            if params.get("min") is not None and params.get("min") != "":
                sp("system", "min_refresh_rate", str(params["min"]))
            if params.get("max") is not None and params.get("max") != "":
                sp("system", "peak_refresh_rate", str(params["max"]))
        elif action == "permission":
            rc, err = perm(params.get("op", "revoke"), params["pkg"], params["perm"])
            if rc != 0:
                return False, (err or "权限操作失败（请确认该 App 声明了此权限）")
        elif action == "special_perm":
            rc, err = perm(params.get("op", "grant"), params["pkg"], params["perm"])
            if rc != 0:
                return False, (err or "特殊权限操作失败（App 可能未声明该权限，或该权限需系统/签名级）")
        elif action == "notification":
            rc, _, err = sh("appops set %s POST_NOTIFICATIONS %s" % (params["pkg"], params.get("op", "deny")))
            if rc != 0:
                return False, (err or "通知设置失败")
        elif action == "wm_density":
            if params.get("reset"):
                sh("wm density reset")
            elif params.get("value"):
                sh("wm density %s" % params["value"])
        elif action == "wm_size":
            if params.get("reset"):
                sh("wm size reset")
            elif params.get("value"):
                sh("wm size %s" % params["value"])
        elif action == "disable_ads":
            sp("global", "personalized_ad_enabled", "0")
            # 停用广告相关系统组件（失败不致命）
            sh("pm disable-user --user 0 com.miui.msa.global 2>/dev/null")
            sh("pm disable-user --user 0 com.miui.daemon 2>/dev/null")
            return True, "已关闭个性化广告推荐，并停用 MSA / MiuiDaemon 广告服务（如需彻底清除可到包名管理器卸载广告包）"
        elif action == "svc":
            svc = params.get("svc")
            verb = "enable" if params.get("enable", True) else "disable"
            if svc in ("wifi", "data", "airplane"):
                sh("svc %s %s" % (svc, verb))
            else:
                return False, "未知的 svc 类型"
        elif action == "input_tap":
            sh("input tap %s %s" % (params["x"], params["y"]))
        elif action == "input_swipe":
            sh("input swipe %s %s %s %s %s" % (params["x1"], params["y1"], params["x2"], params["y2"], params.get("dur", 300)))
        elif action == "input_text":
            txt = (params.get("text", "") or "").replace(" ", "%s")
            sh("input text %s" % txt)
        elif action == "input_key":
            sh("input keyevent %s" % params.get("key", "KEYCODE_HOME"))
        elif action == "screencap":
            rc, _, err = sh("screencap -p /sdcard/_adbcap.png")
            if rc != 0:
                return False, (err or "截图失败")
            return "screencap", "/sdcard/_adbcap.png"
        elif action == "screenrecord":
            rc, _, err = sh("screenrecord --time-limit %s /sdcard/_adbrecord.mp4" % params.get("limit", 10))
            if rc != 0:
                return False, (err or "录屏失败")
            return "screenrecord", "/sdcard/_adbrecord.mp4"
        else:
            return False, "未知 action: %s" % action
        return True, "操作成功"
    except Exception as e:
        return False, str(e)


# ================= 新增功能：监控 / 文件 / 无线 / 宏 / 定时 / 预设 =================
import time as _time
import re as _re

DATA_DIR = os.path.join(BASE_DIR, "data")
MACRO_DIR = os.path.join(DATA_DIR, "macros")
os.makedirs(MACRO_DIR, exist_ok=True)
SCHED_FILE = os.path.join(DATA_DIR, "schedules.json")
PRESET_FILE = os.path.join(DATA_DIR, "presets.json")
FILE_DIR = os.path.join(BASE_DIR, "phone-files")
os.makedirs(FILE_DIR, exist_ok=True)
UPLOAD_DIR = os.path.join(FILE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

PERF_LAST = {}
SCHEDULES = []

DANGEROUS_PERMS = {
    "android.permission.READ_CALENDAR", "android.permission.WRITE_CALENDAR",
    "android.permission.CAMERA", "android.permission.READ_CONTACTS", "android.permission.WRITE_CONTACTS",
    "android.permission.GET_ACCOUNTS", "android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.RECORD_AUDIO", "android.permission.READ_PHONE_STATE", "android.permission.READ_PHONE_NUMBERS",
    "android.permission.CALL_PHONE", "android.permission.READ_CALL_LOG", "android.permission.WRITE_CALL_LOG",
    "android.permission.READ_EXTERNAL_STORAGE", "android.permission.WRITE_EXTERNAL_STORAGE", "android.permission.SEND_SMS",
    "android.permission.RECEIVE_SMS", "android.permission.READ_SMS", "android.permission.BODY_SENSORS",
    "android.permission.PROCESS_OUTGOING_CALLS", "android.permission.RECEIVE_MMS", "android.permission.READ_MEDIA_IMAGES",
    "android.permission.READ_MEDIA_VIDEO", "android.permission.READ_MEDIA_AUDIO", "android.permission.ACCESS_BACKGROUND_LOCATION",
}

BUILTIN_PRESETS = {
    "省电模式": [
        {"action": "background_limit", "value": 2},
        {"action": "power_mode", "mode": "low"},
        {"action": "ota_disable", "disable": True},
    ],
    "游戏性能": [
        {"action": "background_limit", "value": 0},
        {"action": "power_mode", "mode": "high-performance"},
        {"action": "refresh_rate", "min": "60", "max": "120"},
    ],
    "护眼夜间": [
        {"action": "force_dark", "enable": True},
        {"action": "font_scale", "value": 1.1},
    ],
    "纯净隐私": [
        {"action": "personalized_ad", "disable": True},
        {"action": "sensors_off", "enable": True},
        {"action": "disable_ads"},
    ],
}


def _load_json_file(p, default):
    try:
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _save_json_file(p, obj):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def shell_quote(s):
    return '"' + (s or "") + '"'


# ---- 文件管理 ----
def fs_list(path, serial=None):
    path = path or "/sdcard"
    rc, out, err = run_sh("ls -la " + shell_quote(path), serial=serial)
    if rc != 0:
        return None, err or "列出目录失败"
    entries = []
    for ln in out.splitlines():
        ln = ln.rstrip()
        if not ln or ln.lower().startswith("total "):
            continue
        m = _re.match(r"^([\-dl])([rwxsST\-]{9})[ \t]+([0-9]+)[ \t]+\S+[ \t]+\S+[ \t]+([0-9]+)[ \t]+([0-9\-]+ [0-9:]+)[ \t]+(.+)$", ln)
        if not m:
            continue
        perm, _, _, size, mtime, name = m.groups()
        if name in (".", ".."):
            continue
        entries.append({"name": name, "is_dir": perm.startswith("d"), "size": int(size), "mtime": mtime, "perm": perm})
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries, path


def fs_pull(src, serial=None):
    name = src.rstrip("/").split("/")[-1] or "file"
    dst = os.path.join(FILE_DIR, name)
    rc, _, err = run_adb(["pull", src, dst], serial=serial)
    if rc != 0:
        return None, err or "拉取失败"
    return name, dst


def fs_push(local_name, dst, serial=None):
    src = os.path.join(UPLOAD_DIR, os.path.basename(local_name))
    if not os.path.isfile(src):
        return False, "本地文件不存在，请先上传"
    rc, _, err = run_adb(["push", src, dst], serial=serial)
    if rc != 0:
        return False, err or "推送失败"
    return True, "已推送 " + dst


# ---- 无线 ADB ----
def wireless_status():
    devs = get_devices()
    tcp = [d for d in devs if ":" in d["serial"]]
    usb = [d for d in devs if ":" not in d["serial"]]
    return {"ok": True, "tcp": tcp, "usb": usb}


def wireless_enable(port=5555, serial=None):
    rc, out, err = run_adb(["tcpip", str(port)], serial=serial)
    if rc != 0:
        return False, err or "开启无线调试失败（需先通过 USB 连接）"
    return True, "已切换到 TCP/IP 模式（端口 %d）。请在手机「无线调试」中查看 IP，再点「连接」。" % port


def wireless_connect(endpoint, serial=None):
    rc, out, err = run_adb(["connect", endpoint], serial=serial)
    if rc != 0:
        return False, err or "连接失败"
    return True, (out or "已连接") + "（首次需先在手机无线调试里配对）"


def wireless_disconnect(endpoint="", serial=None):
    rc, out, err = run_adb(["disconnect"] + ([endpoint] if endpoint else []), serial=serial)
    if rc != 0:
        return False, err or "断开失败"
    return True, out or "已断开"


# ---- 电池 / 温度 ----
def battery_info(serial=None):
    info = {}
    rc, out, _ = run_sh("dumpsys battery", serial=serial)
    if rc == 0:
        for ln in out.splitlines():
            ln = ln.strip()
            for key in ("level", "scale", "temperature", "voltage", "status", "health", "technology", "ACpowered", "USBpowered", "present"):
                if ln.lower().startswith(key.lower() + ":"):
                    info[key] = ln.split(":", 1)[1].strip()
        if "temperature" in info:
            try:
                info["temp_c"] = "%.1f C" % (int(info["temperature"]) / 10)
            except Exception:
                pass
        if "voltage" in info:
            try:
                info["voltage_v"] = "%.3f V" % (int(info["voltage"]) / 1000)
            except Exception:
                pass
    rc2, out2, _ = run_sh("dumpsys thermalservice", serial=serial)
    temps = []
    if rc2 == 0:
        for ln in out2.splitlines():
            ln = ln.strip()
            m = _re.search(r"([0-9]+\.[0-9]+|[0-9]+)[ \t]*deg", ln)
            if m:
                temps.append(m.group(1) + " C")
    info["thermal"] = temps[:8]
    return info


# ---- 实时性能 ----
def perf_info(serial=None):
    now = _time.time()
    mem_total = mem_avail = mem_free = None
    rc, out, _ = run_sh("cat /proc/meminfo", serial=serial)
    if rc == 0:
        for ln in out.splitlines():
            if ln.startswith("MemTotal:"):
                mem_total = int(ln.split()[1])
            elif ln.startswith("MemAvailable:"):
                mem_avail = int(ln.split()[1])
            elif ln.startswith("MemFree:"):
                mem_free = int(ln.split()[1])
    cpu_top = []
    cpu_total = None
    rc, out, _ = run_sh("dumpsys cpuinfo", serial=serial)
    if rc == 0:
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.startswith("TOTAL"):
                m = _re.search(r"([0-9.]+)%", ln)
                if m:
                    cpu_total = float(m.group(1))
            else:
                m = _re.match(r"([0-9.]+)%[ \t]+(.+)$", ln)
                if m and "TOTAL" not in ln:
                    cpu_top.append({"pct": float(m.group(1)), "name": m.group(2).strip()})
        cpu_top.sort(key=lambda x: -x["pct"])
        cpu_top = cpu_top[:5]
    rx = tx = 0
    rc, out, _ = run_sh("cat /proc/net/dev", serial=serial)
    if rc == 0:
        for ln in out.splitlines()[2:]:
            parts = ln.split(":")
            if len(parts) != 2:
                continue
            vals = parts[1].split()
            if len(vals) >= 9:
                rx += int(vals[0])
                tx += int(vals[1])
    prev = PERF_LAST.get(serial)
    rx_rate = tx_rate = 0.0
    if prev:
        dt = now - prev[0]
        if dt > 0:
            rx_rate = max(0.0, (rx - prev[1]["rx"]) / dt)
            tx_rate = max(0.0, (tx - prev[1]["tx"]) / dt)
    PERF_LAST[serial] = (now, {"rx": rx, "tx": tx})
    mem_used = (mem_total - mem_avail) if (mem_total and mem_avail is not None) else None
    mem_pct = round(mem_used / mem_total * 100) if (mem_total and mem_used is not None and mem_total > 0) else None
    return {
        "mem_total_mb": round(mem_total / 1024) if mem_total else None,
        "mem_used_mb": round(mem_used / 1024) if mem_used is not None else None,
        "mem_pct": mem_pct,
        "cpu_total": cpu_total,
        "cpu_top": cpu_top,
        "net_rx_kbps": round(rx_rate / 1024, 1),
        "net_tx_kbps": round(tx_rate / 1024, 1),
    }


# ---- 权限审计 ----
def perm_audit(serial=None):
    rc, out, _ = run_adb(["shell", "pm", "list", "packages", "-3"], serial=serial)
    if rc != 0:
        return []
    pkgs = [l.split("package:", 1)[1].strip() for l in out.splitlines() if l.strip().startswith("package:")]
    results = []
    for pkg in pkgs:
        try:
            rc2, o2, _ = run_sh("dumpsys package %s" % pkg, serial=serial, timeout=20)
            if rc2 != 0:
                continue
            perms = []
            capture = False
            for ln in o2.splitlines():
                s = ln.strip()
                if s.startswith("requested permissions:"):
                    capture = True
                    continue
                if capture:
                    if s.endswith(":") or s.startswith("install permissions:") or s.startswith("runtime permissions:"):
                        break
                    if s.startswith("android.permission."):
                        p = s.split(":")[0].strip()
                        if p in DANGEROUS_PERMS:
                            perms.append(p)
            if perms:
                results.append({"pkg": pkg, "name": PKG_DB.get(pkg, {}).get("name", ""), "perms": perms})
        except Exception:
            continue
    return results


# ---- 设备健康报告 ----
def device_report(serial=None):
    info = device_info(serial)
    bat = battery_info(serial)
    rc, out, _ = run_sh("df -h /data", serial=serial)
    storage = {}
    if rc == 0:
        lines = [l for l in out.splitlines() if "/" in l]
        if lines:
            p = lines[0].split()
            if len(p) >= 5:
                storage = {"size": p[1], "used": p[2], "avail": p[3], "pct": p[4]}
    big = []
    rc, out, _ = run_sh("du -sh /sdcard/* 2>/dev/null | sort -rh | head -8", serial=serial)
    if rc == 0:
        for ln in out.splitlines():
            parts = ln.split("\t")
            if len(parts) >= 2:
                big.append({"size": parts[0], "path": parts[1]})
    tweaks = tweak_state(serial)
    rc, out, _ = run_adb(["shell", "pm", "list", "packages", "-3"], serial=serial)
    user_apps = len([l for l in out.splitlines() if l.strip().startswith("package:")])
    rc, out, _ = run_adb(["shell", "pm", "list", "packages", "-s"], serial=serial)
    sys_apps = len([l for l in out.splitlines() if l.strip().startswith("package:")])
    return {
        "info": info, "battery": bat, "storage": storage,
        "big_dirs": big, "tweaks": tweaks,
        "user_apps": user_apps, "sys_apps": sys_apps,
        "generated_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---- 宏 ----
def macro_list():
    items = []
    try:
        for f in os.listdir(MACRO_DIR):
            if f.endswith(".json"):
                d = _load_json_file(os.path.join(MACRO_DIR, f), {})
                items.append({"name": f[:-5], "steps": d.get("steps", []), "desc": d.get("desc", "")})
    except Exception:
        pass
    return items


def macro_save(name, steps, desc=""):
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in (name or "")).strip()
    if not safe:
        safe = "macro"
    _save_json_file(os.path.join(MACRO_DIR, safe + ".json"), {"name": safe, "desc": desc, "steps": steps})
    return True


def macro_delete(name):
    p = os.path.join(MACRO_DIR, name + ".json")
    if os.path.isfile(p):
        os.remove(p)
        return True
    return False


def macro_run(name, serial=None):
    d = _load_json_file(os.path.join(MACRO_DIR, name + ".json"), {})
    steps = d.get("steps", [])
    log = []
    for s in steps:
        act = s.get("action")
        try:
            ok, msg = do_tweak(act, s, serial)
            log.append({"step": act, "ok": ok is True or ok in ("screencap", "screenrecord"), "msg": msg})
        except Exception as e:
            log.append({"step": act, "ok": False, "msg": str(e)})
        _time.sleep(float(s.get("delay", 0.3)))
    return log


# ---- 定时任务 ----
def schedule_load():
    global SCHEDULES
    SCHEDULES = _load_json_file(SCHED_FILE, [])
    return SCHEDULES


def schedule_add(entry):
    global SCHEDULES
    SCHEDULES = _load_json_file(SCHED_FILE, [])
    SCHEDULES.append(entry)
    _save_json_file(SCHED_FILE, SCHEDULES)
    return True


def schedule_delete(name):
    global SCHEDULES
    SCHEDULES = [s for s in _load_json_file(SCHED_FILE, []) if s.get("name") != name]
    _save_json_file(SCHED_FILE, SCHEDULES)
    return True


def schedule_tick():
    global SCHEDULES
    now = _time.localtime()
    hhmm = "%02d:%02d" % (now.tm_hour, now.tm_min)
    dirty = False
    for s in SCHEDULES:
        try:
            typ = s.get("type")
            due = False
            if typ == "daily":
                if s.get("time") == hhmm and s.get("last") != hhmm:
                    due = True
                    s["last"] = hhmm
                    dirty = True
            elif typ == "interval":
                last = s.get("last_ts", 0)
                if _time.time() - last >= int(s.get("minutes", 60)) * 60:
                    due = True
                    s["last_ts"] = _time.time()
                    dirty = True
            if due:
                act = s.get("action", {})
                serial = pick_serial()
                if serial and act:
                    if act.get("kind") == "macro":
                        macro_run(act.get("name"), serial)
                    else:
                        do_tweak(act.get("action"), act, serial)
        except Exception:
            pass
    if dirty:
        _save_json_file(SCHED_FILE, SCHEDULES)


def scheduler_loop():
    while True:
        try:
            schedule_tick()
        except Exception:
            pass
        _time.sleep(30)


# ---- 预设方案 ----
def preset_list():
    user = _load_json_file(PRESET_FILE, [])
    return {"builtin": [{"name": k, "steps": v} for k, v in BUILTIN_PRESETS.items()], "user": user}


def preset_apply(steps, serial=None):
    log = []
    for st in steps:
        try:
            ok, msg = do_tweak(st.get("action"), st, serial)
            log.append({"step": st.get("action"), "ok": ok is True, "msg": msg})
        except Exception as e:
            log.append({"step": st.get("action"), "ok": False, "msg": str(e)})
    return log


def preset_save(name, steps):
    user = _load_json_file(PRESET_FILE, [])
    user = [u for u in user if u.get("name") != name]
    user.append({"name": name, "steps": steps})
    _save_json_file(PRESET_FILE, user)
    return True


def preset_delete(name):
    user = [u for u in _load_json_file(PRESET_FILE, []) if u.get("name") != name]
    _save_json_file(PRESET_FILE, user)
    return True


# ---- 上传解析（multipart）----
def _parse_upload(headers, body):
    ct = headers.get("Content-Type", "") or headers.get("content-type", "")
    m = _re.search(r"boundary=([^;]+)", ct)
    if not m:
        return None, None
    boundary = m.group(1).strip().strip('"')
    parts = body.split(("--" + boundary).encode())
    for part in parts:
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        if b"filename=" in head:
            fn = _re.search(rb'filename="([^"]*)"', head)
            if fn:
                name = fn.group(1).decode("utf-8", "ignore")
                if data.endswith(b"\r\n"):
                    data = data[:-2]
                return name, data
    return None, None


def main():
    schedule_load()
    try:
        threading.Thread(target=scheduler_loop, daemon=True).start()
    except Exception:
        pass
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("=" * 50)
    print("ADB 手机优化助手已启动")
    print("本机 adb 路径: %s" % ADB)
    print("请在浏览器打开: http://%s:%d" % (HOST, PORT))
    print("按 Ctrl+C 停止")
    print("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
