from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from app.api.schemas import ChatRequest, ChatResponse, ScrapeRequest
from app.config import settings
from app.scraper.browser import browser_manager
from app.services.agent_service import agent_service
from app.tools.human_tools import human_request, _human_response_queue


router = APIRouter()


class HumanResponse(BaseModel):
    answer: str


@router.get("/health", tags=["health"])
async def health():
    return {
        "status": "ok",
        "mode": settings.mode,
        "human_involvement_enabled": settings.human_involvement_enabled,
    }


@router.get("/config", tags=["config"])
async def get_config():
    """Runtime deployment config for frontend and operators."""
    return {
        "mode": settings.mode,
        "playwright_headed": settings.playwright_headed,
        "human_involvement_enabled": settings.human_involvement_enabled,
    }


@router.get("/human/status", tags=["human"])
async def get_human_status():
    if not settings.human_involvement_enabled:
        return {
            "waiting": False,
            "prompt": None,
            "human_involvement_enabled": False,
        }
    return {**human_request, "human_involvement_enabled": True}


@router.post("/human/response", tags=["human"])
async def submit_human_response(response: HumanResponse):
    if not settings.human_involvement_enabled:
        raise HTTPException(
            status_code=403,
            detail="Human involvement is disabled in production mode (MODE=production).",
        )
    if not human_request.get("waiting"):
        raise HTTPException(status_code=400, detail="Not currently waiting for human input.")
    await _human_response_queue.put({"answer": response.answer})
    return {"status": "success"}


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(request: ChatRequest):
    """Chat with the AI agent. It may use browse_web tool automatically."""
    result = await agent_service.chat(request.messages)
    return ChatResponse(**result)


@router.post("/scrape/", tags=["scraping"])
async def scrape(request: ScrapeRequest):
    """Direct scraping endpoint (for testing or direct use)."""
    try:
        result = await browser_manager.browse_web(
            url=str(request.url),
            extract_content=request.extract_content,
            scroll_page=request.scroll_page,
            take_screenshot=request.take_screenshot,
            wait_for=request.wait_for,
            max_text_length=request.max_text_length,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
