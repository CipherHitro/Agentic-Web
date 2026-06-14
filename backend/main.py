from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import logging
import os
from dotenv import load_dotenv

# Load environment variables from backend/.env
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from app.scraper.browser import browser_manager
from app.api.routes import router as api_router


class _SuppressHumanStatusLogFilter(logging.Filter):
    """Hide noisy /human/status polling from uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/human/status" not in msg


logging.getLogger("uvicorn.access").addFilter(_SuppressHumanStatusLogFilter())

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate settings
    from app.config import settings
    if not settings.openrouter_api_key:
        raise ValueError(
            "OPENROUTER_API_KEY is not configured. Please set it in your environment or backend/.env file."
        )
    # Startup: Launch browser using the settings/env configuration
    await browser_manager.start()
    yield
    # Shutdown
    await browser_manager.close()

app = FastAPI(
    title="Agentic Web AI",
    description="AI-powered web browsing agent with tool calling",
    version="2.0.0",
    lifespan=lifespan
)

# CORS — allow Streamlit frontend to call API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

@app.get("/")
async def root():
    return {"status": "Agentic Web AI is running", "browser": "connected"}
