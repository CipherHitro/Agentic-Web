from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # development: headed browser + human can take over for login/MFA/CAPTCHA
    # production:  headless browser + no interactive handoff (Render, etc.)
    mode: str = "development"

    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-2.5-flash-lite"
    frontend_url: str = "http://localhost:8501"
    app_title: str = "Agentic Web AI"
    tavily_api_key: str | None = None
    save_screenshots_local: bool = False
    nemotron_nvidia: str | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, v: object) -> str:
        if v is None:
            return "development"
        normalized = str(v).strip().lower()
        if normalized not in ("development", "production"):
            raise ValueError("MODE must be 'development' or 'production'")
        return normalized

    @property
    def is_development(self) -> bool:
        return self.mode == "development"

    @property
    def is_production(self) -> bool:
        return self.mode == "production"

    @property
    def playwright_headed(self) -> bool:
        """Headed (visible) browser only in development."""
        return self.is_development

    @property
    def human_involvement_enabled(self) -> bool:
        """Allow pausing for login/MFA/CAPTCHA handoff only in development."""
        return self.is_development

    def deployment_context_for_prompt(self) -> str:
        if self.is_production:
            return (
                "\n\n══════════════════════════════════════════════════\n"
                "DEPLOYMENT MODE: PRODUCTION\n"
                "══════════════════════════════════════════════════\n\n"
                "The browser runs HEADLESS on a remote server. The user CANNOT see or "
                "interact with the browser window.\n"
                "- NEVER call request_human_input — it is disabled and will fail.\n"
                "- Tasks requiring login, sign-in, MFA/2FA, CAPTCHA, or any manual "
                "browser action CANNOT be completed.\n"
                "- If you hit an auth wall, call finish_task and clearly explain that "
                "this step needs interactive browser access (e.g. signing in) which is "
                "not available in production. Do NOT keep retrying login flows.\n"
                "- Focus on tasks that work without authentication, or use public pages only."
            )
        return (
            "\n\n══════════════════════════════════════════════════\n"
            "DEPLOYMENT MODE: DEVELOPMENT\n"
            "══════════════════════════════════════════════════\n\n"
            "The browser runs in HEADED (visible) mode on the local machine.\n"
            "- You MAY call request_human_input when login, MFA, CAPTCHA, or manual "
            "browser action is required.\n"
            "- Tell the human exactly what to do in the browser window and what to "
            "type in chat when done. After they confirm, continue the task immediately."
        )


settings = Settings()
