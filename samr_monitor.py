# -*- coding: utf-8 -*-
"""
SAMR 公告监控 - GitHub Actions 版
监控 https://www.samr.gov.cn/jls/djcx/index.html
检测新公告并通过钉钉推送通知。
状态文件保存在 state/samr_state.json，每次运行后提交回仓库。
"""
import json
import hashlib
import time
import os
import sys
from pathlib import Path
from datetime import datetime

URL = "https://www.samr.gov.cn/jls/djcx/index.html"
STATE_FILE = Path(__file__).parent / "state" / "samr_state.json"


def send_dingtalk(webhook, content):
    """发送钉钉文本消息"""
    import urllib.request
    msg = {"msgtype": "text", "text": {"content": content}}
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read().decode("utf-8"))
        print(f"  钉钉文本: {'OK' if result.get('errcode') == 0 else result.get('errmsg')}")


def send_dingtalk_link(webhook, title, text, message_url):
    """发送钉钉链接消息"""
    import urllib.request
    msg = {"msgtype": "link", "link": {"title": title, "text": text, "messageUrl": message_url, "picUrl": ""}}
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read().decode("utf-8"))
        print(f"  钉钉链接: {'OK' if result.get('errcode') == 0 else result.get('errmsg')}")


def send_dingtalk_feedcard(webhook, items):
    """发送钉钉 FeedCard 消息"""
    import urllib.request
    links = [{"title": i["title"], "messageURL": i["message_url"], "picURL": ""} for i in items[:8]]
    if not links:
        return
    msg = {"msgtype": "feedCard", "feedCard": {"links": links}}
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read().decode("utf-8"))
        print(f"  钉钉FeedCard: {'OK' if result.get('errcode') == 0 else result.get('errmsg')}")


def fetch_announcements():
    """使用 Playwright 抓取公告列表"""
    from playwright.sync_api import sync_playwright

    print(f"  启动 Playwright / Chromium...")
    items = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        print(f"  访问: {URL}")
        page.goto(URL, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(2000)

        # 获取页面所有链接
        links = page.query_selector_all("a")
        print(f"  找到 {len(links)} 个链接标签")

        for link in links:
            try:
                title = link.inner_text().strip()
                href = link.get_attribute("href")
                if not title or not href:
                    continue
                if len(title) < 5:
                    continue
                if href.startswith("/"):
                    href = "https://www.samr.gov.cn" + href
                item_hash = hashlib.md5(f"{title}{href}".encode()).hexdigest()
                items.append({"title": title, "url": href, "hash": item_hash})
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

    print(f"  共获取 {len(unique)} 条公告")
    return unique


def fetch_pdfs_from_detail(detail_url, browser):
    """访问详情页获取 PDF 链接"""
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(detail_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1000)

        pdfs = []
        seen = set()
        for selector in ['a[href*=".pdf"]', 'a[href*=".PDF"]', 'a[href*="attach"]']:
            links = page.query_selector_all(selector)
            for link in links:
                try:
                    href = link.get_attribute("href")
                    text = link.inner_text().strip()
                    if href and ".pdf" in href.lower():
                        if href.startswith("/"):
                            href = "https://www.samr.gov.cn" + href
                        if href in seen:
                            continue
                        seen.add(href)
                        pdfs.append({"title": text or "未命名", "pdf_url": href})
                except:
                    continue
        page.close()
        return pdfs
    except Exception as e:
        print(f"  访问详情页失败: {e}")
        return []


def main():
    webhook = os.environ.get("DINGTALK_WEBHOOK")
    if not webhook:
        print("[FAIL] DINGTALK_WEBHOOK environment variable not set")
        sys.exit(1)

    print("=" * 60)
    print(f"SAMR 公告监控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 抓取公告
    print("\n[1/3] 抓取公告列表...")
    items = fetch_announcements()
    if not items:
        print("  未获取到公告数据")
        sys.exit(1)

    print(f"\n最新公告 (前5条):")
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

    # 检测新公告
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

    # 处理新公告
    if new_items:
        print(f"\n[3/3] 发现 {len(new_items)} 条新公告！")

        # 推送文本通知
        notify_text = f"标准物质 市场监管总局有新发布！共 {len(new_items)} 条\n"
        for i, item in enumerate(new_items[:10], 1):
            notify_text += f"\n{i}. {item['title']}"
        send_dingtalk(webhook, notify_text)

        # 推送链接消息
        time.sleep(1)
        for item in new_items[:3]:
            send_dingtalk_link(webhook, item["title"][:50], "标准物质 - 市场监管总局新发布", item["url"])
            time.sleep(0.5)

        # 尝试获取 PDF 链接并推送 FeedCard
        feedcard_items = []
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
                for item in new_items[:5]:
                    pdfs = fetch_pdfs_from_detail(item["url"], browser)
                    for pdf in pdfs:
                        feedcard_items.append({"title": f"{pdf['title']}", "message_url": pdf["pdf_url"]})
                    time.sleep(1)
                browser.close()
        except Exception as e:
            print(f"  PDF 链接获取失败: {e}")

        if feedcard_items:
            print(f"  推送 {len(feedcard_items)} 条 PDF 链接卡片...")
            send_dingtalk_feedcard(webhook, feedcard_items)

    else:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        send_dingtalk(webhook, f"标准物质 | 【SAMR公告监控】{now_str} 监控完成，暂无新公告。最新: {items[0]['title'][:30]}")

    # 保存状态
    print(f"\n保存状态...")
    STATE_FILE.parent.mkdir(exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "hash": items[0]["hash"],
            "title": items[0]["title"],
            "time": datetime.now().isoformat(),
            "total": len(items)
        }, f, ensure_ascii=False, indent=2)
    print(f"  状态已保存: {items[0]['title'][:50]}")

    print("\n监控完成")


if __name__ == "__main__":
    main()
