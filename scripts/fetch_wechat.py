#!/usr/bin/env python3
"""fetch_wechat.py — 通过搜狗微信搜索抓取公众号最新文章（无需扫码登录）。

用法:
    python3 scripts/fetch_wechat.py [--accounts accounts.json]
        [--out wechat_items.json] [--state wechat_state.json]
        [--per-account 3] [--max-pages 1]

流程:
    1. 读取公众号清单（accounts.json）
    2. 逐账号请求搜狗微信文章搜索（type=2），解析标题/摘要/时间/跳转链
    3. 跟踪搜狗跳转链解析出 mp.weixin.qq.com 真实链接（搜狗中转链有时效，必须实时解析）
    4. 输出 wechat_items.json（与 aihot items 兼容的 schema）
    5. 更新 wechat_state.json（连续失败计数，供 workflow 断流告警使用）

反爬说明:
    - 可携带搜狗 Cookie（环境变量 SOGOU_COOKIE），大幅降低 Actions IP 被风控概率
    - 账号间随机延迟 1-3 秒，避免集中请求
    - 检测到验证码/antispider 页面时计为失败并在日志提示 Cookie 可能过期

纯标准库实现，Windows / Linux / macOS 均可运行。
"""
import argparse
import hashlib
import html
import http.cookiejar
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SEARCH_URL = "https://weixin.sogou.com/weixin?type=2&ie=utf8&query={q}"  # tsn 时间过滤参数实测无效，改用脚本侧时间窗口过滤
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 搜索结果条目块：<div class="txt-box"> ... </div>
RE_ITEM_BLOCK = re.compile(r'<div class="txt-box">.*?</div>\s*</div>', re.S)
RE_TITLE_LINK = re.compile(r'<h3>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
RE_SUMMARY = re.compile(r'<p class="txt-info">(.*?)</p>', re.S)
RE_ACCOUNT = re.compile(r'<a class="account"[^>]*>(.*?)</a>', re.S)
RE_TIMESTAMP = re.compile(r"timeConvert\('(\d+)'\)")
RE_STRIP_TAG = re.compile(r"<[^>]+>")


SESSION_OPENER = None  # 带 Cookie 会话（全局单例）


def build_session(cookie: str = ""):
    """建立带搜狗会话 Cookie 的请求会话。

    先访问搜狗首页获取匿名会话 Cookie（SUV/SNUID/SUID 等，实测足以通过
    搜索与跳转链反爬，无需登录搜狗账号），后续请求自动携带。
    若传入 SOGOU_COOKIE 则优先使用（登录态 Cookie 更稳固）。
    """
    global SESSION_OPENER
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        opener.open(urllib.request.Request("https://weixin.sogou.com/", headers=headers), timeout=20)
    except Exception as exc:  # noqa: BLE001 - 首页预热失败不阻断，后续请求仍会尝试
        print(f"搜狗首页预热失败（不影响后续尝试）: {exc}", file=sys.stderr)
    SESSION_OPENER = opener
    return opener


def http_get(url: str, cookie: str = "", referer: str = "", timeout: int = 25) -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if cookie:
        headers["Cookie"] = cookie
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    opener = SESSION_OPENER or urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def is_antispider(page: str) -> bool:
    return ("antispider" in page) or ("请输入验证码" in page) or ("验证码" in page and "txt-box" not in page)


def parse_results(page: str, account: str, per_account: int, since_ts: int) -> list[dict]:
    """解析搜狗文章搜索结果页。

    搜狗默认排序混有旧文章（tsn 时间过滤参数实测无效），因此在脚本侧按时间窗口
    （publishedAt >= since_ts）过滤，只保留近 N 天且账号归属匹配的文章。
    """
    out = []
    for block in RE_ITEM_BLOCK.findall(page):
        m = RE_TITLE_LINK.search(block)
        if not m:
            continue
        link, title = m.group(1), RE_STRIP_TAG.sub("", m.group(2)).strip()
        title = html.unescape(title)
        ms = RE_SUMMARY.search(block)
        summary = html.unescape(RE_STRIP_TAG.sub("", ms.group(1)).strip()) if ms else ""
        ma = RE_ACCOUNT.search(block)
        src_account = html.unescape(RE_STRIP_TAG.sub("", ma.group(1)).strip()) if ma else ""
        # 归属过滤：搜索「机器之心」可能混入提到该名的其他账号文章
        if src_account and account not in src_account and src_account not in account:
            continue
        mt = RE_TIMESTAMP.search(block)
        if not mt:
            continue
        ts = int(mt.group(1))
        if ts < since_ts:  # 早于时间窗口丢弃；搜狗按相关度排序（非严格时间序），不能提前 break
            continue
        published_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if link.startswith("//"):
            link = "https:" + link
        elif link.startswith("/"):
            link = "https://weixin.sogou.com" + link
        out.append({
            "title": title,
            "summary": summary,
            "sogouLink": html.unescape(link),
            "source": f"公众号：{src_account or account}",
            "publishedAt": published_iso,
        })
        if len(out) >= per_account:
            break
    return out


def parse_jump_url(page: str) -> str:
    """从搜狗跳转页解析真实文章链接。

    搜狗反爬把 URL 拆成片段用 JS 拼接：url += 'https://mp.'; url += 'weixin.qq.c'; ...
    需按顺序提取所有 url += '...' 片段并拼接。
    """
    parts = re.findall(r"url \+= '([^']*)'", page)
    if parts:
        url = "".join(parts)
        if url.startswith("http"):
            return url
    m = re.search(r"https?://mp\.weixin\.qq\.com/s[^\"'\s<>]*", page)
    if m:
        return html.unescape(m.group(0))
    return ""


def resolve_real_url(sogou_link: str, cookie: str = "") -> str:
    """跟踪搜狗跳转链，解析出 mp.weixin.qq.com 真实文章链接。

    搜狗中转链有时效（约数天到十几天），快照里必须存真实链接。
    跳转页为 JS 重定向（URL 拆片拼接），需携带搜狗会话 Cookie 访问。
    解析失败时退回原搜狗链接（可打开但有过期风险）。
    """
    if "mp.weixin.qq.com" in sogou_link:
        return sogou_link
    try:
        page = http_get(sogou_link, cookie=cookie, referer="https://weixin.sogou.com/")
        if is_antispider(page):
            print("    跳转链触发反爬（会话 Cookie 可能失效），保留搜狗链接", file=sys.stderr)
            return sogou_link
        real = parse_jump_url(page)
        if real and "mp.weixin.qq.com" in real:
            return real.split("@")[-1] if "@" in real else real
    except Exception as exc:  # noqa: BLE001 - 跳转解析失败不应中断整体抓取
        print(f"    跳转链解析失败（保留搜狗链接）: {exc}", file=sys.stderr)
    return sogou_link


def fetch_all(accounts: list[str], cookie: str, per_account: int, days: int) -> tuple[list[dict], int, int]:
    items, ok_count, fail_count = [], 0, 0
    since_ts = int(time.time()) - days * 86400
    for name in accounts:
        try:
            url = SEARCH_URL.format(q=urllib.parse.quote(name))
            page = http_get(url, cookie=cookie, referer="https://weixin.sogou.com/")
            if is_antispider(page):
                raise RuntimeError("触发搜狗反爬（验证码页），Cookie 可能过期或缺失")
            results = parse_results(page, name, per_account, since_ts)
            for r in results:
                r["url"] = resolve_real_url(r["sogouLink"], cookie)
                r["id"] = "wechat:" + hashlib.md5(r["url"].encode("utf-8")).hexdigest()[:12]
                time.sleep(random.uniform(0.3, 0.8))
            items.extend(results)
            ok_count += 1
            print(f"  [OK] {name}: {len(results)} 篇")
        except Exception as exc:  # noqa: BLE001 - 单账号失败不影响其他账号
            fail_count += 1
            print(f"  [FAIL] {name}: {exc}", file=sys.stderr)
        time.sleep(random.uniform(1.0, 3.0))
    return items, ok_count, fail_count


def main() -> int:
    parser = argparse.ArgumentParser(description="通过搜狗微信搜索抓取公众号文章")
    parser.add_argument("--accounts", default="accounts.json")
    parser.add_argument("--out", default="wechat_items.json")
    parser.add_argument("--state", default="wechat_state.json")
    parser.add_argument("--per-account", type=int, default=3)
    parser.add_argument("--days", type=int, default=7, help="只保留近 N 天文章（默认 7）")
    args = parser.parse_args()

    try:
        with open(args.accounts, "r", encoding="utf-8") as f:
            accounts = [a["name"] for a in json.load(f)["accounts"]]
    except Exception as exc:  # noqa: BLE001
        print(f"读取公众号清单失败: {exc}", file=sys.stderr)
        return 1

    cookie = os.environ.get("SOGOU_COOKIE", "")
    build_session(cookie)  # 无 SOGOU_COOKIE 时自动用匿名会话 Cookie（首页预热获取）
    print(f"开始抓取 {len(accounts)} 个公众号（Cookie: {'登录态' if cookie else '匿名会话'}，窗口: 近 {args.days} 天）")
    items, ok_count, fail_count = fetch_all(accounts, cookie, args.per_account, args.days)

    now = datetime.now(timezone.utc)
    # 读取既有 state，更新连续失败计数
    state = {"consecutive_failures": 0, "last_success_at": None}
    try:
        with open(args.state, "r", encoding="utf-8") as f:
            state.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass

    success = ok_count > 0 and len(items) > 0
    if success:
        state["consecutive_failures"] = 0
        state["last_success_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    state["last_run_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    state["last_ok_accounts"] = ok_count
    state["last_fail_accounts"] = fail_count
    state["last_items"] = len(items)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"fetchedAt": state["last_run_at"], "ok": success, "items": items},
                  f, ensure_ascii=False, indent=1)
    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)

    print(f"完成：成功账号 {ok_count}/{len(accounts)}，共 {len(items)} 篇文章，"
          f"连续失败计数 {state['consecutive_failures']}")
    if not success:
        print("警告：本次公众号源全部失败（若持续失败将触发 Issue 告警）", file=sys.stderr)
    return 0  # 不阻断 workflow：公众号源失败时快照照常发布（降级）


if __name__ == "__main__":
    sys.exit(main())
