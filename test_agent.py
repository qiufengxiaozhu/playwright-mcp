"""
browser-use Agent 示例：用 AI 自动操作浏览器

使用前需要设置环境变量（任选一个 LLM 提供商）：
  export MINIMAX_API_KEY=your-key       # MiniMax（OpenAI 兼容，推荐）
  export OPENAI_API_KEY=your-key        # OpenAI
  export GOOGLE_API_KEY=your-key        # Google Gemini
  export ANTHROPIC_API_KEY=your-key     # Anthropic Claude
  export BROWSER_USE_API_KEY=your-key   # Browser Use 官方模型

可选覆盖 MiniMax 模型名：
  export MINIMAX_MODEL=MiniMax-M2.5     # 默认 MiniMax-M2.7
"""
import asyncio
import os
import sys

from browser_use import Agent, Browser, BrowserConfig


async def main():
    llm = None

    if os.getenv("MINIMAX_API_KEY"):
        from langchain_openai import ChatOpenAI
        model = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
        llm = ChatOpenAI(
            model=model,
            api_key=os.getenv("MINIMAX_API_KEY"),
            base_url="https://api.minimaxi.com/v1",
        )
        print(f"使用 MiniMax {model}")
    elif os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model="gpt-4o-mini")
        print("使用 OpenAI gpt-4o-mini")
    elif os.getenv("GOOGLE_API_KEY"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
        print("使用 Google Gemini 2.0 Flash")
    elif os.getenv("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(model="claude-sonnet-4-20250514")
        print("使用 Anthropic Claude Sonnet")
    elif os.getenv("BROWSER_USE_API_KEY"):
        from browser_use import ChatBrowserUse
        llm = ChatBrowserUse()
        print("使用 Browser Use 官方模型")
    else:
        print("❌ 未设置任何 LLM API key，请设置以下环境变量之一：")
        print("   MINIMAX_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY / ANTHROPIC_API_KEY / BROWSER_USE_API_KEY")
        sys.exit(1)

    browser = Browser(config=BrowserConfig(headless=True))

    task = "Go to https://example.com and tell me the title and main heading of the page"

    print(f"任务: {task}")
    print("启动 Agent...")

    agent = Agent(task=task, llm=llm, browser=browser)
    result = await agent.run()

    print(f"\n✅ Agent 完成！结果:\n{result}")

    await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
