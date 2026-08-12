#!/usr/bin/env python3
"""refresh_cookie.py — SOGOU_COOKIE 验证/更新助手（已降级为可选工具）。

重要变更（2026-08-12）:
    fetch_wechat.py 已支持自动获取搜狗匿名会话 Cookie（首页预热），实测足以
    通过搜索与跳转链反爬并解析出微信真实链接，因此正常情况下**无需配置
    SOGOU_COOKIE**。本脚本仅用于：
    - 排查抓取问题时验证当前 Cookie/匿名会话是否仍有效（--test）
    - 搜狗风控升级时，手动提供登录态 Cookie 作为加固手段

用法:
    python scripts/refresh_cookie.py            # 交互式粘贴 Cookie 并验证
    python scripts/refresh_cookie.py --test     # 验证本地已保存的 Cookie 是否仍有效
    python scripts/refresh_cookie.py --clear    # 清除本地保存的 Cookie

纯标准库实现。
"""
import argparse
import getpass
import json
import os
import sys
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
COOKIE_FILE = os.path.join(REPO_ROOT, ".sogou_cookie")
SECRET_URL = "https://github.com/WadeLiuAstro/AI-HOT/settings/secrets/actions/SOGOU_COOKIE"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def http_get(url: str, cookie: str = "", referer: str = "") -> str:
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def is_antispider(page: str) -> bool:
    return ("antispider" in page) or ("请输入验证码" in page) or ("验证码" in page and "txt-box" not in page)


def verify_cookie(cookie: str) -> tuple[bool, bool, str]:
    """用 Cookie 实测搜狗：返回 (搜索可用, 跳转链可解析, 说明)。"""
    # 1. 搜索页测试
    q = urllib.parse.quote("机器之心")
    page = http_get(f"https://weixin.sogou.com/weixin?type=2&ie=utf8&query={q}", cookie=cookie)
    if is_antispider(page):
        return False, False, "搜索页触发反爬：Cookie 无效或已过期"
    if "txt-box" not in page:
        return False, False, "搜索页无结果：页面结构异常"
    # 2. 跳转链解析测试（搜狗把 URL 拆片用 url += '...' 拼接，需按序拼接）
    import html as _html
    import re
    m = re.search(r'<h3>.*?<a[^>]+href="([^"]+)"', page, re.S)
    if not m:
        return True, False, "搜索正常，但未找到可测试的跳转链"
    link = _html.unescape(m.group(1)).strip()  # 还原 &amp; 等 HTML 实体
    if link.startswith("//"):
        link = "https:" + link
    elif link.startswith("/"):
        link = "https://weixin.sogou.com" + link
    try:
        jump_page = http_get(link, cookie=cookie, referer="https://weixin.sogou.com/")
        parts = re.findall(r"url \+= '([^']*)'", jump_page)
        if parts and "".join(parts).startswith("http"):
            return True, True, "搜索 + 跳转链解析均正常（可获取微信真实链接）"
        if "mp.weixin.qq.com" in jump_page:
            return True, True, "搜索 + 跳转链解析均正常（可获取微信真实链接）"
        if is_antispider(jump_page):
            return True, False, "搜索正常，但跳转链仍触发反爬（Cookie 权限不足）"
        return True, False, "搜索正常，跳转链页面结构无法识别"
    except Exception as exc:  # noqa: BLE001
        return True, False, f"搜索正常，跳转链请求失败: {exc}"


def load_saved() -> str:
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="SOGOU_COOKIE 一键更新助手")
    parser.add_argument("--test", action="store_true", help="验证本地已保存的 Cookie")
    parser.add_argument("--clear", action="store_true", help="清除本地保存的 Cookie")
    args = parser.parse_args()

    if args.clear:
        if os.path.exists(COOKIE_FILE):
            os.remove(COOKIE_FILE)
            print("已清除本地 Cookie 备份")
        return 0

    if args.test:
        cookie = load_saved()
        if not cookie:
            print("本地无已保存的 Cookie（运行不带参数的本脚本进行更新）")
            return 1
        ok_search, ok_jump, msg = verify_cookie(cookie)
        print(f"验证结果: {msg}")
        if not ok_jump:
            print("提示：Cookie 已失效或降级，请重新获取并更新")
            return 1
        return 0

    print("=" * 62)
    print("SOGOU_COOKIE 更新助手")
    print("=" * 62)
    print("获取步骤：")
    print("  1. 浏览器打开 https://weixin.sogou.com （建议先登录搜狗账号）")
    print("  2. 随便搜索一次，按 F12 打开开发者工具 → Network（网络）")
    print("  3. 点击列表中 weixin?type=2... 的请求 → Request Headers")
    print("  4. 复制 Cookie: 后面的整段值（很长的一串）")
    print("-" * 62)
    cookie = input("请粘贴 Cookie（输入时不回显）: ").strip() if sys.stdin.isatty() else ""
    if not cookie:
        try:
            cookie = getpass.getpass("请粘贴 Cookie: ").strip()
        except EOFError:
            cookie = ""
    if not cookie:
        print("未输入 Cookie，退出")
        return 1
    cookie = cookie.removeprefix("Cookie:").strip()

    print("\n正在验证 Cookie 有效性（实测搜狗搜索 + 跳转链）...")
    ok_search, ok_jump, msg = verify_cookie(cookie)
    print(f"验证结果: {msg}")
    if not ok_search:
        print("Cookie 无效，请重新获取（确认复制完整、搜狗账号已登录）")
        return 1

    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(cookie)
    print(f"\n已备份到本地: {COOKIE_FILE}（该文件已被 .gitignore 忽略，不会进仓库）")
    print("\n" + "=" * 62)
    print("最后一步：更新 GitHub Secret（约 1 分钟）")
    print(f"  1. 打开 {SECRET_URL}")
    print("  2. 点击 SOGOU_COOKIE 右侧的编辑（铅笔）图标")
    print("  3. 粘贴刚才的 Cookie 值 → Update secret")
    print("  4. 到 Actions 页手动 Run workflow 一次，公众号文章将解析出微信真实链接")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
