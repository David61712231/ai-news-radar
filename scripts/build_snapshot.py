#!/usr/bin/env python3
"""build_snapshot.py — 抓取 aihot 公开 API，生成 AI HOT 仪表盘静态快照（单文件 HTML）。

用法:
    python3 scripts/build_snapshot.py [--out public/index.html]
        [--template templates/index.template.html]
        [--api-base https://aihot.virxact.com]
        [--days 7]

流程:
    1. 分页抓取 /api/public/items（扁平条目流，天然去重）
    2. 日报 = 最近有内容的北京日期当天条目；周报 = 该日期前 N 天
    3. 按六版块分组、全局连续编号、北京时间人话时间
    4. 用模板渲染出单文件 HTML（DATA 内嵌）

纯标准库实现，Windows / Linux / macOS 均可运行。
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# 六版块固定顺序（与前端 SECTION_COLORS 对应）
SECTIONS = ["模型发布/更新", "产品发布/更新", "AI泛娱乐新闻", "行业动态", "论文研究", "技巧与观点"]

# API category -> 六版块
CATEGORY_MAP = {
    "ai-models": "模型发布/更新",
    "ai-products": "产品发布/更新",
    "industry": "行业动态",
    "paper": "论文研究",
    "tip": "技巧与观点",
}

BJ = timezone(timedelta(hours=8), name="Asia/Shanghai")
WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

MAX_PAGES = 20  # 分页上限（每页 50 条），防止死循环


def fetch_items(api_base: str, since_bj: datetime) -> list[dict]:
    """分页抓取 items，直到覆盖 since_bj 之前的条目或翻完为止。

    注意：API 的翻页参数是 cursor（实测 nextCursor 会重复返回第一页）。
    """
    items: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        params = {"limit": "50"}
        if cursor:
            params["cursor"] = cursor
        url = f"{api_base}/api/public/items?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        batch = data.get("items") or []
        items.extend(batch)
        if not data.get("hasNext") or not data.get("nextCursor"):
            break
        cursor = data["nextCursor"]
        # 本页最旧条目已早于窗口起点，无需继续翻页
        oldest = min((i.get("publishedAt") or "" for i in batch), default="")
        if oldest and to_bj(oldest) < since_bj:
            break
    return items


def to_bj(iso: str) -> datetime:
    """ISO8601 -> 北京时间 datetime（无法解析时返回遥远的过去）。"""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(BJ)
    except (ValueError, AttributeError):
        return datetime(2000, 1, 1, tzinfo=BJ)


def fmt_date(d: datetime) -> str:
    return f"{d.year}年{d.month}月{d.day}日 {WEEKDAYS[d.weekday()]}"


def fmt_time_text(dt: datetime, today: datetime) -> str:
    """北京时间人话：今天 HH:MM / 昨天 HH:MM / M/D HH:MM"""
    day_diff = (today.date() - dt.date()).days
    hm = dt.strftime("%H:%M")
    if day_diff == 0:
        return f"今天 {hm}"
    if day_diff == 1:
        return f"昨天 {hm}"
    return f"{dt.month}/{dt.day} {hm}"


def build_item(raw: dict, num: int, today: datetime) -> dict:
    """API item -> 模板 item 结构。"""
    published = to_bj(raw.get("publishedAt") or "")
    category = CATEGORY_MAP.get(raw.get("category") or "", "行业动态")
    url = raw.get("url") or raw.get("permalink") or ""
    source = raw.get("source") or raw.get("attribution", {}).get("source") or "AI HOT"
    return {
        "id": f"aihot:{raw.get('id')}",
        "title": raw.get("title") or "",
        "summary": raw.get("summary") or "",
        "url": url,
        "source": source,
        "sourceType": "aihot",
        "category": category,
        "publishedAt": raw.get("publishedAt") or "",
        "mpName": None,
        "num": num,
        "timeText": fmt_time_text(published, today),
    }


def group_sections(items: list[dict]) -> list[dict]:
    """按六版块固定顺序分组。"""
    grouped = {s: [] for s in SECTIONS}
    for it in items:
        grouped.setdefault(it["category"], []).append(it)
    return [
        {"label": s, "count": len(grouped[s]), "items": grouped[s]}
        for s in SECTIONS
    ]


def build_view(view: str, items: list[dict], day: datetime, days: int, generated_at: datetime) -> dict:
    """组装 daily / weekly 视图。items 需已按 publishedAt 降序。"""
    start = day - timedelta(days=days - 1)
    end = day + timedelta(days=1)
    raw = [i for i in items if start <= to_bj(i.get("publishedAt") or "") < end]
    raw.sort(key=lambda i: to_bj(i.get("publishedAt") or ""), reverse=True)
    converted = [build_item(it, idx + 1, day) for idx, it in enumerate(raw)]
    sections = group_sections(converted)
    return {
        "view": view,
        "range": {
            "start": start.date().isoformat(),
            "end": day.date().isoformat(),
            "label": f"{fmt_date(start)} 至 {fmt_date(day)}" if view == "weekly" else fmt_date(day),
        },
        "total": len(converted),
        "lead": None,
        "sections": sections,
        "stats": [{"label": s["label"], "count": s["count"]} for s in sections],
        "mpStatus": {
            "connected": False,
            "note": "未检测到本地公众号聚合 RSS（URLError），仅显示 aihot 数据",
        },
        "generatedAt": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def render(template_path: str, out_path: str, data: dict) -> None:
    """读模板，替换 DATA 占位符，输出快照 HTML。"""
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()
    json_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # 注意：不能用 re.sub 替换，替换串里的 \n 会被 re 解释成换行；用 str.replace 最安全
    placeholder = "const DATA = __DATA__;"
    if placeholder not in html:
        raise RuntimeError(f"模板中未找到 DATA 占位符（{template_path}）")
    new_html = html.replace(placeholder, f"const DATA = {json_str};", 1)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(new_html)
    print(f"已生成 {out_path}（{len(new_html)} 字节）")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 AI HOT 仪表盘静态快照")
    parser.add_argument("--out", default="public/index.html")
    parser.add_argument("--template", default="templates/index.template.html")
    parser.add_argument("--api-base", default="https://aihot.virxact.com")
    parser.add_argument("--days", type=int, default=7, help="周报窗口天数（默认 7）")
    args = parser.parse_args()

    now_bj = datetime.now(BJ)
    try:
        items = fetch_items(args.api_base, now_bj - timedelta(days=args.days + 2))
    except Exception as exc:  # noqa: BLE001 - 抓取失败给出可读错误
        print(f"抓取失败: {exc}", file=sys.stderr)
        return 1
    if not items:
        print("抓取结果为空，放弃生成", file=sys.stderr)
        return 1

    # 按 publishedAt 降序，去重（同 id 保留最新）
    items.sort(key=lambda i: to_bj(i.get("publishedAt") or ""), reverse=True)
    seen: set[str] = set()
    deduped = []
    for i in items:
        key = i.get("id") or i.get("permalink") or ""
        if key in seen:
            continue
        seen.add(key)
        deduped.append(i)
    items = deduped

    latest_day = to_bj(items[0].get("publishedAt") or "").date()
    generated_at = datetime.now(timezone.utc)
    data = {
        "daily": build_view("daily", items, datetime.combine(latest_day, datetime.min.time(), tzinfo=BJ), 1, generated_at),
        "weekly": build_view("weekly", items, datetime.combine(latest_day, datetime.min.time(), tzinfo=BJ), args.days, generated_at),
    }
    render(args.template, args.out, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
