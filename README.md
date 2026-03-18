# Playwright MCP Server

**English** | [中文](README_CN.md)

A lightweight [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that gives AI agents — such as **Cursor**, **Claude Desktop**, **Windsurf**, or any MCP-compatible client — full control over a real Chromium browser through **27 atomic tools**.

> **No LLM API key needed.** Your AI agent *is* the brain; this server is the hands.

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-green)

---

## Why This Project?

Most browser-automation MCP servers either bundle an LLM (requiring an API key and adding latency) or only expose a handful of high-level actions. **Playwright MCP** takes a different approach:

| Feature | Playwright MCP | Others |
|---|---|---|
| LLM dependency | **None** — the AI agent decides | Requires OpenAI / Anthropic key |
| Tool granularity | **27 atomic tools** — click, type, hover, drag, keyboard, tabs, cookies, PDF export… | Typically 5–10 high-level actions |
| File upload | **Two strategies** — direct `setInputFiles` + file-chooser dialog interception | Rarely supported |
| Multi-tab | **Full support** — new/list/switch/close tabs | Usually single-page |
| DOM snapshot | **Structured JSON** — tag, text, selector, bounding box, ARIA attributes | Accessibility tree only (often empty for SPAs) |
| Visual mode | **Non-headless by default** — watch the AI operate in real time | Usually headless |
| Dependencies | **2 packages** — `mcp` + `playwright` | Heavy dependency trees |
| Configuration | **Environment variables** — viewport, locale, timeout, browser mode | Hard-coded |

---

## Quick Start

### 1. Install

```bash
# Clone
git clone https://github.com/qiufengxiaozhu/playwright-mcp.git
cd playwright-mcp

# Create virtual environment and install dependencies
uv venv && source .venv/bin/activate
uv pip install -e .

# Install Chromium browser
python -m playwright install chromium
```

Or with plain pip:

```bash
pip install -e .
python -m playwright install chromium
```

### 2. Configure in Cursor

Add to your `.cursor/mcp.json` (project-level or global `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "playwright-mcp": {
      "command": "/path/to/playwright-mcp/.venv/bin/python",
      "args": ["/path/to/playwright-mcp/mcp_server.py"],
      "env": {
        "BROWSER_MODE": "visible"
      }
    }
  }
}
```

### 3. Use

Just ask Cursor naturally:

> "Open https://example.com, click the Login button, type my username, and take a screenshot."

Cursor will automatically call the appropriate MCP tools in sequence.

---

## All 27 Tools

### Navigation
| Tool | Description |
|---|---|
| `browser_open` | Navigate to a URL, returns page title + interactive elements |
| `browser_go_back` | Navigate back in history |
| `browser_go_forward` | Navigate forward in history |
| `browser_refresh` | Refresh the current page |

### Interaction
| Tool | Description |
|---|---|
| `browser_click` | Click by CSS selector or text; supports force, double-click, right-click, position offset |
| `browser_hover` | Hover over an element (triggers tooltips, dropdowns) |
| `browser_type` | Type text into inputs; supports clear-first, press-enter-after |
| `browser_keyboard` | Press keys or shortcuts (`Enter`, `Escape`, `Control+a`, `Meta+c`) |
| `browser_select` | Select option in `<select>` by value, label, or index |
| `browser_drag` | Drag element to target element or coordinates |

### File Upload
| Tool | Description |
|---|---|
| `browser_upload_file` | Upload to `<input type="file">` (supports multiple files) |
| `browser_upload_via_dialog` | Intercept file-chooser dialog — works with dynamic/JS-created file inputs |

### Observation
| Tool | Description |
|---|---|
| `browser_screenshot` | Screenshot viewport, full page, or specific element; optional wait-for-idle |
| `browser_get_text` | Get text content of page or element |
| `browser_snapshot` | Structured DOM snapshot — interactive elements + text nodes as JSON |
| `browser_get_url` | Get current URL and title |

### Scroll
| Tool | Description |
|---|---|
| `browser_scroll` | Scroll page or container — directional, to-top, to-bottom |

### Wait
| Tool | Description |
|---|---|
| `browser_wait_for` | Wait for element, text, network idle, or timeout |

### JavaScript
| Tool | Description |
|---|---|
| `browser_execute_js` | Execute arbitrary JavaScript and return results |

### Multi-Tab
| Tool | Description |
|---|---|
| `browser_new_tab` | Open new tab, optionally navigate to URL |
| `browser_list_tabs` | List all open tabs with IDs, URLs, titles |
| `browser_switch_tab` | Switch to a tab by ID |
| `browser_close_tab` | Close a tab |

### Export
| Tool | Description |
|---|---|
| `browser_save_pdf` | Export current page as PDF (configurable format, orientation) |

### Cookies
| Tool | Description |
|---|---|
| `browser_get_cookies` | Get cookies for current page or specific URLs |
| `browser_set_cookie` | Set a cookie with full options |

### Lifecycle
| Tool | Description |
|---|---|
| `browser_close` | Close browser and clean up resources |

---

## Configuration

All configuration is done through environment variables:

| Variable | Default | Description |
|---|---|---|
| `BROWSER_MODE` | `visible` | `visible` = non-headless Chromium (watch it work!), `headless` = headless |
| `VIEWPORT_WIDTH` | `1920` | Browser viewport width |
| `VIEWPORT_HEIGHT` | `1080` | Browser viewport height |
| `BROWSER_LOCALE` | `zh-CN` | Browser locale (affects language, date formats) |
| `BROWSER_TIMEOUT` | `30000` | Default navigation timeout in milliseconds |

Example with custom config:

```json
{
  "mcpServers": {
    "playwright-mcp": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/mcp_server.py"],
      "env": {
        "BROWSER_MODE": "visible",
        "VIEWPORT_WIDTH": "1440",
        "VIEWPORT_HEIGHT": "900",
        "BROWSER_LOCALE": "en-US",
        "BROWSER_TIMEOUT": "60000"
      }
    }
  }
}
```

---

## Design Philosophy

```
┌─────────────────────────────────────────────────┐
│  AI Agent (Cursor / Claude / Windsurf / ...)    │
│                                                 │
│  "I see a login button in the screenshot.       │
│   Let me click it, then type the username..."   │
│                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │screenshot│→ │  decide  │→ │  action  │      │
│  │ snapshot │  │  (LLM)   │  │ (tool)   │      │
│  └──────────┘  └──────────┘  └──────────┘      │
└─────────────────┬───────────────────────────────┘
                  │ MCP Protocol (stdio)
┌─────────────────▼───────────────────────────────┐
│  Playwright MCP Server                          │
│                                                 │
│  27 atomic tools:                               │
│  click, type, screenshot, snapshot, upload,     │
│  hover, drag, keyboard, tabs, cookies, PDF...   │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  Playwright → Chromium Browser           │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

**Key insight:** The AI agent already understands web pages from screenshots and DOM structure. It doesn't need another LLM layer — it just needs reliable, granular browser controls. That's exactly what this server provides.

---

## Real-World Example

Here's what happened when Cursor used this MCP to add watermarks to documents:

1. **`browser_open`** → navigated to the web app, got interactive elements list
2. **`browser_click`** → clicked "test account to login"
3. **`browser_click`** → clicked "Login" button
4. **`browser_screenshot`** → confirmed login success, saw the dashboard
5. **`browser_upload_via_dialog`** → uploaded a DOCX file by intercepting the file chooser
6. **`browser_execute_js`** → modified watermark config (font, color) in a textarea
7. **`browser_click`** → clicked "Apply"
8. **`browser_screenshot`** → captured the result with watermark rendered

All of this was done **visually** — the user watched the browser operate in real time.

---

## Comparison with Cursor's Built-in Browser

Cursor ships with `cursor-ide-browser`, which is great for simple pages. However, for complex SPAs (React, Vue, Angular):

- **Accessibility tree is often empty** → `browser_snapshot` returns nothing useful
- **No file upload support** → can't test upload flows
- **No `evaluate` / JS execution** → can't interact with React state
- **No multi-tab** → can't test multi-window workflows

This MCP server fills those gaps while working alongside `cursor-ide-browser`.

---

## WSL + WSLg Setup

If you're on Windows with WSL2, the `visible` mode works through **WSLg** — the browser window appears on your Windows desktop automatically. No extra configuration needed.

If WSLg is not available, use `BROWSER_MODE=headless` and rely on `browser_screenshot` for visual feedback.

---

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

---

## License

MIT — see [LICENSE](LICENSE) for details.
