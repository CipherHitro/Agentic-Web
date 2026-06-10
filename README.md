# 🤖 Agentic Web AI

A full-stack AI-powered web browsing agent. This repository consists of a **FastAPI backend** (which orchestrates a Playwright browser instance and interfaces with OpenRouter LLMs) and a **Streamlit frontend** client that provides a responsive, native-themed chat interface.

---

## 📂 Project Architecture

```text
Agentic-Web/
├── backend/          # FastAPI Backend Server
│   ├── app/          # API routes, LLM agents, Playwright scraper, & tools
│   ├── main.py       # FastAPI entry point & lifespan browser manager hooks
│   └── pyproject.toml# Backend dependencies managed by uv
├── frontend/         # Streamlit Web UI
│   ├── main.py       # UI views and user chat loop logic
│   └── pyproject.toml# Frontend dependencies managed by uv
└── README.md         # Monorepo documentation (this file)
```

---

## ⚙️ Prerequisites

We use **`uv`** for super-fast Python packaging, dependency syncing, and virtual environment management in both directories.

### Install `uv` (if not already installed)

* **macOS / Linux:**
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
* **Windows (PowerShell):**
  ```powershell
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
* **Via pip (Alternative):**
  ```bash
  pip install uv
  ```

---

## 🚀 Getting Started

First, clone the repository:
```bash
git clone https://github.com/yashviPayal/Agentic-Web.git
cd Agentic-Web
```

### 1. Backend Setup & Run

1. Navigate to the `backend` folder:
   ```bash
   cd backend
   ```

2. Copy the example environment template to `.env`:
   * **Linux/macOS:**
     ```bash
     cp .env.example .env
     ```
   * **Windows (Command Prompt):**
     ```cmd
     copy .env.example .env
     ```
   * **Windows (PowerShell):**
     ```powershell
     Copy-Item .env.example -Destination .env
     ```

3. Open `.env` and fill in your settings:
   * `OPENROUTER_API_KEY`: Your OpenRouter API Key (required for LLM chat features).
   * `PLAYWRIGHT_HEADED`: Set to `true` if you want to watch the browser work in a visible window, or `false` (default) for headless background operation.

4. Install the Python dependencies and the Playwright browser binaries:
   ```bash
   uv sync
   uv run playwright install
   ```

5. Start the FastAPI development server:
   ```bash
   uv run uvicorn main:app --reload --port 8000
   ```
   *The backend server will run at `http://127.0.0.1:8000`.*

---

### 2. Frontend Setup & Run

1. Open a **new terminal window** and navigate to the `frontend` folder:
   ```bash
   cd frontend
   ```

2. Create a `.env` file containing the backend URL:
   * **Linux/macOS:**
     ```bash
     echo "BACKEND_URL=http://localhost:8000" > .env
     ```
   * **Windows (Command Prompt):**
     ```cmd
     echo BACKEND_URL=http://localhost:8000 > .env
     ```
   * **Windows (PowerShell):**
     ```powershell
     "BACKEND_URL=http://localhost:8000" | Out-File -Encoding ASCII .env
     ```

3. Install the frontend dependencies:
   ```bash
   uv sync
   ```

4. Start the Streamlit client:
   ```bash
   uv run streamlit run main.py
   ```
   *The frontend client will open in your browser at `http://localhost:8501`.*

---

## 🔌 API Endpoints (FastAPI)

* **`GET /` / `GET /health`**: Diagnostics to verify the API and browser manager connection.
* **`POST /chat/`**: Communicates with the OpenRouter-backed AI agent. The agent will automatically call the `browse_web` tool if requested to look up web content.
* **`POST /scrape/`**: Directly browses a URL with Playwright, returning structured text content, screenshots, page status, and hyperlinks.

### 📖 Interactive Documentation
Visit the interactive docs once the backend server is running:
* **Swagger UI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
* **ReDoc:** [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
