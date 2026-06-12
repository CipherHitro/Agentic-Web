from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Cheapest OpenRouter models with tool-calling support:
    # - "meta-llama/llama-3.1-8b-instruct:free" (Free / 0 credits, but subject to rate limits)
    # - "meta-llama/llama-3.1-8b-instruct" (Paid, but ultra cheap: $0.02 per 1M input / $0.03 per 1M output)
    # - "google/gemini-2.5-flash-lite" (Paid, extremely cheap: $0.075 per 1M input / $0.30 per 1M output, highly reliable)
    openrouter_model: str = "google/gemini-2.5-flash-lite"
    frontend_url: str = "http://localhost:8501"
    app_title: str = "Agentic Web AI"
    playwright_headed: bool = True
    tavily_api_key: str | None = None


settings = Settings()

