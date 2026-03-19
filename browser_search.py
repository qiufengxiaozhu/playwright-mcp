"""
通用浏览器搜索脚本 — 支持小红书、百度等网站的自动化搜索与截图

用法：
  python browser_search.py --keyword "搜索关键词"
  python browser_search.py --keyword "搜索关键词" --site xiaohongshu --save-to /path/to/screenshot.png
  python browser_search.py --keyword "搜索关键词" --site baidu --headless

支持的站点：xiaohongshu / baidu / bing / google / 自定义 URL
"""
import argparse
import asyncio
import os
import sys
import urllib.parse
from pathlib import Path

from playwright.async_api import async_playwright

DESKTOP_PATH = None
SCREENSHOTS_DIR = Path("/tmp/screenshots")

CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--dns-prefetch-disable",
    "--disable-features=NetworkService,NetworkServiceInProcess",
]

SITE_SEARCH_URLS = {
    "xiaohongshu": "https://www.xiaohongshu.com/search_result?keyword={keyword}",
    "baidu": "https://www.baidu.com/s?wd={keyword}",
    "bing": "https://www.bing.com/search?q={keyword}",
    "google": "https://www.google.com/search?q={keyword}",
}


def detect_desktop_path() -> str | None:
    """自动检测 WSL 环境下的 Windows 桌面路径。"""
    users_dir = Path("/mnt/c/Users")
    if not users_dir.exists():
        return None
    for user_dir in users_dir.iterdir():
        if user_dir.name in ("Public", "Default", "Default User", "All Users", "DefaultAppPool", "desktop.ini"):
            continue
        desktop = user_dir / "Desktop"
        if desktop.exists():
            return str(desktop)
    return None


async def search_and_screenshot(
    keyword: str,
    site: str = "xiaohongshu",
    url: str | None = None,
    headless: bool = False,
    save_to: str | None = None,
    timeout: int = 60000,
    wait_seconds: int = 8,
):
    """执行搜索并截图。

    Args:
        keyword: 搜索关键词
        site: 目标站点（xiaohongshu/baidu/bing/google）
        url: 自定义 URL（优先于 site），其中 {keyword} 会被替换
        headless: 是否无头模式
        save_to: 截图保存路径（默认桌面 + /tmp/screenshots/）
        timeout: 导航超时（毫秒）
        wait_seconds: 页面加载后额外等待秒数
    """
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    encoded_keyword = urllib.parse.quote(keyword)
    if url:
        search_url = url.replace("{keyword}", encoded_keyword)
    elif site in SITE_SEARCH_URLS:
        search_url = SITE_SEARCH_URLS[site].replace("{keyword}", encoded_keyword)
    else:
        print(f"不支持的站点: {site}，支持: {', '.join(SITE_SEARCH_URLS.keys())}")
        sys.exit(1)

    print(f"搜索关键词: {keyword}")
    print(f"目标 URL: {search_url}")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless, args=CHROMIUM_ARGS)
    ctx = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
    )
    page = await ctx.new_page()

    try:
        await page.goto(search_url, timeout=timeout, wait_until="domcontentloaded")
        print(f"页面加载完成: {page.url}")
    except Exception as e:
        print(f"导航超时（继续执行）: {e}")

    await asyncio.sleep(wait_seconds)

    if site == "xiaohongshu":
        await page.evaluate("""() => {
            document.querySelectorAll('[to="body"], .reds-mask').forEach(el => el.remove());
            document.body.style.overflow = 'auto';
            document.documentElement.style.overflow = 'auto';
        }""")
        await asyncio.sleep(1)

    title = await page.title()
    print(f"页面标题: {title}")

    text = await page.evaluate("() => document.body.innerText")
    print(f"页面文本长度: {len(text)} 字符")

    safe_keyword = keyword.replace(" ", "_").replace("/", "_")[:50]
    filename = f"{site}_{safe_keyword}.png"

    tmp_path = SCREENSHOTS_DIR / filename
    try:
        buf = await page.locator("body").screenshot(type="png", timeout=30000)
        with open(tmp_path, "wb") as f:
            f.write(buf)
        print(f"截图已保存: {tmp_path}")
    except Exception as e:
        print(f"截图失败: {e}")
        tmp_path = None

    desktop = save_to or (detect_desktop_path() and str(Path(detect_desktop_path()) / filename))
    if desktop and tmp_path:
        try:
            Path(desktop).parent.mkdir(parents=True, exist_ok=True)
            with open(desktop, "wb") as f:
                f.write(buf)
            print(f"截图已保存到桌面: {desktop}")
        except Exception as e:
            print(f"保存到桌面失败: {e}")

    if text:
        preview = text.strip()[:1000]
        print(f"\n--- 页面内容预览 ---\n{preview}\n--- 预览结束 ---")

    await browser.close()
    await pw.stop()

    return {"url": page.url if not page.is_closed() else search_url, "title": title, "text_length": len(text), "screenshot": str(tmp_path)}


def main():
    parser = argparse.ArgumentParser(description="通用浏览器搜索与截图工具")
    parser.add_argument("--keyword", "-k", required=True, help="搜索关键词")
    parser.add_argument("--site", "-s", default="xiaohongshu", help="目标站点: xiaohongshu/baidu/bing/google")
    parser.add_argument("--url", "-u", help="自定义搜索 URL，{keyword} 会被替换为关键词")
    parser.add_argument("--headless", action="store_true", help="无头模式（不显示浏览器窗口）")
    parser.add_argument("--save-to", help="截图保存路径（默认桌面 + /tmp/screenshots/）")
    parser.add_argument("--timeout", type=int, default=60000, help="导航超时毫秒数（默认 60000）")
    parser.add_argument("--wait", type=int, default=8, help="页面加载后额外等待秒数（默认 8）")
    args = parser.parse_args()

    result = asyncio.run(search_and_screenshot(
        keyword=args.keyword,
        site=args.site,
        url=args.url,
        headless=args.headless,
        save_to=args.save_to,
        timeout=args.timeout,
        wait_seconds=args.wait,
    ))
    print(f"\n完成: {result}")


if __name__ == "__main__":
    main()
