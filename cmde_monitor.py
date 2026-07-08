# -*- coding: utf-8 -*-
"""
CMDE 审评进度监控 - GitHub Actions 版
监控 https://www.cmde.org.cn/xwdt/shpbg/index.html
检测新发布并通过钉钉推送通知。
状态文件保存在 state/cmde_state.json，每次运行后提交回仓库。
"""
import json
import hashlib
import time
import os
import sys
from pathlib import Path
from datetime import datetime

URL = "https://www.cmde.org.cn/xwdt/shpbg/index.html"
STATE_FILE = Path(__file__).parent / "state" / "cmde_state.json"


def send_dingtalk_text(webhook, content):
    """发送钉钉文本消息"""
    import urllib.request
    if "标准物质" not in content:
        content = "标准物质 | " + content
    msg = {"msgtype": "text", "text": {"content": content}}
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read().decode("utf-8"))
        print(f"  钉钉文本: {'OK' if result.get('errcode') == 0 else result.get('errmsg')}")


def send_dingtalk_link(webhook, title, message_url):
    """发送钉钉链接消息"""
    import urllib.request
    if "标准物质" not in title:
        title = "标准物质 | " + title
    msg = {"msgtype": "link", "link": {"title": title, "text": "CMDE 审评进度更新", "messageUrl": message_url, "picUrl": ""}}
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read().decode("utf-8"))
        print(f"  钉钉链接: {'OK' if result.get('errcode') == 0 else result.get('errmsg')}")


def fetch_cmde_announcements():
    """使用 Playwright 抓取审评进度页面"""
    from playwright.sync_api import sync_playwright

    print("  启动 Playwright / Chromium...")
    items = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-infobars"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print(f"  访问: {URL}")
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)

        links = page.query_selector_all("a")
        print(f"  找到 {len(links)} 个链接标签")

        for link in links:
            try:
                title = link.inner_text().strip()
                href = link.get_attribute("href")
                if not title or not href:
                    continue
                if len(title) < 8:
                    continue
                if href.startswith("http"):
                    full_url = href
                elif href.startswith("/"):
                    full_url = "https://www.cmde.org.cn" + href
                else:
                    full_url = "https://www.cmde.org.cn/xwdt/shpbg/" + href
                item_hash = hashlib.md5(f"{title}{full_url}".encode()).hexdigest()
                items.append({"title": title, "url": full_url, "hash": item_hash})
            except:
                continue

        browser.close()

    # 去重
    seen = set()
    unique = []
    for item in items:
        if item["hash"] not in seen:
            seen.add(item["hash"])
            unique.append(item)

    print(f"  共获取 {len(unique)} 条审评进度信息")
    return unique


def main():
    webhook = os.environ.get("DINGTALK_WEBHOOK")
    if not webhook:
        print("[FAIL] DINGTALK_WEBHOOK environment variable not set")
        sys.exit(1)

    print("=" * 60)
    print(f"CMDE 审评进度监控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 抓取
    print("\n[1/3] 抓取审评进度列表...")
    items = fetch_cmde_announcements()
    if not items:
        print("  未获取到数据")
        sys.exit(1)

    print(f"\n最新信息 (前5条):")
    for i, item in enumerate(items[:5], 1):
        print(f"  {i}. {item['title'][:60]}")

    # 读取上次状态
    print(f"\n[2/3] 检查更新...")
    last_hash = None
    last_title = None
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                last_hash = state.get("hash")
                last_title = state.get("title")
                print(f"  上次状态: {last_title}")
        except Exception as e:
            print(f"  读取状态失败: {e}")

    # 检测新信息
    new_items = []
    if last_hash is None:
        print("  首次运行，仅记录当前状态")
    else:
        for i, item in enumerate(items):
            if item["hash"] == last_hash:
                new_items = items[:i]
                break
        if not new_items and last_title:
            for i, item in enumerate(items):
                if item["title"] == last_title:
                    new_items = items[:i]
                    break
        if not new_items and items[0]["hash"] != last_hash:
            new_items = items[:3]

    # 处理新信息
    if new_items:
        print(f"\n[3/3] 发现 {len(new_items)} 条新信息！")

        notify_text = f"[通知] CMDE 审评进度更新！共 {len(new_items)} 条新信息\n"
        for i, item in enumerate(new_items[:10], 1):
            notify_text += f"\n{i}. {item['title']}"
        send_dingtalk_text(webhook, notify_text)

        time.sleep(1)
        for item in new_items[:5]:
            send_dingtalk_link(webhook, item["title"], item["url"])
            time.sleep(0.5)

    else:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        send_dingtalk_text(webhook, f"【CMDE审评进度】{now_str} 监控完成，暂无新发布。最新: {items[0]['title'][:30]}")

    # 保存状态
    print(f"\n保存状态...")
    STATE_FILE.parent.mkdir(exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "hash": items[0]["hash"],
            "title": items[0]["title"],
            "url": items[0]["url"],
            "time": datetime.now().isoformat(),
            "total": len(items)
        }, f, ensure_ascii=False, indent=2)
    print(f"  状态已保存: {items[0]['title'][:50]}")

    print("\n监控完成")


if __name__ == "__main__":
    main()
