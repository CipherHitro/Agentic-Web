# Agentic-Web: Complete Technical Developer Guide

Welcome to the **Agentic-Web** project. This document serves as a complete technical and structural guide to the project. It is written to help new developers understand the architecture, how the AI makes decisions, how each feature is implemented, and how to navigate the codebase.

---

## 🏗️ 1. Project Architecture Overview

Agentic-Web is an autonomous AI agent capable of browsing the web in real-time to answer user questions. Instead of relying purely on pre-trained knowledge, it acts as a digital human: it can search, click links, read web pages, and extract data.

The project is split into two primary layers:
*   **Frontend (UI):** A chat interface built with **Streamlit** where users input queries and view the agent's responses and logs.
*   **Backend (Brain & Engine):** A **FastAPI** server that orchestrates the AI agent, manages the LLM conversation loop, and drives a headless browser using **Playwright**.

---

## 🧠 2. How the AI Agent Loop Works

The core intelligence of the project lives in `backend/app/agents/web_agent.py`. The `AIAgent.chat()` function is the engine that drives everything. Here is how it executes:

1.  **Intent Routing (Layer 1):** Before doing anything complex, a fast, deterministic LLM prompt categorizes the user's query into `CONVERSATIONAL`, `STATIC_KNOWLEDGE`, or `WEB_REQUIRED`. If the question doesn't require the web (e.g., "Hello" or "Write a python script"), the agent bypasses the browser entirely to save time and resources.
2.  **The Autonomous Loop:** If the web is required, the agent enters a `while` loop (capped at 20 steps to prevent infinite looping).
    *   The agent is given a system prompt (`backend/app/agents/prompts.py`) that lists its available "Tools".
    *   The LLM is asked: *"Here is the user's question, and here is what you have done so far. What tool do you want to use next?"*
3.  **Tool Execution:** The AI chooses a tool (e.g., `search_web`). The backend pauses the LLM, executes the requested Python code, and feeds the result back into the conversation history.
4.  **Auto-Recovery & Safeguards:** The loop includes built-in safeguards:
    *   **Context Management:** Raw web pages are huge. Before adding `browse_web` results back into the LLM's memory, the agent truncates the text to 4,000 characters and strips out raw hyperlink arrays to prevent the LLM from crashing due to context overflow.
    *   **Failure Bail-outs:** If the LLM gets confused and stops calling tools, the agent nudges it. If it fails twice in a row, the loop automatically bails out and attempts to answer the user using whatever data it has already collected.

---

## 🛠️ 3. Features & Tools (How they are implemented)

The AI is granted five specific tools to interact with the world. You can find the registry for all tools in `backend/app/tools/tool_registry.py`.

### A. `search_web`
*   **File:** `backend/app/tools/search_tools.py`
*   **What it does:** Performs a web search (via DuckDuckGo) and returns a list of URLs, titles, and short text snippets.
*   **How it works:** It uses the `duckduckgo-search` library to fetch organic search results. The AI uses this as its starting point to find relevant URLs.

### B. `browse_web`
*   **File:** `backend/app/scraper/browser.py` & `backend/app/scraper/page_handler.py`
*   **What it does:** Opens a specific URL and reads the page content.
*   **How it works:** This is the heaviest feature. It uses **Playwright** to spin up a headless Chromium browser instance. It navigates to the URL, scrolls down to trigger lazy-loaded elements, and waits for the network to settle. It then extracts the raw HTML and cleans it by stripping out `<script>` and `<style>` tags to return readable text to the AI.

### C. `navigate_page`
*   **File:** `backend/app/tools/navigation_tools.py`
*   **What it does:** Allows the AI to click links on the *current* page to go deeper (e.g., clicking a "Next Page" button or a "Reviews" tab).
*   **How it works:** When `browse_web` runs, it secretly caches all the interactive links on that page. When the AI calls `navigate_page` with an intent (like "go to the stars tab"), this tool uses a fast LLM pass to score and pick the best matching URL from the cached links, and instructs Playwright to click/navigate to it.

### D. `extract_data`
*   **File:** `backend/app/tools/extraction_tools.py`
*   **What it does:** Pulls specific, structured facts from the current webpage (e.g., extracting just the "price" and "product name").
*   **How it works:** The AI passes a list of desired fields (e.g., `["price"]`). The tool automatically grabs the current page's HTML from the Playwright instance. It uses a **Two-Pass Strategy**:
    1.  **HTML Pass:** It uses `BeautifulSoup` to look for exact matches in meta tags, table headers, or class names (high confidence).
    2.  **LLM Pass:** If the HTML pass fails, it chunks the text and uses an LLM to smartly extract the data from the unstructured text (medium confidence).

### E. `finish_task`
*   **File:** `backend/app/tools/finish_tool.py`
*   **What it does:** Ends the agent loop and returns the final answer to the user.
*   **How it works:** When the AI determines it has found the requested information, it calls this tool with its `answer` and the `sources` it used. The `AIAgent` loop intercepts this call, marks the task as complete, and breaks the `while` loop.

---

## 🗺️ 4. Codebase Navigation (How to trace a request)

If you want to follow the data flow from the moment a user hits "Send" to the moment the answer appears, follow this path:

1.  **Frontend (`frontend/main.py`)**: User types a query. Streamlit makes a POST request to `http://localhost:8000/chat/`.
2.  **API Router (`backend/main.py`)**: The FastAPI endpoint `/chat/` receives the request and instantiates the `AIAgent`.
3.  **The Brain (`backend/app/agents/web_agent.py`)**: `AIAgent.chat()` takes over. It routes the intent, loads the system prompt (`prompts.py`), and starts the LLM loop.
4.  **Tool Execution (`backend/app/tools/tool_registry.py`)**: When the LLM decides to use a tool, `execute_tool()` acts as a switchboard, routing the request to the correct Python function in `search_tools.py`, `extraction_tools.py`, etc.
5.  **Browser Control (`backend/app/scraper/browser.py`)**: If the tool involves the web, `BrowserManager` controls Playwright.
6.  **Return**: The loop finishes (via `finish_task`), and the final string is returned to `backend/main.py`, which sends it back to Streamlit to display.

---

## ➕ 5. Developer Guide: How to Add a New Feature / Tool

If you want to give the AI a new ability (for example, the ability to read PDF files or interact with a database), follow these 4 simple steps:

1.  **Write the Logic:** Create a new Python file in `backend/app/tools/` (e.g., `pdf_tools.py`) and write an `async def read_pdf(file_url: str):` function.
2.  **Define the Schema:** Open `backend/app/tools/tool_registry.py`. Add your new function's JSON schema (name, description, parameters) to the `TOOLS` list so the LLM knows what arguments it requires.
3.  **Register the Function:** In the same `tool_registry.py` file, add your function to the `TOOL_REGISTRY` dictionary mapping so the system knows what code to run when the LLM asks for it.
4.  **Update the System Prompt:** Open `backend/app/agents/prompts.py` and add a brief description of your tool to the `WEB_AGENT_SYSTEM_PROMPT` so the AI understands *when* and *why* it should use this new superpower.
