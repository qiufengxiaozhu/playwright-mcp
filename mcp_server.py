"""
Playwright MCP Server — 为 AI 智能体提供原子级浏览器操作工具

Playwright MCP Server — Atomic browser tools for AI agents (Cursor / Claude / etc.)

设计理念：
  不内置 LLM。AI 智能体（如 Cursor）充当"大脑"，
  本 MCP Server 仅提供原子级浏览器操作。
  智能体根据截图 / DOM 快照自行决定下一步操作。

浏览器模式（通过环境变量 BROWSER_MODE 控制）：
  visible  → Playwright 内置 Chromium 有头模式（默认，WSLg 下可在 Windows 桌面看到操作过程）
  headless → Playwright 内置 Chromium 无头模式

可配置项（均通过环境变量设置）：
  BROWSER_MODE     — visible（默认）/ headless
  VIEWPORT_WIDTH   — 视口宽度（默认 1920）
  VIEWPORT_HEIGHT  — 视口高度（默认 1080）
  BROWSER_LOCALE   — 浏览器语言区域（默认 zh-CN）
  BROWSER_TIMEOUT  — 默认导航超时，单位毫秒（默认 30000）
"""
import asyncio
import base64
import json
import logging
import os
import sys
import traceback
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("playwright-mcp")

# ---------------------------------------------------------------------------
# 环境变量配置
# ---------------------------------------------------------------------------
VIEWPORT_W = int(os.getenv("VIEWPORT_WIDTH", "1920"))    # 视口宽度
VIEWPORT_H = int(os.getenv("VIEWPORT_HEIGHT", "1080"))   # 视口高度
LOCALE = os.getenv("BROWSER_LOCALE", "zh-CN")            # 浏览器语言
DEFAULT_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "30000"))  # 默认超时（毫秒）

# ---------------------------------------------------------------------------
# 浏览器生命周期管理
# ---------------------------------------------------------------------------
_playwright = None          # Playwright 实例
_browser = None             # Browser 实例
_contexts: dict[str, "BrowserContext"] = {}  # 标签页 ID → BrowserContext 映射
_pages: dict[str, "Page"] = {}               # 标签页 ID → Page 映射
_active_tab: str | None = None               # 当前活跃标签页 ID


async def ensure_browser():
    """确保浏览器已启动，返回当前活跃页面。

    首次调用时会启动 Playwright 和 Chromium 浏览器，
    后续调用复用已有实例（除非页面已关闭）。
    """
    global _playwright, _browser, _active_tab

    # 如果当前标签页仍然有效，直接返回
    if _active_tab and _active_tab in _pages:
        page = _pages[_active_tab]
        if not page.is_closed():
            return page

    from playwright.async_api import async_playwright

    # 首次启动 Playwright
    if _playwright is None:
        _playwright = await async_playwright().start()

    # 浏览器未启动或已断开连接时，重新启动
    if _browser is None or not _browser.is_connected():
        mode = os.getenv("BROWSER_MODE", "visible").lower()
        headless = mode == "headless"

        _browser = await _playwright.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",            # Docker/WSL 环境需要
                "--disable-setuid-sandbox",
                "--disable-gpu",           # WSLg 兼容性
                "--disable-dev-shm-usage", # 避免共享内存不足
            ],
        )

    # 创建新的浏览器上下文和页面（即新标签页）
    if not _active_tab or _active_tab not in _pages:
        ctx = await _browser.new_context(
            viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
            locale=LOCALE,
        )
        page = await ctx.new_page()
        tab_id = f"tab-{id(page)}"
        _contexts[tab_id] = ctx
        _pages[tab_id] = page
        _active_tab = tab_id

    return _pages[_active_tab]


async def close_browser():
    """关闭浏览器，清理所有状态（上下文、页面、Playwright 实例）。"""
    global _playwright, _browser, _active_tab
    _contexts.clear()
    _pages.clear()
    _active_tab = None
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
    _playwright = None
    _browser = None


# ---------------------------------------------------------------------------
# MCP Server 定义
# ---------------------------------------------------------------------------
app = Server("playwright-mcp")


def _prop(name: str, typ: str, desc: str, **kw) -> dict:
    """构造 JSON Schema 属性的辅助函数。"""
    p = {"type": typ, "description": desc}
    p.update(kw)
    return name, p


def _tool(name: str, desc: str, props: list[tuple], required: list[str] | None = None) -> types.Tool:
    """构造 MCP Tool 定义的辅助函数，减少重复代码。"""
    properties = {}
    for pname, pdef in props:
        properties[pname] = pdef
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return types.Tool(name=name, description=desc, inputSchema=schema)


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """注册所有 27 个浏览器工具。"""
    return [
        # ==================== 导航类工具 ====================
        _tool("browser_open",
              "Navigate to a URL. Returns page title and interactive elements."
              " / 导航到指定 URL，返回页面标题和可交互元素列表。",
              [_prop("url", "string", "目标 URL"),
               _prop("wait_until", "string", "等待条件: domcontentloaded / networkidle / load / commit", default="domcontentloaded")],
              required=["url"]),

        _tool("browser_go_back",
              "Navigate back in browser history. / 浏览器后退。",
              [_prop("wait_until", "string", "导航后的等待条件", default="domcontentloaded")]),

        _tool("browser_go_forward",
              "Navigate forward in browser history. / 浏览器前进。",
              [_prop("wait_until", "string", "导航后的等待条件", default="domcontentloaded")]),

        _tool("browser_refresh",
              "Refresh the current page. / 刷新当前页面。",
              [_prop("wait_until", "string", "刷新后的等待条件", default="domcontentloaded")]),

        # ==================== 交互类工具 ====================
        _tool("browser_click",
              "Click an element by CSS selector or text content. Supports force-click to bypass disabled/hidden checks."
              " / 点击元素，支持 CSS 选择器或文本匹配，可强制点击绕过 disabled/hidden 检查。",
              [_prop("selector", "string", "CSS 选择器"),
               _prop("text", "string", "按文本内容匹配（选择器不好写时使用）"),
               _prop("force", "boolean", "强制点击，绕过可操作性检查（默认 false）", default=False),
               _prop("double_click", "boolean", "双击（默认 false）", default=False),
               _prop("right_click", "boolean", "右键点击（默认 false）", default=False),
               _prop("position_x", "number", "在元素内指定 x 偏移位置点击"),
               _prop("position_y", "number", "在元素内指定 y 偏移位置点击"),
               _prop("timeout", "integer", "超时时间，毫秒（默认 5000）", default=5000)]),

        _tool("browser_hover",
              "Hover over an element. Useful for triggering tooltips, dropdown menus, etc."
              " / 鼠标悬停在元素上，用于触发 tooltip、下拉菜单等。",
              [_prop("selector", "string", "CSS 选择器"),
               _prop("text", "string", "按文本内容匹配"),
               _prop("timeout", "integer", "超时时间，毫秒（默认 5000）", default=5000)]),

        _tool("browser_type",
              "Type text into an input field."
              " / 在输入框中输入文字。",
              [_prop("selector", "string", "输入框的 CSS 选择器"),
               _prop("text", "string", "要输入的文字"),
               _prop("clear", "boolean", "是否先清空（默认 true，使用 fill；false 则逐键输入）", default=True),
               _prop("press_enter", "boolean", "输入后是否按回车（默认 false）", default=False),
               _prop("timeout", "integer", "超时时间，毫秒（默认 5000）", default=5000)],
              required=["selector", "text"]),

        _tool("browser_keyboard",
              "Press keyboard keys. Supports special keys and shortcuts (e.g. 'Enter', 'Escape', 'Control+a', 'Meta+c')."
              " / 按键操作，支持特殊键和组合快捷键。",
              [_prop("key", "string", "要按的键或组合键（Playwright 键名）"),
               _prop("selector", "string", "先聚焦此元素再按键（可选）")],
              required=["key"]),

        _tool("browser_select",
              "Select option(s) in a <select> dropdown."
              " / 在下拉框中选择选项。",
              [_prop("selector", "string", "select 元素的 CSS 选择器"),
               _prop("value", "string", "按 value 属性选择"),
               _prop("label", "string", "按显示文本选择"),
               _prop("index", "integer", "按索引选择")],
              required=["selector"]),

        _tool("browser_drag",
              "Drag an element to a target position or element."
              " / 拖拽元素到目标位置或目标元素。",
              [_prop("source_selector", "string", "被拖拽元素的 CSS 选择器"),
               _prop("target_selector", "string", "放置目标的 CSS 选择器"),
               _prop("target_x", "number", "放置到页面绝对 x 坐标（替代 target_selector）"),
               _prop("target_y", "number", "放置到页面绝对 y 坐标")],
              required=["source_selector"]),

        # ==================== 文件上传工具 ====================
        _tool("browser_upload_file",
              "Upload file to an <input type='file'> element."
              " / 上传文件到 file input 元素，支持多文件。",
              [_prop("selector", "string", "file input 的 CSS 选择器"),
               _prop("file_path", "string", "文件路径（多文件用逗号分隔）")],
              required=["selector", "file_path"]),

        _tool("browser_upload_via_dialog",
              "Upload file by intercepting the file chooser dialog. Works for dynamic file inputs created by JavaScript."
              " / 通过拦截文件选择对话框上传文件。适用于 JS 动态创建 file input 的场景。",
              [_prop("trigger_selector", "string", "触发文件对话框的元素 CSS 选择器（上传按钮/区域）"),
               _prop("trigger_text", "string", "触发元素的文本内容（替代 selector）"),
               _prop("file_path", "string", "要上传的文件路径"),
               _prop("timeout", "integer", "等待文件对话框弹出的超时时间，毫秒（默认 10000）", default=10000)],
              required=["file_path"]),

        # ==================== 观察类工具 ====================
        _tool("browser_screenshot",
              "Take a screenshot of the current page or a specific element."
              " / 对当前页面或指定元素截图。",
              [_prop("wait_for_idle", "boolean", "截图前等待网络空闲（默认 false）", default=False),
               _prop("wait_ms", "integer", "截图前额外等待的毫秒数（默认 0）", default=0),
               _prop("full_page", "boolean", "截取整个可滚动页面（默认 false）", default=False),
               _prop("selector", "string", "只截取指定元素（CSS 选择器）")]),

        _tool("browser_get_text",
              "Get text content of the page or a specific element."
              " / 获取页面或指定元素的文本内容。",
              [_prop("selector", "string", "CSS 选择器（可选，不提供则获取整个页面文本）")]),

        _tool("browser_snapshot",
              "Get a structured DOM snapshot: interactive elements + optional text nodes. Returns JSON."
              " / 获取结构化 DOM 快照：可交互元素 + 可选文本节点，返回 JSON。",
              [_prop("selector", "string", "限定扫描范围的 CSS 选择器（可选）"),
               _prop("include_text_nodes", "boolean", "是否包含可见文本节点（默认 false）", default=False),
               _prop("max_elements", "integer", "最多返回的可交互元素数（默认 100）", default=100)]),

        _tool("browser_get_url",
              "Get the current page URL and title. / 获取当前页面 URL 和标题。",
              []),

        # ==================== 滚动工具 ====================
        _tool("browser_scroll",
              "Scroll the page or a specific container."
              " / 滚动页面或指定容器。",
              [_prop("direction", "string", "滚动方向: down / up / left / right（默认 down）", default="down"),
               _prop("amount", "integer", "滚动像素数（默认 500）", default=500),
               _prop("selector", "string", "容器 CSS 选择器（可选，不提供则滚动页面）"),
               _prop("to_bottom", "boolean", "直接滚动到底部（默认 false）", default=False),
               _prop("to_top", "boolean", "直接滚动到顶部（默认 false）", default=False)]),

        # ==================== 等待工具 ====================
        _tool("browser_wait_for",
              "Wait for a condition: element visible/hidden, text appears, network idle, or just a timeout."
              " / 等待条件满足：元素可见/隐藏、文本出现、网络空闲、或纯等待。",
              [_prop("selector", "string", "等待该元素出现/消失（CSS 选择器）"),
               _prop("state", "string", "等待状态: visible / hidden / attached / detached（默认 visible）", default="visible"),
               _prop("text", "string", "等待页面出现该文本"),
               _prop("idle", "boolean", "等待网络空闲（默认 false）", default=False),
               _prop("timeout", "integer", "超时时间，毫秒（默认 30000）", default=30000)]),

        # ==================== JavaScript 执行 ====================
        _tool("browser_execute_js",
              "Execute JavaScript in the page context and return the result. Use IIFE pattern: (() => { ... })()"
              " / 在页面中执行 JavaScript 并返回结果。建议用 IIFE 模式。",
              [_prop("script", "string", "要执行的 JavaScript 代码")],
              required=["script"]),

        # ==================== 多标签页管理 ====================
        _tool("browser_new_tab",
              "Open a new browser tab and optionally navigate to a URL."
              " / 打开新标签页，可选导航到指定 URL。",
              [_prop("url", "string", "要导航到的 URL（可选）")]),

        _tool("browser_list_tabs",
              "List all open browser tabs with their IDs, URLs, and titles."
              " / 列出所有打开的标签页及其 ID、URL、标题。",
              []),

        _tool("browser_switch_tab",
              "Switch to a different browser tab by its ID."
              " / 切换到指定 ID 的标签页。",
              [_prop("tab_id", "string", "要切换到的标签页 ID")],
              required=["tab_id"]),

        _tool("browser_close_tab",
              "Close a browser tab. Closes current tab if no tab_id specified."
              " / 关闭标签页，不指定 tab_id 则关闭当前标签页。",
              [_prop("tab_id", "string", "要关闭的标签页 ID（可选，默认关闭当前标签页）")]),

        # ==================== PDF 导出 ====================
        _tool("browser_save_pdf",
              "Export the current page as a PDF file."
              " / 将当前页面导出为 PDF 文件。",
              [_prop("path", "string", "PDF 保存路径"),
               _prop("format", "string", "纸张格式: A4 / Letter / Legal 等（默认 A4）", default="A4"),
               _prop("landscape", "boolean", "横向排版（默认 false）", default=False)],
              required=["path"]),

        # ==================== Cookie 管理 ====================
        _tool("browser_get_cookies",
              "Get all cookies for the current page."
              " / 获取当前页面的所有 Cookie。",
              [_prop("urls", "string", "逗号分隔的 URL 列表（可选，默认当前页面）")]),

        _tool("browser_set_cookie",
              "Set a cookie. / 设置 Cookie。",
              [_prop("name", "string", "Cookie 名称"),
               _prop("value", "string", "Cookie 值"),
               _prop("url", "string", "Cookie 所属 URL（可选，默认当前页面）"),
               _prop("domain", "string", "Cookie 域名（可选）"),
               _prop("path", "string", "Cookie 路径（默认 /）", default="/"),
               _prop("httpOnly", "boolean", "HTTP Only 标志", default=False),
               _prop("secure", "boolean", "Secure 标志", default=False)],
              required=["name", "value"]),

        # ==================== 生命周期 ====================
        _tool("browser_close",
              "Close the browser and clean up all resources."
              " / 关闭浏览器并清理所有资源。",
              []),
    ]


# ---------------------------------------------------------------------------
# 工具处理函数（Handlers）
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent | types.ImageContent]:
    """MCP 工具调用的统一入口，根据工具名分派到对应处理函数。"""
    try:
        handler = HANDLERS.get(name)
        if handler:
            return await handler(arguments)
        return _text(f"Unknown tool: {name}")
    except Exception as e:
        error_msg = f"Error in {name}: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        return _text(error_msg)


def _text(msg: str) -> list[types.TextContent]:
    """构造文本类型的 MCP 响应。"""
    return [types.TextContent(type="text", text=msg)]


# ======================== 导航类处理函数 ========================

async def handle_open(args: dict):
    """打开 URL 并返回页面信息和可交互元素列表。"""
    url = args["url"]
    wait_until = args.get("wait_until", "domcontentloaded")
    page = await ensure_browser()
    await page.goto(url, wait_until=wait_until, timeout=DEFAULT_TIMEOUT)
    await asyncio.sleep(1)  # 给 SPA 应用额外的渲染时间
    title = await page.title()

    elements = await _get_interactive_elements(page)
    summary = f"Navigated to: {page.url}\nTitle: {title}\n\nInteractive elements ({len(elements)}):\n"
    for i, el in enumerate(elements[:30]):
        summary += f"  [{i}] <{el['tag']}> {el['text'][:60]}  → {el['selector']}\n"
    if len(elements) > 30:
        summary += f"  ... and {len(elements) - 30} more\n"
    return _text(summary)


async def handle_go_back(args: dict):
    """浏览器历史后退。"""
    page = await ensure_browser()
    wait_until = args.get("wait_until", "domcontentloaded")
    await page.go_back(wait_until=wait_until, timeout=DEFAULT_TIMEOUT)
    title = await page.title()
    return _text(f"Navigated back → {page.url} ({title})")


async def handle_go_forward(args: dict):
    """浏览器历史前进。"""
    page = await ensure_browser()
    wait_until = args.get("wait_until", "domcontentloaded")
    await page.go_forward(wait_until=wait_until, timeout=DEFAULT_TIMEOUT)
    title = await page.title()
    return _text(f"Navigated forward → {page.url} ({title})")


async def handle_refresh(args: dict):
    """刷新当前页面。"""
    page = await ensure_browser()
    wait_until = args.get("wait_until", "domcontentloaded")
    await page.reload(wait_until=wait_until, timeout=DEFAULT_TIMEOUT)
    title = await page.title()
    return _text(f"Refreshed → {page.url} ({title})")


# ======================== 交互类处理函数 ========================

async def handle_click(args: dict):
    """点击元素，支持文本匹配、CSS 选择器、双击、右键、强制点击、坐标偏移。"""
    page = await ensure_browser()
    selector = args.get("selector")
    text = args.get("text")
    force = args.get("force", False)
    double = args.get("double_click", False)
    right = args.get("right_click", False)
    timeout = args.get("timeout", 5000)
    pos_x = args.get("position_x")
    pos_y = args.get("position_y")

    position = None
    if pos_x is not None and pos_y is not None:
        position = {"x": pos_x, "y": pos_y}

    click_kwargs = {"timeout": timeout, "force": force}
    if position:
        click_kwargs["position"] = position

    # 优先使用文本匹配（选择器不好写时更方便）
    if text and not selector:
        locator = page.get_by_text(text, exact=False).first
        if double:
            await locator.dblclick(**click_kwargs)
        elif right:
            await locator.click(button="right", **click_kwargs)
        else:
            await locator.click(**click_kwargs)
        return _text(f"Clicked text: \"{text}\"")

    if selector:
        if double:
            await page.dblclick(selector, **click_kwargs)
        elif right:
            await page.click(selector, button="right", **click_kwargs)
        else:
            await page.click(selector, **click_kwargs)
        return _text(f"Clicked: {selector}")

    return _text("Error: provide selector or text")


async def handle_hover(args: dict):
    """鼠标悬停，用于触发 tooltip、下拉菜单等交互效果。"""
    page = await ensure_browser()
    selector = args.get("selector")
    text = args.get("text")
    timeout = args.get("timeout", 5000)

    if text and not selector:
        await page.get_by_text(text, exact=False).first.hover(timeout=timeout)
        return _text(f"Hovered over text: \"{text}\"")
    if selector:
        await page.hover(selector, timeout=timeout)
        return _text(f"Hovered: {selector}")
    return _text("Error: provide selector or text")


async def handle_type(args: dict):
    """在输入框中输入文字，支持清空后填充或逐键输入两种模式。"""
    page = await ensure_browser()
    selector = args["selector"]
    text = args["text"]
    clear = args.get("clear", True)
    press_enter = args.get("press_enter", False)
    timeout = args.get("timeout", 5000)

    if clear:
        # fill 模式：先清空再一次性填入（适合大多数场景）
        await page.fill(selector, text, timeout=timeout)
    else:
        # type 模式：逐键输入（适合需要触发 keydown/keyup 事件的场景）
        await page.locator(selector).first.type(text, timeout=timeout)

    if press_enter:
        await page.locator(selector).first.press("Enter")

    return _text(f"Typed {len(text)} chars into {selector}")


async def handle_keyboard(args: dict):
    """模拟键盘按键，支持单键和组合键。"""
    page = await ensure_browser()
    key = args["key"]
    selector = args.get("selector")

    if selector:
        # 先聚焦指定元素再按键
        await page.locator(selector).first.press(key)
    else:
        # 全局按键
        await page.keyboard.press(key)
    return _text(f"Pressed: {key}")


async def handle_select(args: dict):
    """在 <select> 下拉框中选择选项，支持按 value、label 或 index 选择。"""
    page = await ensure_browser()
    selector = args["selector"]
    value = args.get("value")
    label = args.get("label")
    index = args.get("index")

    if value is not None:
        await page.select_option(selector, value=value, timeout=5000)
        return _text(f"Selected value={value}")
    elif label is not None:
        await page.select_option(selector, label=label, timeout=5000)
        return _text(f"Selected label={label}")
    elif index is not None:
        await page.select_option(selector, index=index, timeout=5000)
        return _text(f"Selected index={index}")
    return _text("Error: provide value, label, or index")


async def handle_drag(args: dict):
    """拖拽元素到目标位置，支持拖到另一个元素或指定坐标。"""
    page = await ensure_browser()
    source = args["source_selector"]
    target_sel = args.get("target_selector")
    target_x = args.get("target_x")
    target_y = args.get("target_y")

    if target_sel:
        await page.drag_and_drop(source, target_sel, timeout=10000)
        return _text(f"Dragged {source} → {target_sel}")
    elif target_x is not None and target_y is not None:
        # 手动模拟拖拽：获取源元素中心 → 按下 → 移动 → 释放
        src_box = await page.locator(source).first.bounding_box()
        if not src_box:
            return _text(f"Error: cannot find element {source}")
        sx = src_box["x"] + src_box["width"] / 2
        sy = src_box["y"] + src_box["height"] / 2
        await page.mouse.move(sx, sy)
        await page.mouse.down()
        await page.mouse.move(target_x, target_y, steps=20)
        await page.mouse.up()
        return _text(f"Dragged {source} → ({target_x}, {target_y})")
    return _text("Error: provide target_selector or target_x/target_y")


# ======================== 文件上传处理函数 ========================

async def handle_upload_file(args: dict):
    """直接上传文件到 <input type="file"> 元素，支持逗号分隔多文件。"""
    page = await ensure_browser()
    selector = args["selector"]
    file_path = args["file_path"]
    paths = [p.strip() for p in file_path.split(",")]
    for p in paths:
        if not os.path.exists(p):
            return _text(f"Error: file not found: {p}")
    if len(paths) == 1:
        await page.set_input_files(selector, paths[0], timeout=5000)
    else:
        await page.set_input_files(selector, paths, timeout=5000)
    return _text(f"Uploaded: {', '.join(os.path.basename(p) for p in paths)}")


async def handle_upload_via_dialog(args: dict):
    """通过拦截文件选择对话框上传文件。

    适用于页面通过 JS 动态创建 <input type="file"> 或使用第三方组件的场景。
    工作流：注册 file_chooser 监听 → 点击触发元素 → 对话框弹出后自动设置文件。
    """
    page = await ensure_browser()
    trigger_selector = args.get("trigger_selector")
    trigger_text = args.get("trigger_text")
    file_path = args["file_path"]
    timeout = args.get("timeout", 10000)

    if not os.path.exists(file_path):
        return _text(f"Error: file not found: {file_path}")

    async with page.expect_file_chooser(timeout=timeout) as fc_info:
        if trigger_selector:
            await page.click(trigger_selector, timeout=5000, force=True)
        elif trigger_text:
            await page.get_by_text(trigger_text, exact=False).first.click(timeout=5000, force=True)
        else:
            return _text("Error: provide trigger_selector or trigger_text")

    file_chooser = await fc_info.value
    await file_chooser.set_files(file_path)
    return _text(f"Uploaded via dialog: {os.path.basename(file_path)}")


# ======================== 观察类处理函数 ========================

async def handle_screenshot(args: dict):
    """页面截图，支持等待网络空闲、额外延迟、全页截图、元素截图。"""
    page = await ensure_browser()
    wait_for_idle = args.get("wait_for_idle", False)
    wait_ms = args.get("wait_ms", 0)
    full_page = args.get("full_page", False)
    selector = args.get("selector")

    if wait_for_idle:
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass  # 超时也继续截图

    if wait_ms > 0:
        await asyncio.sleep(wait_ms / 1000)

    if selector:
        el = page.locator(selector).first
        screenshot_bytes = await el.screenshot(type="png")
    else:
        screenshot_bytes = await page.screenshot(type="png", full_page=full_page)

    title = await page.title()
    b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    return [
        types.TextContent(type="text", text=f"Page: {title} ({page.url})"),
        types.ImageContent(type="image", data=b64, mimeType="image/png"),
    ]


async def handle_get_text(args: dict):
    """获取页面或指定元素的文本内容，最多返回 5000 字符。"""
    page = await ensure_browser()
    selector = args.get("selector")
    if selector:
        text = await page.locator(selector).first.text_content(timeout=5000) or ""
    else:
        text = await page.text_content("body") or ""
    text = text.strip()[:5000]
    return _text(text)


async def handle_snapshot(args: dict):
    """获取结构化 DOM 快照。

    在浏览器中执行 JS 扫描所有可交互元素（按钮、链接、输入框、ARIA 角色元素等），
    返回每个元素的标签名、文本、CSS 选择器、位置尺寸、属性等信息。
    弥补了 accessibility tree 对 SPA 应用经常返回空的不足。
    """
    page = await ensure_browser()
    scope_selector = args.get("selector")
    include_text = args.get("include_text_nodes", False)
    max_elements = args.get("max_elements", 100)

    # 在页面中执行的 JavaScript：扫描可交互元素并提取结构化信息
    js = """(opts) => {
        const scopeSel = opts.scopeSelector;
        const includeText = opts.includeText;
        const maxEls = opts.maxElements;
        const root = scopeSel ? document.querySelector(scopeSel) : document.body;
        if (!root) return { error: 'Element not found: ' + scopeSel };

        // 可交互元素选择器：覆盖常见 HTML 元素和 ARIA 角色
        const interactiveSel = 'a, button, input, select, textarea, [role="button"], [role="radio"], [role="checkbox"], [role="tab"], [role="switch"], [role="listbox"], [role="option"], [role="menuitem"], [onclick], [class*="upload"], [class*="Upload"], label[for], [contenteditable="true"]';
        const elements = Array.from(root.querySelectorAll(interactiveSel));

        const results = elements.slice(0, maxEls).map((el) => {
            const tag = el.tagName.toLowerCase();
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) return null;  // 跳过不可见元素

            const item = {
                tag,
                text: (el.textContent || el.value || el.placeholder || '').trim().substring(0, 100),
                visible: rect.width > 0 && rect.height > 0,
                rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
            };

            // 生成最佳 CSS 选择器
            const id = el.id;
            if (id) { item.id = id; item.selector = '#' + CSS.escape(id); }
            else {
                const name = el.getAttribute('name');
                if (name) item.selector = tag + '[name="' + name + '"]';
                else {
                    const forAttr = el.getAttribute('for');
                    if (forAttr) item.selector = tag + '[for="' + forAttr + '"]';
                    else item.selector = tag;
                }
            }

            // 提取关键属性
            const type = el.getAttribute('type');
            if (type) item.type = type;
            const role = el.getAttribute('role');
            if (role) item.role = role;
            if (el.disabled) item.disabled = true;
            if ((type === 'radio' || type === 'checkbox') && el.checked) item.checked = true;
            if (el.getAttribute('href')) item.href = el.getAttribute('href').substring(0, 200);
            const cls = el.className?.toString?.();
            if (cls) item.class = cls.substring(0, 80);
            if (el.getAttribute('aria-label')) item.ariaLabel = el.getAttribute('aria-label');

            return item;
        }).filter(Boolean);

        // 可选：提取可见文本节点（帮助理解页面内容）
        let textNodes = [];
        if (includeText) {
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
                acceptNode: (node) => {
                    const t = node.textContent.trim();
                    if (!t || t.length < 2) return NodeFilter.FILTER_REJECT;
                    const p = node.parentElement;
                    if (!p) return NodeFilter.FILTER_REJECT;
                    const r = p.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return NodeFilter.FILTER_REJECT;
                    const ptag = p.tagName.toLowerCase();
                    if (['script', 'style', 'noscript'].includes(ptag)) return NodeFilter.FILTER_REJECT;
                    return NodeFilter.FILTER_ACCEPT;
                }
            });
            let node, count = 0;
            while ((node = walker.nextNode()) && count < 50) {
                const p = node.parentElement;
                const r = p.getBoundingClientRect();
                textNodes.push({
                    text: node.textContent.trim().substring(0, 100),
                    parentTag: p.tagName.toLowerCase(),
                    rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
                });
                count++;
            }
        }

        return { url: location.href, title: document.title, elements: results, textNodes };
    }"""

    result = await page.evaluate(js, {
        "scopeSelector": scope_selector,
        "includeText": include_text,
        "maxElements": max_elements,
    })

    if isinstance(result, dict) and "error" in result:
        return _text(f"Snapshot error: {result['error']}")

    return _text(json.dumps(result, ensure_ascii=False, indent=2))


async def handle_get_url(args: dict):
    """获取当前页面的 URL 和标题。"""
    page = await ensure_browser()
    title = await page.title()
    return _text(f"URL: {page.url}\nTitle: {title}")


# ======================== 滚动处理函数 ========================

async def handle_scroll(args: dict):
    """滚动页面或容器，支持方向滚动和一键到顶/到底。"""
    page = await ensure_browser()
    direction = args.get("direction", "down")
    amount = args.get("amount", 500)
    selector = args.get("selector")
    to_bottom = args.get("to_bottom", False)
    to_top = args.get("to_top", False)

    if to_bottom:
        if selector:
            await page.evaluate("(sel) => { const el = document.querySelector(sel); if (el) el.scrollTop = el.scrollHeight; }", selector)
        else:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        return _text("Scrolled to bottom")

    if to_top:
        if selector:
            await page.evaluate("(sel) => { const el = document.querySelector(sel); if (el) el.scrollTop = 0; }", selector)
        else:
            await page.evaluate("window.scrollTo(0, 0)")
        return _text("Scrolled to top")

    dx, dy = 0, 0
    if direction == "down": dy = amount
    elif direction == "up": dy = -amount
    elif direction == "right": dx = amount
    elif direction == "left": dx = -amount

    if selector:
        result = await page.evaluate(f"(sel) => {{ const el = document.querySelector(sel); if (!el) return 'not found'; el.scrollBy({dx}, {dy}); return 'ok'; }}", selector)
        return _text(f"Scrolled {selector} {direction} {amount}px: {result}")
    else:
        await page.evaluate(f"window.scrollBy({dx}, {dy})")
        return _text(f"Scrolled page {direction} {amount}px")


# ======================== 等待处理函数 ========================

async def handle_wait_for(args: dict):
    """等待指定条件满足，支持多种模式组合使用。"""
    page = await ensure_browser()
    selector = args.get("selector")
    state = args.get("state", "visible")
    text = args.get("text")
    idle = args.get("idle", False)
    timeout = args.get("timeout", 30000)

    results = []

    if idle:
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout)
            results.append("Network idle")
        except Exception:
            results.append("Network idle timeout")

    if selector:
        try:
            await page.wait_for_selector(selector, state=state, timeout=timeout)
            results.append(f"Element {selector} is {state}")
        except Exception:
            results.append(f"Timeout waiting for {selector} ({state})")

    if text:
        try:
            await page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout)
            results.append(f"Text \"{text}\" appeared")
        except Exception:
            results.append(f"Timeout waiting for text \"{text}\"")

    # 如果没有指定任何条件，则纯等待指定时间
    if not results:
        await asyncio.sleep(timeout / 1000)
        results.append(f"Waited {timeout}ms")

    return _text(" | ".join(results))


# ======================== JavaScript 执行 ========================

async def handle_execute_js(args: dict):
    """在页面上下文中执行 JavaScript 代码并返回结果。"""
    page = await ensure_browser()
    script = args["script"]
    result = await page.evaluate(script)
    if isinstance(result, (dict, list)):
        return _text(json.dumps(result, ensure_ascii=False, indent=2))
    return _text(f"Result: {result}")


# ======================== 多标签页管理 ========================

async def handle_new_tab(args: dict):
    """在当前浏览器上下文中打开新标签页。"""
    global _active_tab
    page = await ensure_browser()
    ctx = _contexts.get(_active_tab)
    if not ctx:
        return _text("Error: no browser context")

    new_page = await ctx.new_page()
    tab_id = f"tab-{id(new_page)}"
    _pages[tab_id] = new_page
    _active_tab = tab_id

    url = args.get("url")
    if url:
        await new_page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        title = await new_page.title()
        return _text(f"New tab {tab_id}: {url} ({title})")
    return _text(f"New tab opened: {tab_id}")


async def handle_list_tabs(args: dict):
    """列出所有未关闭的标签页信息。"""
    lines = []
    for tid, page in _pages.items():
        if page.is_closed():
            continue
        try:
            title = await page.title()
        except Exception:
            title = "(error)"
        active = " [ACTIVE]" if tid == _active_tab else ""
        lines.append(f"  {tid}: {page.url} — {title}{active}")
    return _text(f"Open tabs ({len(lines)}):\n" + "\n".join(lines))


async def handle_switch_tab(args: dict):
    """切换到指定 ID 的标签页。"""
    global _active_tab
    tab_id = args["tab_id"]
    if tab_id not in _pages:
        return _text(f"Error: tab {tab_id} not found")
    if _pages[tab_id].is_closed():
        return _text(f"Error: tab {tab_id} is closed")
    _active_tab = tab_id
    page = _pages[tab_id]
    title = await page.title()
    return _text(f"Switched to {tab_id}: {page.url} ({title})")


async def handle_close_tab(args: dict):
    """关闭标签页，如果关闭的是当前活跃标签页则自动切换到其他标签页。"""
    global _active_tab
    tab_id = args.get("tab_id", _active_tab)
    if not tab_id or tab_id not in _pages:
        return _text(f"Error: tab {tab_id} not found")

    page = _pages.pop(tab_id)
    ctx = _contexts.pop(tab_id, None)
    if not page.is_closed():
        await page.close()

    if tab_id == _active_tab:
        remaining = [t for t in _pages if not _pages[t].is_closed()]
        _active_tab = remaining[0] if remaining else None

    return _text(f"Closed tab: {tab_id}" + (f" → active: {_active_tab}" if _active_tab else ""))


# ======================== PDF 导出 ========================

async def handle_save_pdf(args: dict):
    """将当前页面导出为 PDF 文件（仅无头模式支持）。"""
    page = await ensure_browser()
    path = args["path"]
    fmt = args.get("format", "A4")
    landscape = args.get("landscape", False)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    await page.pdf(path=path, format=fmt, landscape=landscape)
    return _text(f"PDF saved: {path}")


# ======================== Cookie 管理 ========================

async def handle_get_cookies(args: dict):
    """获取当前浏览器上下文中的 Cookie。"""
    page = await ensure_browser()
    ctx = page.context
    urls_str = args.get("urls")
    if urls_str:
        urls = [u.strip() for u in urls_str.split(",")]
        cookies = await ctx.cookies(urls)
    else:
        cookies = await ctx.cookies()
    return _text(json.dumps(cookies, ensure_ascii=False, indent=2))


async def handle_set_cookie(args: dict):
    """在当前浏览器上下文中设置 Cookie。"""
    page = await ensure_browser()
    ctx = page.context
    cookie = {
        "name": args["name"],
        "value": args["value"],
        "path": args.get("path", "/"),
        "httpOnly": args.get("httpOnly", False),
        "secure": args.get("secure", False),
    }
    url = args.get("url")
    domain = args.get("domain")
    if url:
        cookie["url"] = url
    elif domain:
        cookie["domain"] = domain
    else:
        cookie["url"] = page.url

    await ctx.add_cookies([cookie])
    return _text(f"Cookie set: {cookie['name']}={cookie['value']}")


# ======================== 生命周期 ========================

async def handle_close(args: dict):
    """关闭浏览器并释放所有资源。"""
    await close_browser()
    return _text("Browser closed")


# ---------------------------------------------------------------------------
# 工具名 → 处理函数的映射表
# ---------------------------------------------------------------------------
HANDLERS = {
    "browser_open": handle_open,
    "browser_go_back": handle_go_back,
    "browser_go_forward": handle_go_forward,
    "browser_refresh": handle_refresh,
    "browser_click": handle_click,
    "browser_hover": handle_hover,
    "browser_type": handle_type,
    "browser_keyboard": handle_keyboard,
    "browser_select": handle_select,
    "browser_drag": handle_drag,
    "browser_upload_file": handle_upload_file,
    "browser_upload_via_dialog": handle_upload_via_dialog,
    "browser_screenshot": handle_screenshot,
    "browser_get_text": handle_get_text,
    "browser_snapshot": handle_snapshot,
    "browser_get_url": handle_get_url,
    "browser_scroll": handle_scroll,
    "browser_wait_for": handle_wait_for,
    "browser_execute_js": handle_execute_js,
    "browser_new_tab": handle_new_tab,
    "browser_list_tabs": handle_list_tabs,
    "browser_switch_tab": handle_switch_tab,
    "browser_close_tab": handle_close_tab,
    "browser_save_pdf": handle_save_pdf,
    "browser_get_cookies": handle_get_cookies,
    "browser_set_cookie": handle_set_cookie,
    "browser_close": handle_close,
}


# ---------------------------------------------------------------------------
# 辅助函数：提取页面可交互元素
# ---------------------------------------------------------------------------
async def _get_interactive_elements(page, filter_selector: str | None = None) -> list[dict]:
    """在页面中提取可交互元素列表（最多 100 个），用于 browser_open 的返回信息。"""
    js = """(filterSel) => {
        const sel = filterSel || 'a, button, input, select, textarea, [role="button"], [role="radio"], [role="checkbox"], [onclick], [class*="upload"], label[for], [contenteditable="true"]';
        return Array.from(document.querySelectorAll(sel)).slice(0, 100).map((el) => {
            const tag = el.tagName.toLowerCase();
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) return null;
            const id = el.id || '';
            const text = (el.textContent || el.value || el.placeholder || '').trim().substring(0, 100);
            let selector = '';
            if (id) selector = '#' + CSS.escape(id);
            else {
                const name = el.getAttribute('name');
                if (name) selector = tag + '[name="' + name + '"]';
                else selector = tag + ':nth-of-type(' + (Array.from(el.parentElement?.querySelectorAll(tag) || []).indexOf(el) + 1) + ')';
            }
            return { tag, id, text, selector };
        }).filter(Boolean);
    }"""
    return await page.evaluate(js, filter_selector)


# ---------------------------------------------------------------------------
# 程序入口
# ---------------------------------------------------------------------------
async def main():
    """启动 MCP Server，通过 stdio 与客户端通信。"""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
