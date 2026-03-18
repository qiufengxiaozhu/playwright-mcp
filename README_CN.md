# Playwright MCP Server

[English](README.md) | **中文**

一个轻量级的 [MCP（模型上下文协议）](https://modelcontextprotocol.io/)服务器，为 AI 智能体（如 **Cursor**、**Claude Desktop**、**Windsurf** 等 MCP 兼容客户端）提供对真实 Chromium 浏览器的完整控制，共 **27 个原子级工具**。

> **无需 LLM API Key。** 你的 AI 智能体就是"大脑"，本服务器是"双手"。

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-green)

---

## 为什么选择这个项目？

市面上大多数浏览器自动化 MCP 服务器要么内置 LLM（需要 API Key 且增加延迟），要么只暴露少量高级操作。**Playwright MCP** 采用了完全不同的方案：

| 特性 | Playwright MCP | 其他方案 |
|---|---|---|
| LLM 依赖 | **无** — AI 智能体自行决策 | 需要 OpenAI / Anthropic Key |
| 工具粒度 | **27 个原子工具** — 点击、输入、悬停、拖拽、键盘、标签页、Cookie、PDF 导出… | 通常 5–10 个高级操作 |
| 文件上传 | **两种策略** — 直接 `setInputFiles` + 文件对话框拦截 | 很少支持 |
| 多标签页 | **完整支持** — 新建/列出/切换/关闭标签页 | 通常只支持单页面 |
| DOM 快照 | **结构化 JSON** — 标签名、文本、选择器、位置尺寸、ARIA 属性 | 仅有 accessibility tree（SPA 应用经常为空） |
| 可视化模式 | **默认有头模式** — 实时观看 AI 操作过程 | 通常是无头模式 |
| 依赖数量 | **仅 2 个包** — `mcp` + `playwright` | 依赖树庞大 |
| 可配置性 | **环境变量** — 视口、语言、超时、浏览器模式均可配置 | 硬编码 |

---

## 快速开始

### 1. 安装

```bash
# 克隆仓库
git clone https://github.com/qiufengxiaozhu/playwright-mcp.git
cd playwright-mcp

# 创建虚拟环境并安装依赖
uv venv && source .venv/bin/activate
uv pip install -e .

# 安装 Chromium 浏览器
python -m playwright install chromium
```

也可以用 pip：

```bash
pip install -e .
python -m playwright install chromium
```

### 2. 在 Cursor 中配置

在 `.cursor/mcp.json`（项目级或全局 `~/.cursor/mcp.json`）中添加：

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

### 3. 使用

直接用自然语言告诉 Cursor：

> "打开 https://example.com，点击登录按钮，输入用户名，然后截个图。"

Cursor 会自动按顺序调用对应的 MCP 工具完成操作。

---

## 全部 27 个工具

### 导航
| 工具 | 说明 |
|---|---|
| `browser_open` | 导航到 URL，返回页面标题 + 可交互元素列表 |
| `browser_go_back` | 浏览器后退 |
| `browser_go_forward` | 浏览器前进 |
| `browser_refresh` | 刷新当前页面 |

### 交互
| 工具 | 说明 |
|---|---|
| `browser_click` | 点击元素，支持 CSS 选择器或文本匹配，支持强制点击、双击、右键、坐标偏移 |
| `browser_hover` | 鼠标悬停（触发 tooltip、下拉菜单） |
| `browser_type` | 输入文字，支持先清空再输入、输入后按回车 |
| `browser_keyboard` | 模拟按键或快捷键（`Enter`、`Escape`、`Control+a`、`Meta+c`） |
| `browser_select` | 在 `<select>` 下拉框中选择，支持按 value、label 或 index |
| `browser_drag` | 拖拽元素到目标元素或指定坐标 |

### 文件上传
| 工具 | 说明 |
|---|---|
| `browser_upload_file` | 上传文件到 `<input type="file">`（支持多文件） |
| `browser_upload_via_dialog` | 拦截文件选择对话框上传 — 适用于 JS 动态创建的文件输入 |

### 观察
| 工具 | 说明 |
|---|---|
| `browser_screenshot` | 截图：视口、全页、或指定元素；可选等待网络空闲 |
| `browser_get_text` | 获取页面或元素的文本内容 |
| `browser_snapshot` | 结构化 DOM 快照 — 可交互元素 + 文本节点，返回 JSON |
| `browser_get_url` | 获取当前 URL 和标题 |

### 滚动
| 工具 | 说明 |
|---|---|
| `browser_scroll` | 滚动页面或容器 — 支持方向滚动、一键到顶/到底 |

### 等待
| 工具 | 说明 |
|---|---|
| `browser_wait_for` | 等待元素出现/消失、文本出现、网络空闲、或纯等待 |

### JavaScript
| 工具 | 说明 |
|---|---|
| `browser_execute_js` | 在页面中执行 JavaScript 并返回结果 |

### 多标签页
| 工具 | 说明 |
|---|---|
| `browser_new_tab` | 打开新标签页，可选导航到 URL |
| `browser_list_tabs` | 列出所有标签页的 ID、URL、标题 |
| `browser_switch_tab` | 切换到指定标签页 |
| `browser_close_tab` | 关闭标签页 |

### 导出
| 工具 | 说明 |
|---|---|
| `browser_save_pdf` | 将当前页面导出为 PDF（可配置纸张格式和方向） |

### Cookie
| 工具 | 说明 |
|---|---|
| `browser_get_cookies` | 获取当前页面或指定 URL 的 Cookie |
| `browser_set_cookie` | 设置 Cookie（完整选项） |

### 生命周期
| 工具 | 说明 |
|---|---|
| `browser_close` | 关闭浏览器并清理资源 |

---

## 配置说明

所有配置通过环境变量完成：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `BROWSER_MODE` | `visible` | `visible` = 有头模式（看得到操作！）；`headless` = 无头模式 |
| `VIEWPORT_WIDTH` | `1920` | 浏览器视口宽度 |
| `VIEWPORT_HEIGHT` | `1080` | 浏览器视口高度 |
| `BROWSER_LOCALE` | `zh-CN` | 浏览器语言区域（影响语言、日期格式） |
| `BROWSER_TIMEOUT` | `30000` | 默认导航超时（毫秒） |

自定义配置示例：

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

## 设计理念

```
┌─────────────────────────────────────────────────┐
│  AI 智能体（Cursor / Claude / Windsurf / ...）  │
│                                                 │
│  "我在截图中看到了登录按钮，                      │
│   让我点击它，然后输入用户名..."                   │
│                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │  截图    │→ │  决策    │→ │  操作    │      │
│  │  快照    │  │ （LLM）  │  │ （工具） │      │
│  └──────────┘  └──────────┘  └──────────┘      │
└─────────────────┬───────────────────────────────┘
                  │ MCP 协议（stdio）
┌─────────────────▼───────────────────────────────┐
│  Playwright MCP Server                          │
│                                                 │
│  27 个原子工具：                                 │
│  点击、输入、截图、快照、上传、悬停、             │
│  拖拽、键盘、标签页、Cookie、PDF...              │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  Playwright → Chromium 浏览器            │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

**核心洞察：** AI 智能体已经能从截图和 DOM 结构理解网页。它不需要另一个 LLM 层 — 它只需要可靠、细粒度的浏览器控制。这正是本服务器提供的。

---

## 实战案例

以下是 Cursor 使用本 MCP 为文档添加水印的真实操作过程：

1. **`browser_open`** → 导航到 Web 应用，获取可交互元素列表
2. **`browser_click`** → 点击"测试号登录"
3. **`browser_click`** → 点击"Login"按钮
4. **`browser_screenshot`** → 确认登录成功，看到仪表盘
5. **`browser_upload_via_dialog`** → 通过拦截文件选择对话框上传 DOCX 文件
6. **`browser_execute_js`** → 修改 textarea 中的水印配置（字体、颜色）
7. **`browser_click`** → 点击"应用"
8. **`browser_screenshot`** → 截图确认水印渲染效果

整个过程**全程可视化** — 用户实时观看浏览器操作。

---

## 与 Cursor 内置浏览器的对比

Cursor 自带的 `cursor-ide-browser` 适合简单页面。但对于复杂 SPA 应用（React、Vue、Angular），它存在以下限制：

- **Accessibility tree 经常为空** → `browser_snapshot` 返回不了有用信息
- **不支持文件上传** → 无法测试上传流程
- **没有 `evaluate` / JS 执行** → 无法操作 React 状态
- **不支持多标签页** → 无法测试多窗口工作流

本 MCP Server 填补了这些空白，可以与 `cursor-ide-browser` 协同使用。

---

## WSL + WSLg 环境配置

如果你在 Windows 上使用 WSL2，`visible` 模式通过 **WSLg** 工作 — 浏览器窗口会自动显示在 Windows 桌面上，无需额外配置。

如果 WSLg 不可用，使用 `BROWSER_MODE=headless` 并通过 `browser_screenshot` 获取视觉反馈。

---

## 贡献

欢迎贡献！请随时提交 Issue 和 Pull Request。

---

## 许可证

MIT — 详见 [LICENSE](LICENSE)。
