import logging
import urllib.parse
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup

from app.scraper.browser import browser_manager
from app.scraper.page_handler import navigate, scroll_to_bottom
from app.services.llm_service import get_openai_client
from app.config import settings
from app.tools.extraction_tools import extract_clean_content

logger = logging.getLogger(__name__)


def get_links(html: str, base_url: str) -> List[Dict[str, str]]:
    """
    Extracts all absolute links from HTML, filters out junk links,
    deduplicates them, and returns up to 50 links.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    raw_links = soup.find_all("a", href=True)
    seen_urls = set()
    links = []

    for a in raw_links:
        href = a["href"].strip()
        text = a.get_text(strip=True)

        # Skip anchor, javascript, email, phone, etc.
        if (
            not href
            or href.startswith("#")
            or href.startswith("javascript:")
            or href.startswith("mailto:")
            or href.startswith("tel:")
        ):
            continue

        # Resolve relative URLs
        absolute_url = urllib.parse.urljoin(base_url, href)

        # Parse and filter out non http/https URLs or external CDN links
        parsed = urllib.parse.urlparse(absolute_url)
        if parsed.scheme not in ("http", "https"):
            continue

        # Deduplicate based on URL
        if absolute_url in seen_urls:
            continue

        seen_urls.add(absolute_url)
        links.append({
            "text": text or absolute_url,
            "url": absolute_url
        })

        if len(links) >= 50:
            break

    return links


async def pick_best_link(links: List[Dict[str, str]], intent: str) -> Optional[str]:
    """
    Uses the LLM to choose the best URL from a list of links matching a user navigation intent.
    """
    if not links:
        return None

    client = get_openai_client()
    model = settings.openrouter_model

    formatted_links = "\n".join(f"- Text: '{l['text']}' | URL: {l['url']}" for l in links)

    prompt = (
        "You are a deterministic router agent.\n"
        "Given the user's intent, select the single best matching absolute URL from the list of available links.\n\n"
        f"User Intent: \"{intent}\"\n\n"
        "Available Links:\n"
        f"{formatted_links}\n\n"
        "Rules:\n"
        "1. Return ONLY the absolute URL of the selected link from the list.\n"
        "2. Do NOT add any preamble, markdown code blocks, explanation, or extra characters.\n"
        "3. If no link is a good match, return the string \"None\"."
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.0,
        )
        choice = response.choices[0].message.content.strip()
        # Clean any markdown formatting
        if choice.startswith("```"):
            choice = choice.replace("```", "").strip()
        if choice.startswith("url"):
            choice = choice.replace("url", "").strip()
        
        choice = choice.strip("\"'")
        
        if choice.lower() == "none" or not choice.startswith("http"):
            return None

        # Verify the chosen URL actually exists in the links list
        chosen_url = next((l["url"] for l in links if l["url"] == choice), None)
        if chosen_url:
            return chosen_url

        # Fallback substring match if LLM slightly altered it
        chosen_url = next((l["url"] for l in links if choice in l["url"] or l["url"] in choice), None)
        return chosen_url
    except Exception as e:
        logger.error(f"Error picking best link via LLM: {e}")
        return None


async def navigate_page(intent: str, max_depth: int = 5) -> Dict[str, Any]:
    """
    Statefully navigate the current Playwright page to a new URL matching the user's intent.
    """
    page = browser_manager.current_page
    if not page or page.is_closed():
        return {
            "success": False,
            "error": "No active browser session. You must call 'browse_web' before using 'navigate_page'."
        }

    # Initialize depth count on browser_manager if not set
    if not hasattr(browser_manager, "navigation_depth"):
        browser_manager.navigation_depth = 0

    browser_manager.navigation_depth += 1
    if browser_manager.navigation_depth > max_depth:
        return {
            "success": False,
            "error": f"Navigation depth limit ({max_depth}) exceeded. Please extract whatever data you have collected."
        }

    html = await page.content()
    base_url = page.url

    links = get_links(html, base_url)
    if not links:
        return {
            "success": False,
            "error": "No clickable links found on the current page."
        }

    target_url = await pick_best_link(links, intent)
    if not target_url:
        return {
            "success": False,
            "error": f"Could not find any link matching navigation intent: '{intent}'."
        }

    print(f"🔗 [NAVIGATING] Found matching URL: {target_url}. Navigating page...")
    logger.info(f"Navigating to {target_url} matching intent '{intent}'")

    try:
        response = await navigate(page, target_url)
        await scroll_to_bottom(page)

        title = await page.title()
        html_content = await page.content()

        result: Dict[str, Any] = {
            "url": target_url,
            "title": title,
            "status": response.status if response else 200,
            "success": True,
        }

        # Consistent contract with browse_web
        content, extracted_links, nav_links = extract_clean_content(
            html_content, base_url=target_url, max_text_length=8000
        )
        result["content"] = content
        result["links"] = extracted_links
        result["navigation_links"] = nav_links

        return result
    except Exception as e:
        logger.error(f"Failed navigating to {target_url}: {e}")
        return {
            "success": False,
            "error": f"Failed navigating to {target_url}: {str(e)}"
        }
