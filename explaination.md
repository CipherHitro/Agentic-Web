# Agentic-Web: Complete Technical Developer Guide

Welcome to the **Agentic-Web** project. This document serves as a comprehensive technical and structural guide. It is written to help new developers understand the architecture, how the AI makes decisions, how each feature is implemented, and how to navigate the codebase.

---

## 🏗️ 1. Project Architecture Overview

Agentic-Web is an autonomous AI agent capable of browsing the web in real-time to answer user questions. Instead of relying purely on pre-trained knowledge, it acts as a digital human: it can search, click links, fill forms, read web pages, and extract data.

The project is split into two primary layers:
*   **Frontend (UI):** A chat interface built with **Streamlit** where users input queries and view the agent's responses, logs, and execution steps. It handles human-in-the-loop interactions when the agent needs human assistance.
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
    *   **Stuck Detection:** The agent tracks consecutive action-tool failures and repetitive action signatures. If it gets stuck repeating the same action, it injects a recovery nudge prompting `observe_page` or `go_back`.
    *   **Observe-to-Act Lock:** If the agent calls `observe_page` but then calls it again without acting, the system detects this "observe-only loop" and injects a nudge forcing the agent to execute the recommended next action (e.g., `click_element`).
    *   **Finish Task Guardrails:** The `finish_task` tool is heavily guarded. The agent cannot finish unless it has listed valid sources and has actually used web tools (e.g., `search_web`, `browse_web`) during its session.

---

## 🛠️ 3. Features & Tools (How they are implemented)

The AI is granted fifteen specific tools to interact with the world. You can find the registry for all tools in `backend/app/tools/tool_registry.py`.

### A. `search_web`
*   **File:** `backend/app/tools/search_tools.py`
*   **What it does:** Performs a web search (via DuckDuckGo) and returns a list of URLs, titles, and short text snippets.
*   **How it works:** It uses the `duckduckgo-search` library to fetch organic search results. The AI uses this as its starting point to find relevant URLs. **Crucial Rule:** Search snippets are never the final answer; the agent must always follow up with `browse_web`.

### B. `browse_web`
*   **File:** `backend/app/scraper/browser.py` & `backend/app/scraper/page_handler.py`
*   **What it does:** Opens a specific URL and reads the page content.
*   **How it works:** This is the heaviest feature. It uses **Playerializer** to spin up a headless Chromium browser instance. It navigates to the URL, scrolls down to trigger lazy-loaded elements, and waits for the network to settle. It then extracts the raw HTML and cleans it by stripping out `<script>` and `<style>` tags to return readable text to the AI. It also features a retry mechanism with exponential backoff for robustness.

### C. `navigate_page`
*   **File:** `backend/app/tools/navigation_tools.py`
*   **What it does:** Allows the AI to click links on the *current* page to go deeper (e.g., clicking a "Next Page" button or a "Reviews" tab). It also supports `get_current_url()` and `go_back()` for navigation state management.
*   **How it works:** When `browse_web` runs, it secretly caches all the interactive links on that page. When the AI calls `navigate_page` with an intent (like "go to the stars tab"), this tool uses a fast LLM pass to score and pick the best matching URL from the cached links, and instructs Playwright to click/navigate to it.

### D. `click_element` ⭐ (NEW)
*   **File:** `backend/app/tools/click_tools.py`
*   **What it does:** Clicks a button, toggle, dropdown trigger, or any interactive element that is NOT a plain navigation link.
*   **How it works:** This is a sophisticated multi-strategy clicker:
    1.  **DOM Scanning:** It first runs a JavaScript scan (`backend/app/scraper/dom_scanner.py`) to find all visible interactive elements.
    2.  **LLM Matching:** It asks the LLM to pick the best matching element text or `aria-label` from the scanned list.
    3.  **Submit Fast-Path:** For Save/Submit buttons, it tries a fast DOM path using Playwright's role-based locators before involving the LLM.
    4.  **Vision Fallback:** If DOM and LLM matching fail, it takes a screenshot and uses a **Vision Model** (e.g., Gemini or Llama Vision) to find the exact pixel coordinates of the element and clicks it directly.
    5.  **Failure Handling:** On failure, it captures a screenshot and calls the vision model for guidance on what went wrong.

### E. `fill_form_field` ⭐ (NEW)
*   **File:** `backend/app/tools/fill_tools.py`
*   **What it does:** Types text into an input, textarea, or contenteditable field on the current page.
*   **How it works:** It employs a multi-layered approach to find and fill fields:
    1.  **Fast Panel Fill:** If an edit panel is already open (detected via `page_state.py`), it tries to fill visible fields directly without involving the LLM.
    2.  **DOM Walk:** It walks the DOM across the main page and all child frames to find inputs, textareas, and contenteditables. It uses the LLM to pick the best field based on the user's description.
    3.  **Vision Fallback:** If the DOM walk fails, it takes a screenshot and uses a vision model to identify the correct CSS selector or aria-label for the field, then fills it.

### F. `read_form_fields` & `select_form_option` ⭐ (NEW)
*   **Files:** `backend/app/tools/form_inspect_tools.py`, `backend/app/tools/select_tools.py`
*   **What they do:** `read_form_fields` scans the current page and returns every form question with its type (text, radio, checkbox) and options. `select_form_option` selects a specific radio button, checkbox, or dropdown option.
*   **How they work:** They use JavaScript to scan for ARIA-based forms (like Google Forms) and native HTML controls. `select_form_option` uses the LLM to map the user's intent to the correct `data-select-id` and includes idempotency guards to prevent toggling checkboxes off. If DOM matching fails, it falls back to vision-based pixel clicking.

### G. `extract_data`
*   **File:** `backend/app/tools/extraction_tools.py`
*   **What it does:** Pulls specific, structured facts from the current webpage (e.g., extracting just the "price" and "product name").
*   **How it works:** The AI passes a list of desired fields (e.g., `["price"]`). The tool automatically grabs the current page's HTML from the Playwright instance. It uses a **Two-Pass Strategy**:
    1.  **HTML Pass:** It uses `BeautifulSoup` to look for exact matches in meta tags, table headers, or class names (high confidence).
    2.  **LLM Pass:** If the HTML pass fails, it chunks the text and uses an LLM to smartly extract the data from the unstructured text (medium confidence).

### H. `scroll` ⭐ (NEW)
*   **File:** `backend/app/tools/scroll_tools.py`
*   **What it does:** Scrolls the current page up, down, to the top, or to the bottom.
*   **How it works:** Executes JavaScript `window.scrollBy` or `window.scrollTo` and then re-extracts the visible content using the same logic as `browse_web`. This allows the agent to read long articles or find elements that are below the fold.

### I. `take_screenshot` & `observe_page` ⭐ (NEW)
*   **Files:** `backend/app/tools/screenshot_tools.py`, `backend/app/tools/observe_tools.py`
*   **What they do:** `take_screenshot` captures the current page and analyzes it with a vision model to describe what's on screen. `observe_page` is a more advanced tool that analyzes the DOM to create a step-by-step action plan for achieving a specific goal (e.g., "find and click the logout button").
*   **How they work:** Both tools leverage the **Vision Service** (`backend/app/services/vision_service.py`):
    *   `observe_page` first checks if an edit panel is already open (skipping vision if so). It then scans interactive elements and uses the LLM to generate an action plan with explicit `NEXT_TOOL` and `NEXT_INTENT` fields, which the main agent loop enforces.
    *   `take_screenshot` simply captures the page and asks the vision model for a detailed description of the UI, which is then fed back to the LLM for reasoning.

### J. `request_human_input` ⭐ (NEW)
*   **File:** `backend/app/tools/human_tools.py`
*   **What it does:** Pauses the agent loop and shows a message to the human in the frontend, waiting for a response.
*   **How it works:** Sets a global state (`human_request`). The frontend's background thread polls `/human/status`. When the status shows `waiting`, the frontend displays a form. Once the user submits their response to `/human/response`, the tool returns the answer to the agent loop, which then resumes execution.

### K. `finish_task`
*   **File:** `backend/app/tools/finish_tool.py`
*   **What it does:** Ends the agent loop and returns the final answer to the user.
*   **How it works:** When the AI determines it has found the requested information, it calls this tool with its `answer` and the `sources` it used. The `AIAgent` loop intercepts this call, marks the task as complete, and breaks the `while` loop.

---

## 🗺️ 4. Codebase Navigation (How to trace a request)

If you want to follow the data flow from the moment a user hits "Send" to the moment the answer appears, follow this path:

1.  **Frontend (`frontend/main.py`)**: User types a query. Streamlit makes a POST request to `http://localhost:8000/chat/`. It also starts a background thread to poll `/human/status` for potential human-in-the-loop interactions.
2.  **API Router (`backend/app/api/routes.py`)**: The FastAPI endpoint `/chat/` receives the request and delegates to `AgentService`.
3.  **The Brain (`backend/app/agents/web_agent.py`)**: `AgentService.chat()` instantiates the `AIAgent` and calls `chat()`. This function takes over, routing the intent, loading the system prompt (`prompts.py`), and starting the LLM tool-calling loop.
4.  **Tool Dispatch (`backend/app/tools/tool_registry.py`)**: When the LLM decides to use a tool, `execute_tool()` acts as a switchboard, routing the request to the correct Python function.
5.  **Browser Control (`backend/app/scraper/browser.py`)**: If the tool involves the web, `BrowserManager` controls Playwright. It handles new contexts, sessions, navigation, and anti-bot measures.
6.  **Services (`backend/app/services/`)**: Shared resources like the LLM client (`llm_service.py`), Vision client (`vision_service.py`), and the high-level `agent_service.py` are used by the tools and the agent.
7.  **Return**: The loop finishes (via `finish_task`), and the final string is returned to `backend/app/api/routes.py`, which sends it back to Streamlit to display. The frontend also parses the `steps` array to show a visual timeline of the tools used.

---

## ➕ 5. Developer Guide: How to Add a New Feature / Tool

If you want to give the AI a new ability (for example, the ability to read PDF files or interact with a database), follow these 4 simple steps:

1.  **Write the Logic:** Create a new Python file in `backend/app/tools/` (e.g., `pdf_tools.py`) and write an `async def read_pdf(file_url: str):` function.
2.  **Define the Schema:** Open `backend/app/tools/tool_registry.py`. Add your new function's JSON schema (name, description, parameters) to the `get_tool_definitions()` list so the LLM knows what arguments it requires.
3.  **Register the Function:** In the same `tool_registry.py` file, add your function to the `TOOL_REGISTRY` dictionary mapping so the system knows what code to run when the LLM asks for it.
4.  **Update the System Prompt:** Open `backend/app/agents/prompts.py` and add a brief description of your tool to the `WEB_AGENT_SYSTEM_PROMPT` so the AI understands *when* and *why* it should use this new superpower.

---

## 🧩 6. Key Architectural Patterns

### A. The Agent Loop & State Management
The `AIAgent.chat()` function is a stateful loop. It maintains `AgentState` to track navigation depth, which tools have been called (`search_web_called`, `browse_web_succeeded`), and various failure counters. This state is isolated per user query. The loop's behavior is heavily influenced by these flags; for example, it will reject an early `finish_task` call if `browse_web` was never successfully executed.

### B. Vision-Augmented DOM Interaction
Instead of relying solely on brittle CSS selectors, the system uses a **Vision Model** (e.g., Google Gemini or Llama Vision) as a fallback. When the standard DOM-matching or LLM-based text matching fails to find a button or input, the system:
1. Takes a screenshot of the current page.
2. Sends it to the Vision Service along with the goal (e.g., "click the save button").
3. The vision model returns either pixel coordinates for a direct mouse click or a description of the element's location.
This is implemented in `backend/app/services/vision_service.py` and used by `click_element`, `fill_form_field`, and `select_form_option`.

### C. Human-in-the-Loop (HITL)
For tasks that cannot proceed without human intervention (like logins or CAPTCHAs), the `request_human_input` tool pauses the entire agent loop. It uses a global state dict and an `asyncio.Queue` to communicate with the FastAPI endpoint (`/human/status` and `/human/response`). The Streamlit frontend polls the status endpoint and renders a blocking form when the agent is waiting.

### D. Form Panel State Detection
Many modern websites (like GitHub) use inline edit dialogs. The system has a dedicated module (`backend/app/scraper/page_state.py`) that detects when such a panel is open. This prevents the agent from clicking the "Edit" trigger again (which would close the panel) and allows the `fill_form_field` tool to fast-path fields without needing an LLM call.

### E. Anti-Bot Measures
The `BrowserManager` (`backend/app/scraper/browser.py`) incorporates several anti-detection techniques:
-   **Session Persistence:** Browser local/session storage is saved per domain in `.sessions/` to maintain login states across agent runs.
-   **Stealth Scripts:** On every new context, it injects a script to remove `navigator.webdriver` and other Playwright-specific properties.
-   **Realistic Viewport & Locale:** Contexts are created with a 1920x1080 viewport and US locale to mimic a real user.
