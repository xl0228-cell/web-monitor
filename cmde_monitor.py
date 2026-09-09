# -*- coding: utf-8 -*-
"""
CMDE 审评进度网页监控脚本 - GitHub Actions 版本
功能：
1. 使用 Playwright 渲染动态网页 https://www.cmde.org.cn/xwdt/shpbg/index.html
2. 只提取「审评报告」公告列表里的真实公告（div.list > ul > li > a），
   过滤掉导航栏的固定栏目链接（之前误把它们当最新公告，导致 hash 永不变化）。
3. 公告链接特征：/xwdt/shpbg/<17位时间戳>.html（如 20260812084053156.html，
   前 14 位是 YYYYMMDDHHMMSS，最后 3 位是序号）。
4. 检测新发布并对比上次状态（以"最新一条"为基准）。
5. 通过钉钉 Webhook 推送通知（新公告 + 状态通知）。
6. 状态文件写入 state/cmde_state.json，由 GitHub Actions 提交回仓库。
"""

from pathlib import Path
from datetime import datetime
import json
import hashlib
import time
import sys
import os

# ========== 配置区域 ==========
URL = "https://www.cmde.org.cn/xwdt/shpbg/index.html"
# Webhook 优先从环境变量读取（GitHub Actions 用 Secret 注入）
DINGTALK_WEBHOOK = os.environ.get(
    "DINGTALK_WEBHOOK",
    ""
)
STATE_FILE = Path(__file__).parent / "state" / "cmde_state.json"
CMDE_BASE = "https://www.cmde.org.cn"
MAX_RETRY = 3
# ==============================


def send_dingtalk_text(content: str):
    """发送钉钉文本消息"""
    import urllib.request
    if "标准物质" not in content:
        content = "标准物质 | " + content
    msg = {"msgtype": "text", "text": {"content": content}}
    try:
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            DINGTALK_WEBHOOK,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read().decode("utf-8"))
            if result.get("errcode") == 0:
                print(f"  钉钉文本: OK")
            else:
                print(f"  钉钉文本失败: {result.get('errmsg')}")
    except Exception as e:
        print(f"  钉钉文本异常: {e}")


def send_dingtalk_link(title: str, message_url: str):
    """发送钉钉链接消息"""
    import urllib.request
    if "标准物质" not in title:
        title = "标准物质 | " + title
    msg = {
        "msgtype": "link",
        "link": {
            "title": title,
            "text": "CMDE 审评进度更新",
            "messageUrl": message_url,
            "picUrl": "",
        },
    }
    try:
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            DINGTALK_WEBHOOK,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read().decode("utf-8"))
            if result.get("errcode") == 0:
                print(f"  钉钉链接: OK ({title[:30]})")
            else:
                print(f"  钉钉链接失败: {result.get('errmsg')}")
    except Exception as e:
        print(f"  钉钉链接异常: {e}")


def fetch_cmde_announcements() -> list:
    """使用 Playwright 抓取 CMDE 审评进度页面。

    返回列表：[{"title":..., "url":..., "date":..., "ts":..., "hash":...}, ...]
    按发布时间戳降序，最新在前。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [错误] Playwright 未安装")
        return []

    last_error = None
    for attempt in range(1, MAX_RETRY + 1):
        print(f"  启动 Playwright (第 {attempt}/{MAX_RETRY} 次)...")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-infobars",
                    ],
                )
                context = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()

                print(f"  访问: {URL}")
                page.goto(URL, wait_until="networkidle", timeout=60000)
                # 等待公告列表渲染出现
                try:
                    page.wait_for_selector(
                        "div.list ul li a[href*='shpbg/']",
                        timeout=15000,
                    )
                except Exception:
                    pass
                page.wait_for_timeout(3000)

                # 在 div.list 区域筛选真实公告（避免抓取导航栏固定栏目）
                items_raw = page.evaluate(
                    """() => {
                        const results = [];
                        const seen = new Set();
                        const nodes = document.querySelectorAll('div.list ul li a');
                        for (const a of nodes) {
                            const href = a.getAttribute('href') || '';
                            // 17 位时间戳 + .html
                            const m = href.match(/xwdt\\/shpbg\\/(\\d{17})\\.html/);
                            if (!m) continue;
                            const title = (a.innerText || a.textContent || '')
                                .replace(/\\s+/g, ' ').trim();
                            if (!title) continue;
                            const key = m[1] + '|' + title;
                            if (seen.has(key)) continue;
                            seen.add(key);
                            results.push({ ts: m[1], title: title, path: href });
                        }
                        return results;
                    }"""
                )

                browser.close()

                if not items_raw:
                    print(f"  第 {attempt} 次未抓到（可能被反爬），重试...")
                    time.sleep(8)
                    continue

                # 构造完整 URL、日期、hash
                items = []
                for it in items_raw:
                    ts = it["ts"]
                    full_url = it["path"]
                    if full_url.startswith("http"):
                        pass
                    elif full_url.startswith("/"):
                        full_url = CMDE_BASE + full_url
                    else:
                        seg = full_url.split("/xwdt/shpbg/")[-1]
                        full_url = f"{CMDE_BASE}/xwdt/shpbg/{seg}"
                    # 前 14 位: YYYYMMDDHHMMSS
                    date = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
                    item_hash = hashlib.md5(
                        f"{ts}|{full_url}|{it['title']}".encode()
                    ).hexdigest()
                    items.append(
                        {
                            "title": it["title"],
                            "url": full_url,
                            "date": date,
                            "ts": ts,
                            "hash": item_hash,
                        }
                    )

                # 时间戳降序
                items.sort(key=lambda x: x["ts"], reverse=True)
                print(f"  共获取 {len(items)} 条真实审评报告公告")
                return items

        except Exception as e:
            last_error = e
            print(f"  第 {attempt} 次抓取失败: {e}")
            time.sleep(8)

    print(f"  抓取重试 {MAX_RETRY} 次仍失败: {last_error}")
    return []


def main():
    if not DINGTALK_WEBHOOK:
        print("[FAIL] 未设置 DINGTALK_WEBHOOK 环境变量")
        sys.exit(1)

    print("=" * 60)
    print(f"CMDE 审评进度监控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print("\n[1/3] 抓取审评进度列表...")
    items = fetch_cmde_announcements()
    if not items:
        print("  未获取到数据")
        sys.exit(1)

    print("\n最新公告 (前 5 条):")
    for i, item in enumerate(items[:5], 1):
        print(f"  {i}. {item['title'][:60]} ({item['date']})")
        print(f"     {item['url']}")

    print(f"\n[2/3] 检查更新...")
    last_hash = None
    last_title = None
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                last_hash = state.get("hash")
                last_title = state.get("title")
                print(f"  上次状态: {last_title} ({state.get('date', '')})")
        except Exception as e:
            print(f"  读取状态失败: {e}")

    # ===== 以「最新一条公告」为基准 =====
    new_items = []
    current_latest = items[0]  # 降序后最新

    if last_hash is None:
        print("  首次运行，仅记录当前状态（不推送通知）")
    elif current_latest["hash"] == last_hash:
        print("  无更新: 最新一条与上次一致")
    else:
        # 找出上次那条在当前列表的位置，它之前即为新增
        last_idx = None
        for i, item in enumerate(items):
            if item["hash"] == last_hash:
                last_idx = i
                break
        if last_idx is None and last_title:
            # hash 匹配不到（标题被编辑），退化按标题+更早 ts 匹配
            for i, item in enumerate(items):
                if item["title"] == last_title and item["ts"] < current_latest["ts"]:
                    last_idx = i
                    break
        if last_idx is not None:
            new_items = items[:last_idx]
        else:
            # 上次记录的那条已被挤出，保守推送最新 3 条
            new_items = items[:3]
        print(f"  新增: {len(new_items)} 条")

    if new_items:
        print(f"\n[3/3] 发现 {len(new_items)} 条新公告，推送中...")
        notify_text = f"[通知] CMDE 审评进度更新！共 {len(new_items)} 条新公告\n"
        for i, item in enumerate(new_items[:10], 1):
            notify_text += f"\n{i}. {item['title']} ({item['date']})"
        send_dingtalk_text(notify_text)

        time.sleep(1)
        for item in new_items[:5]:
            send_dingtalk_link(
                title=f"{item['date'][:10]} {item['title']}",
                message_url=item["url"],
            )
            time.sleep(0.5)
    else:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        latest_desc = (
            f"{current_latest['title'][:40]} ({current_latest['date'][:10]})"
        )
        send_dingtalk_text(
            f"标准物质 | 【CMDE审评进度】{now_str} 监控完成，暂无新审评报告发布。"
            f"最新报告: {latest_desc}"
        )

    # 保存当前最新一条作为下次基准
    print(f"\n保存状态...")
    STATE_FILE.parent.mkdir(exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "hash": current_latest["hash"],
                "title": current_latest["title"],
                "url": current_latest["url"],
                "date": current_latest["date"],
                "ts": current_latest["ts"],
                "time": datetime.now().isoformat(),
                "total": len(items),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"  状态已保存: {current_latest['title'][:50]} ({current_latest['date'][:10]})")
    print("\n监控完成")


if __name__ == "__main__":
    main()