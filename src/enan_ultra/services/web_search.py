"""Small, bounded DuckDuckGo HTML search client."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

MAX_RESPONSE_BYTES = 1_000_000
SEARCH_URL = "https://html.duckduckgo.com/html/"


def _is_duckduckgo_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.casefold().rstrip(".")
    return host == "duckduckgo.com" or host.endswith(".duckduckgo.com")


def parse_search_results(html: str, limit: int = 3) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, str]] = []
    for result in soup.select(".result"):
        link = result.select_one(".result__a")
        snippet = result.select_one(".result__snippet")
        if link is None or snippet is None:
            continue
        title = link.get_text(" ", strip=True)[:300]
        url = str(link.get("href") or "").strip()
        if url.startswith("//"):
            url = "https:" + url
        parsed = urlsplit(url)
        if _is_duckduckgo_host(parsed.hostname):
            redirect_target = parse_qs(parsed.query).get("uddg", [])
            if redirect_target:
                url = unquote(redirect_target[0])
        text = snippet.get_text(" ", strip=True)[:1000]
        parsed = urlsplit(url)
        if not title or not text or parsed.scheme != "https" or not parsed.hostname:
            continue
        if _is_duckduckgo_host(parsed.hostname):
            continue
        results.append({"title": title, "url": url, "snippet": text})
        if len(results) >= max(1, min(limit, 3)):
            break
    return results


async def search_web(query: str) -> list[dict[str, str]]:
    normalized = query.strip()
    if not normalized or len(normalized) > 500:
        raise ValueError("Search query must contain 1-500 characters.")
    timeout = httpx.Timeout(connect=5, read=12, write=5, pool=5)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False,
        headers={"User-Agent": "EnanUltra-WebSearch/1.0", "Accept": "text/html"},
    ) as client:
        async with client.stream(
            "GET", SEARCH_URL, params={"q": normalized, "kl": "wt-wt"},
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type:
                raise ValueError("Search provider did not return an HTML results page.")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("Search results exceeded the response size limit.")
    results = parse_search_results(body.decode("utf-8", errors="replace"))
    if not results:
        raise ValueError("No search results were found.")
    return results
