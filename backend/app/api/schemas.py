from typing import Any, Dict, List, Optional
from enum import Enum

from pydantic import BaseModel, HttpUrl


class BrowserEngine(str, Enum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class ChatRequest(BaseModel):
    messages: List[Dict[str, Any]]


class ChatResponse(BaseModel):
    response: Optional[str] = ""
    tool_used: Optional[str] = None
    tool_result: Optional[Any] = None
    raw_url: Optional[str] = None
    new_messages: Optional[List[Dict[str, Any]]] = None


class ScrapeRequest(BaseModel):
    url: HttpUrl
    extract_content: bool = True
    scroll_page: bool = True
    take_screenshot: bool = False
    wait_for: Optional[str] = None
    max_text_length: int = 8000


class ScrapeResponse(BaseModel):
    url: str
    title: Optional[str] = None
    status: Optional[int] = None
    success: bool
    content: Optional[str] = None
    links: Optional[List[Dict[str, Any]]] = None
    screenshot: Optional[str] = None
    error: Optional[str] = None
