#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 用 GitHub 个人访问令牌（PAT）把本地项目推送到 GitHub 远程仓库。
#
# 适用场景：
#   1) 普通电脑（已装 git）：脚本优先用 git，令牌以「URL 内嵌」方式传入，不写入 .git/config。
#   2) 无 git 的沙箱/容器（如本 Agent 环境）：自动回退到纯 Python 的 dulwich 完成推送。
#
# 用法：
#   python push_with_token.py <仓库HTTPS地址> <PAT令牌> [--user <用户名>] [--dir <项目目录>] [--branch main]
#
# 令牌格式：
#   - 经典令牌 ghp_xxx：直接作为用户名            -> https://ghp_xxx@github.com/...
#   - 细粒度令牌 github_pat_xxx：用户名用 x-access-token -> https://x-access-token:github_pat_xxx@github.com/...
# 脚本会自动把令牌嵌入地址交给 git / dulwich 的 HTTP Basic 认证（控制台只显示脱敏后的地址）。
import os, sys, argparse, shutil, subprocess

def build_auth_url(base_url, token, username=None):
    """把令牌嵌入 HTTPS 地址，用于 Basic Auth。"""
    from urllib.parse import urlparse
    p = urlparse(base_url)
    if username == "x-access-token" or token.startswith("github_pat_"):
        user = "x-access-token"
    else:
        user = username or token  # 经典令牌：令牌即用户名
    netloc = "%s:%s@%s" % (user, token, p.netloc)
    return p._replace(netloc=netloc).geturl()

def _mask(url):
    """脱敏：隐藏地址里的令牌。"""
    return url.split("@")[-1] if "@" in url else url

def push_with_git(repo_dir, auth_url, branch, message):
    def run(*args):
        disp = [(_mask(a) if a == auth_url else a) for a in args]
        print("$", " ".join(disp))
        r = subprocess.run(args, cwd=repo_dir)
        if r.returncode != 0:
            raise RuntimeError("git 命令失败: " + " ".join(disp))
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        run("git", "init", "-q")
    run("git", "add", ".")
    st = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir,
                        capture_output=True, text=True)
    if st.stdout.strip():
        run("git", "commit", "-q", "-m", message)
    else:
        print("ℹ️  没有需要提交的改动，跳过提交。")
    run("git", "branch", "-M", branch)
    # 直接推送到「带令牌的地址」，不写入本地 config，避免令牌落地
    run("git", "push", auth_url, branch)

def push_with_dulwich(repo_dir, auth_url, branch, message):
    from dulwich.repo import Repo
    from dulwich.ignore import IgnoreFilterManager
    import dulwich.porcelain as p
    if os.path.exists(os.path.join(repo_dir, ".git")):
        repo = Repo(repo_dir)
    else:
        repo = Repo.init(repo_dir)
    mgr = IgnoreFilterManager.from_repo(repo)
    tos = []
    for cur, _, files in os.walk(repo_dir):
        if ".git" in cur.split(os.sep):
            continue
        for f in files:
            fp = os.path.join(cur, f)
            rel = os.path.relpath(fp, repo_dir)
            if rel.startswith(".git"):
                continue
            if mgr.is_ignored(rel):  # None/False 视为未忽略
                continue
            tos.append(rel)
    if not tos:
        print("ℹ️  没有需要提交的文件。")
        return
    p.add(repo, tos)
    cid = p.commit(repo, message=message.encode(),
                   author=b"ADB Optimizer <noreply@example.com>",
                   committer=b"ADB Optimizer <noreply@example.com>")
    # 确保本地目标分支指向该提交（dulwich 默认分支可能是 master）
    repo.refs[b"refs/heads/" + branch.encode()] = cid
    print("✅ 已提交 %s" % (cid.decode() if isinstance(cid, bytes) else cid))
    print("📤 推送到 GitHub (%s) ..." % _mask(auth_url))
    refspec = b"refs/heads/%s:refs/heads/%s" % (branch.encode(), branch.encode())
    p.push(repo, auth_url, [refspec])
    print("🎉 已推送到 %s" % _mask(auth_url))

def main():
    ap = argparse.ArgumentParser(description="用 PAT 把项目推送到 GitHub")
    ap.add_argument("repo_url", help="仓库 HTTPS 地址，如 https://github.com/<用户>/<仓库>.git")
    ap.add_argument("token", help="GitHub 个人访问令牌（PAT）")
    ap.add_argument("--user", default=None, help="令牌用户名（细粒度令牌填 x-access-token）")
    ap.add_argument("--dir", default=os.getcwd(), help="项目目录，默认当前目录")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default="Initial commit: ADB 设备优化助手（手机 + 墨水屏）")
    args = ap.parse_args()

    auth_url = build_auth_url(args.repo_url, args.token, args.user)
    if shutil.which("git"):
        print("🔧 检测到 git，使用 git 推送（令牌不落地）")
        push_with_git(args.dir, auth_url, args.branch, args.message)
    else:
        print("🔧 未检测到 git，回退到 dulwich 纯 Python 推送")
        push_with_dulwich(args.dir, auth_url, args.branch, args.message)

if __name__ == "__main__":
    main()
