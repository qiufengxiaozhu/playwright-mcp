"""
Playwright MCP Server — Atomic browser tools for AI agents (Cursor / Claude / etc.)

Design philosophy:
  No built-in LLM. The AI agent (e.g. Cursor) acts as the "brain",
  while this MCP server provides atomic browser operations.
  The agent decides what to do next based on screenshots / DOM snapshots.

Browser modes (env BROWSER_MODE):
  visible  → Playwright built-in Chromium, non-headless (default, WSLg displays on Windows)
  headless → Playwright built-in Chromium, headless

Configuration (env vars):
  BROWSER_MODE     — visible (default) / headless
  VIEWPORT_WIDTH   — viewport width  (default 1920)
  VIEWPORT_HEIGHT  — viewport height (default 1080)
  BROWSER_LOCALE   — locale string   (default zh-CN)
  BROWSER_TIMEOUT  — default timeout in ms (default 30000)
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
# Configuration from environment
# ---------------------------------------------------------------------------
VIEWPORT_W = int(os.getenv("VIEWPORT_WIDTH", "1920"))
VIEWPORT_H = int(os.getenv("VIEWPORT_HEIGHT", "1080"))
LOCALE = os.getenv("BROWSER_LOCALE", "zh-CN")
DEFAULT_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "30000"))

# ---------------------------------------------------------------------------
# Browser lifecycle
# ---------------------------------------------------------------------------
_playwright = None
_browser = None
_contexts: dict[str, "BrowserContext"] = {}
_pages: dict[str, "Page"] = {}
_active_tab: str | None = None


async def ensure_browser():
    """Launch browser if needed, return the active page."""
    global _playwright, _browser, _active_tab

    if _active_tab and _active_tab in _pages:
        page = _pages[_active_tab]
        if not page.is_closed():
            return page

    from playwright.async_api import async_playwright

    if _playwright is None:
        _playwright = await async_playwright().start()

    if _browser is None or not _browser.is_connected():
        mode = os.getenv("BROWSER_MODE", "visible").lower()
        headless = mode == "headless"

        _browser = await _playwright.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
            ],
        )

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
    """Close browser and clean up all state."""
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
# MCP Server
# ---------------------------------------------------------------------------
app = Server("playwright-mcp")


def _prop(name: str, typ: str, desc: str, **kw) -> dict:
    """Helper to build a JSON Schema property."""
    p = {"type": typ, "description": desc}
    p.update(kw)
    return name, p


def _tool(name: str, desc: str, props: list[tuple], required: list[str] | None = None) -> types.Tool:
    """Helper to build a Tool definition."""
    properties = {}
    for pname, pdef in props:
        properties[pname] = pdef
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return types.Tool(name=name, description=desc, inputSchema=schema)


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        # --- Navigation ---
        _tool("browser_open",
              "Navigate to a URL. Returns page title and interactive elements.",
              [_prop("url", "string", "Target URL"),
               _prop("wait_until", "string", "Wait condition: domcontentloaded / networkidle / load / commit", default="domcontentloaded")],
              required=["url"]),

        _tool("browser_go_back",
              "Navigate back in browser history.",
              [_prop("wait_until", "string", "Wait condition after navigation", default="domcontentloaded")]),

        _tool("browser_go_forward",
              "Navigate forward in browser history.",
              [_prop("wait_until", "string", "Wait condition after navigation", default="domcontentloaded")]),

        _tool("browser_refresh",
              "Refresh the current page.",
              [_prop("wait_until", "string", "Wait condition after refresh", default="domcontentloaded")]),

        # --- Interaction ---
        _tool("browser_click",
              "Click an element by CSS selector or text content. Supports force-click to bypass disabled/hidden checks.",
              [_prop("selector", "string", "CSS selector"),
               _prop("text", "string", "Text content to match (when selector is hard to write)"),
               _prop("force", "boolean", "Force click, bypass actionability checks (default false)", default=False),
               _prop("double_click", "boolean", "Double click (default false)", default=False),
               _prop("right_click", "boolean", "Right click / context menu (default false)", default=False),
               _prop("position_x", "number", "Click at specific x offset within element"),
               _prop("position_y", "number", "Click at specific y offset within element"),
               _prop("timeout", "integer", "Timeout in ms (default 5000)", default=5000)]),

        _tool("browser_hover",
              "Hover over an element. Useful for triggering tooltips, dropdown menus, etc.",
              [_prop("selector", "string", "CSS selector"),
               _prop("text", "string", "Text content to match"),
               _prop("timeout", "integer", "Timeout in ms (default 5000)", default=5000)]),

        _tool("browser_type",
              "Type text into an input field.",
              [_prop("selector", "string", "CSS selector of the input"),
               _prop("text", "string", "Text to type"),
               _prop("clear", "boolean", "Clear field first (default true, uses fill; false uses sequential keystrokes)", default=True),
               _prop("press_enter", "boolean", "Press Enter after typing (default false)", default=False),
               _prop("timeout", "integer", "Timeout in ms (default 5000)", default=5000)],
              required=["selector", "text"]),

        _tool("browser_keyboard",
              "Press keyboard keys. Supports special keys and shortcuts (e.g. 'Enter', 'Escape', 'Control+a', 'Meta+c').",
              [_prop("key", "string", "Key or key combination to press (Playwright key names)"),
               _prop("selector", "string", "Focus this element first (optional)")],
              required=["key"]),

        _tool("browser_select",
              "Select option(s) in a <select> dropdown.",
              [_prop("selector", "string", "CSS selector of the select element"),
               _prop("value", "string", "Option value to select"),
               _prop("label", "string", "Option label text to select"),
               _prop("index", "integer", "Option index to select")],
              required=["selector"]),

        _tool("browser_drag",
              "Drag an element to a target position or element.",
              [_prop("source_selector", "string", "CSS selector of the element to drag"),
               _prop("target_selector", "string", "CSS selector of the drop target"),
               _prop("target_x", "number", "Drop at absolute page x coordinate (alternative to target_selector)"),
               _prop("target_y", "number", "Drop at absolute page y coordinate")],
              required=["source_selector"]),

        # --- File upload ---
        _tool("browser_upload_file",
              "Upload file to an <input type='file'> element.",
              [_prop("selector", "string", "CSS selector of the file input"),
               _prop("file_path", "string", "File path to upload (supports multiple paths separated by comma)")],
              required=["selector", "file_path"]),

        _tool("browser_upload_via_dialog",
              "Upload file by intercepting the file chooser dialog. Clicks a trigger element, then intercepts the file dialog. Works for dynamic file inputs created by JavaScript.",
              [_prop("trigger_selector", "string", "CSS selector of the trigger element (upload button/area)"),
               _prop("trigger_text", "string", "Text of the trigger element (alternative to selector)"),
               _prop("file_path", "string", "File path to upload"),
               _prop("timeout", "integer", "Timeout for file dialog in ms (default 10000)", default=10000)],
              required=["file_path"]),

        # --- Observation ---
        _tool("browser_screenshot",
              "Take a screenshot of the current page or a specific element.",
              [_prop("wait_for_idle", "boolean", "Wait for network idle before screenshot (default false)", default=False),
               _prop("wait_ms", "integer", "Extra wait time in ms before screenshot (default 0)", default=0),
               _prop("full_page", "boolean", "Capture full scrollable page (default false)", default=False),
               _prop("selector", "string", "Capture only this element (CSS selector)")]),

        _tool("browser_get_text",
              "Get text content of the page or a specific element.",
              [_prop("selector", "string", "CSS selector (optional, defaults to entire page)")]),

        _tool("browser_snapshot",
              "Get a structured DOM snapshot: interactive elements + optional text nodes. Returns JSON with tag, text, selector, attributes, bounding box.",
              [_prop("selector", "string", "Scope to this CSS selector (optional)"),
               _prop("include_text_nodes", "boolean", "Include visible text nodes (default false)", default=False),
               _prop("max_elements", "integer", "Max interactive elements to return (default 100)", default=100)]),

        _tool("browser_get_url",
              "Get the current page URL and title.",
              []),

        # --- Scroll ---
        _tool("browser_scroll",
              "Scroll the page or a specific container.",
              [_prop("direction", "string", "Scroll direction: down / up / left / right (default down)", default="down"),
               _prop("amount", "integer", "Scroll pixels (default 500)", default=500),
               _prop("selector", "string", "Container CSS selector (optional, scrolls page if omitted)"),
               _prop("to_bottom", "boolean", "Scroll to the very bottom (default false)", default=False),
               _prop("to_top", "boolean", "Scroll to the very top (default false)", default=False)]),

        # --- Wait ---
        _tool("browser_wait_for",
              "Wait for a condition: element visible/hidden, text appears, network idle, or just a timeout.",
              [_prop("selector", "string", "Wait for this element (CSS selector)"),
               _prop("state", "string", "Element state: visible / hidden / attached / detached (default visible)", default="visible"),
               _prop("text", "string", "Wait for this text to appear on page"),
               _prop("idle", "boolean", "Wait for network idle (default false)", default=False),
               _prop("timeout", "integer", "Timeout in ms (default 30000)", default=30000)]),

        # --- JavaScript ---
        _tool("browser_execute_js",
              "Execute JavaScript in the page context and return the result. Use IIFE pattern: (() => { ... })()",
              [_prop("script", "string", "JavaScript code to execute")],
              required=["script"]),

        # --- Tabs ---
        _tool("browser_new_tab",
              "Open a new browser tab and optionally navigate to a URL.",
              [_prop("url", "string", "URL to navigate to (optional)")]),

        _tool("browser_list_tabs",
              "List all open browser tabs with their IDs, URLs, and titles.",
              []),

        _tool("browser_switch_tab",
              "Switch to a different browser tab by its ID.",
              [_prop("tab_id", "string", "Tab ID to switch to")],
              required=["tab_id"]),

        _tool("browser_close_tab",
              "Close a browser tab. Closes current tab if no tab_id specified.",
              [_prop("tab_id", "string", "Tab ID to close (optional, defaults to current)")]),

        # --- PDF export ---
        _tool("browser_save_pdf",
              "Export the current page as a PDF file.",
              [_prop("path", "string", "File path to save the PDF"),
               _prop("format", "string", "Paper format: A4 / Letter / Legal / etc. (default A4)", default="A4"),
               _prop("landscape", "boolean", "Landscape orientation (default false)", default=False)],
              required=["path"]),

        # --- Cookie management ---
        _tool("browser_get_cookies",
              "Get all cookies for the current page.",
              [_prop("urls", "string", "Comma-separated URLs to get cookies for (optional, defaults to current page)")]),

        _tool("browser_set_cookie",
              "Set a cookie.",
              [_prop("name", "string", "Cookie name"),
               _prop("value", "string", "Cookie value"),
               _prop("url", "string", "URL the cookie belongs to (optional, uses current page URL)"),
               _prop("domain", "string", "Cookie domain (optional)"),
               _prop("path", "string", "Cookie path (default /)", default="/"),
               _prop("httpOnly", "boolean", "HTTP only flag", default=False),
               _prop("secure", "boolean", "Secure flag", default=False)],
              required=["name", "value"]),

        # --- Lifecycle ---
        _tool("browser_close",
              "Close the browser and clean up all resources.",
              []),
    ]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent | types.ImageContent]:
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
    return [types.TextContent(type="text", text=msg)]


# --- Navigation ---

async def handle_open(args: dict):
    url = args["url"]
    wait_until = args.get("wait_until", "domcontentloaded")
    page = await ensure_browser()
    await page.goto(url, wait_until=wait_until, timeout=DEFAULT_TIMEOUT)
    await asyncio.sleep(1)
    title = await page.title()

    elements = await _get_interactive_elements(page)
    summary = f"Navigated to: {page.url}\nTitle: {title}\n\nInteractive elements ({len(elements)}):\n"
    for i, el in enumerate(elements[:30]):
        summary += f"  [{i}] <{el['tag']}> {el['text'][:60]}  → {el['selector']}\n"
    if len(elements) > 30:
        summary += f"  ... and {len(elements) - 30} more\n"
    return _text(summary)


async def handle_go_back(args: dict):
    page = await ensure_browser()
    wait_until = args.get("wait_until", "domcontentloaded")
    await page.go_back(wait_until=wait_until, timeout=DEFAULT_TIMEOUT)
    title = await page.title()
    return _text(f"Navigated back → {page.url} ({title})")


async def handle_go_forward(args: dict):
    page = await ensure_browser()
    wait_until = args.get("wait_until", "domcontentloaded")
    await page.go_forward(wait_until=wait_until, timeout=DEFAULT_TIMEOUT)
    title = await page.title()
    return _text(f"Navigated forward → {page.url} ({title})")


async def handle_refresh(args: dict):
    page = await ensure_browser()
    wait_until = args.get("wait_until", "domcontentloaded")
    await page.reload(wait_until=wait_until, timeout=DEFAULT_TIMEOUT)
    title = await page.title()
    return _text(f"Refreshed → {page.url} ({title})")


# --- Interaction ---

async def handle_click(args: dict):
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
    page = await ensure_browser()
    selector = args["selector"]
    text = args["text"]
    clear = args.get("clear", True)
    press_enter = args.get("press_enter", False)
    timeout = args.get("timeout", 5000)

    if clear:
        await page.fill(selector, text, timeout=timeout)
    else:
        await page.locator(selector).first.type(text, timeout=timeout)

    if press_enter:
        await page.locator(selector).first.press("Enter")

    return _text(f"Typed {len(text)} chars into {selector}")


async def handle_keyboard(args: dict):
    page = await ensure_browser()
    key = args["key"]
    selector = args.get("selector")

    if selector:
        await page.locator(selector).first.press(key)
    else:
        await page.keyboard.press(key)
    return _text(f"Pressed: {key}")


async def handle_select(args: dict):
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
    page = await ensure_browser()
    source = args["source_selector"]
    target_sel = args.get("target_selector")
    target_x = args.get("target_x")
    target_y = args.get("target_y")

    if target_sel:
        await page.drag_and_drop(source, target_sel, timeout=10000)
        return _text(f"Dragged {source} → {target_sel}")
    elif target_x is not None and target_y is not None:
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


# --- File upload ---

async def handle_upload_file(args: dict):
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


# --- Observation ---

async def handle_screenshot(args: dict):
    page = await ensure_browser()
    wait_for_idle = args.get("wait_for_idle", False)
    wait_ms = args.get("wait_ms", 0)
    full_page = args.get("full_page", False)
    selector = args.get("selector")

    if wait_for_idle:
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

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
    page = await ensure_browser()
    selector = args.get("selector")
    if selector:
        text = await page.locator(selector).first.text_content(timeout=5000) or ""
    else:
        text = await page.text_content("body") or ""
    text = text.strip()[:5000]
    return _text(text)


async def handle_snapshot(args: dict):
    page = await ensure_browser()
    scope_selector = args.get("selector")
    include_text = args.get("include_text_nodes", False)
    max_elements = args.get("max_elements", 100)

    js = """(opts) => {
        const scopeSel = opts.scopeSelector;
        const includeText = opts.includeText;
        const maxEls = opts.maxElements;
        const root = scopeSel ? document.querySelector(scopeSel) : document.body;
        if (!root) return { error: 'Element not found: ' + scopeSel };

        const interactiveSel = 'a, button, input, select, textarea, [role="button"], [role="radio"], [role="checkbox"], [role="tab"], [role="switch"], [role="listbox"], [role="option"], [role="menuitem"], [onclick], [class*="upload"], [class*="Upload"], label[for], [contenteditable="true"]';
        const elements = Array.from(root.querySelectorAll(interactiveSel));

        const results = elements.slice(0, maxEls).map((el) => {
            const tag = el.tagName.toLowerCase();
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) return null;

            const item = {
                tag,
                text: (el.textContent || el.value || el.placeholder || '').trim().substring(0, 100),
                visible: rect.width > 0 && rect.height > 0,
                rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
            };

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
    page = await ensure_browser()
    title = await page.title()
    return _text(f"URL: {page.url}\nTitle: {title}")


# --- Scroll ---

async def handle_scroll(args: dict):
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


# --- Wait ---

async def handle_wait_for(args: dict):
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

    if not results:
        await asyncio.sleep(timeout / 1000)
        results.append(f"Waited {timeout}ms")

    return _text(" | ".join(results))


# --- JavaScript ---

async def handle_execute_js(args: dict):
    page = await ensure_browser()
    script = args["script"]
    result = await page.evaluate(script)
    if isinstance(result, (dict, list)):
        return _text(json.dumps(result, ensure_ascii=False, indent=2))
    return _text(f"Result: {result}")


# --- Tabs ---

async def handle_new_tab(args: dict):
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


# --- PDF ---

async def handle_save_pdf(args: dict):
    page = await ensure_browser()
    path = args["path"]
    fmt = args.get("format", "A4")
    landscape = args.get("landscape", False)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    await page.pdf(path=path, format=fmt, landscape=landscape)
    return _text(f"PDF saved: {path}")


# --- Cookies ---

async def handle_get_cookies(args: dict):
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


# --- Lifecycle ---

async def handle_close(args: dict):
    await close_browser()
    return _text("Browser closed")


# ---------------------------------------------------------------------------
# Handler registry
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
# Interactive elements helper
# ---------------------------------------------------------------------------
async def _get_interactive_elements(page, filter_selector: str | None = None) -> list[dict]:
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
# Entry point
# ---------------------------------------------------------------------------
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
