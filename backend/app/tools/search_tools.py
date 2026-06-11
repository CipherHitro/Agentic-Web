import logging
import httpx
from app.config import settings

logger = logging.getLogger(__name__)

async def search_web(query: str, count: int = 5):
    """
    Search the internet for current, real-time,
    recent, product, pricing, company,
    news, release, availability and factual information.

    Always use this tool when the user asks
    about current prices, current events,
    recent releases, product availability,
    or information that may have changed.
    """
    api_key = settings.tavily_api_key
    if not api_key:
        logger.error("Tavily API key is missing. Please set TAVILY_API_KEY in the environment.")
        raise ValueError("TAVILY_API_KEY environment variable is not set.")

    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": count,
    }

    logger.info(f"Querying Tavily Search API with query: '{query}', max_results: {count}")
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=15.0)
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("results", []):
        results.append({
            "url": item.get("url"),
            "title": item.get("title"),
            "snippet": item.get("content"),
        })

    logger.info(f"Tavily Search API returned {len(results)} results")
    return results
