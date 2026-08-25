from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import aiohttp
import feedparser

from .models import Route, SearchResult, TorrentStatus
from .utils import stable_result_id

LOGGER = logging.getLogger(__name__)


class ServiceError(RuntimeError):
    pass


def normalize_result_links(*values: object) -> tuple[str | None, str | None]:
    """Return (download URL, magnet), even when an indexer swaps its fields."""
    download_url: str | None = None
    magnet: str | None = None
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        if value.lower().startswith("magnet:?"):
            magnet = magnet or value
        elif value.startswith("/") or urlsplit(value).scheme.lower() in {"http", "https"}:
            download_url = download_url or value
    return download_url, magnet


def jellyfin_search_terms(title: str) -> list[str]:
    terms: list[str] = []

    def add(value: str) -> None:
        value = value.strip(" ._-")
        if value and value not in terms:
            terms.append(value)

    add(title)
    add(re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", title))
    # Jellyfin often stores only the primary display title while release names
    # include an alternate title in parentheses. This also handles a truncated
    # alias that is missing its final parenthesis.
    for value in tuple(terms):
        add(re.sub(r"\s*\([^)]*\)?\s*$", "", value))
    return terms


class QBittorrentClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, username: str, password: str):
        self.session = session
        self.base_url = base_url
        self.username = username
        self.password = password
        self._authenticated = False

    async def login(self) -> None:
        async with self.session.post(
            f"{self.base_url}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
        ) as response:
            body = await response.text()
            if response.status != 200 or body.strip() != "Ok.":
                raise ServiceError(f"qBittorrent authentication failed ({response.status})")
        self._authenticated = True

    async def _request(self, method: str, endpoint: str, **kwargs: Any) -> aiohttp.ClientResponse:
        if not self._authenticated:
            await self.login()
        response = await self.session.request(
            method, f"{self.base_url}/api/v2/{endpoint.lstrip('/')}", **kwargs
        )
        if response.status in {401, 403}:
            response.release()
            await self.login()
            response = await self.session.request(
                method, f"{self.base_url}/api/v2/{endpoint.lstrip('/')}", **kwargs
            )
        if response.status >= 400:
            message = (await response.text())[:300]
            response.release()
            raise ServiceError(f"qBittorrent {endpoint} failed ({response.status}): {message}")
        return response

    async def version(self) -> tuple[str, str]:
        app = await self._request("GET", "app/version")
        api = await self._request("GET", "app/webapiVersion")
        app_text, api_text = await app.text(), await api.text()
        app.release()
        api.release()
        return app_text, api_text

    async def ensure_categories(self, save_path: str) -> None:
        response = await self._request("GET", "torrents/categories")
        existing = await response.json()
        response.release()
        for route in Route:
            category = f"discord-{route.value}"
            if category not in existing:
                result = await self._request(
                    "POST",
                    "torrents/createCategory",
                    data={"category": category, "savePath": save_path},
                )
                result.release()

    async def add_magnet(self, magnet: str, route: Route, save_path: str) -> None:
        response = await self._request(
            "POST",
            "torrents/add",
            data={
                "urls": magnet,
                # Keep active downloads in qBittorrent's normal Uncategorized
                # view. The route is still visible and queryable as a tag.
                "tags": f"discord-{route.value}",
                "savepath": save_path,
            },
        )
        body = await response.text()
        response.release()
        if body.strip().lower() not in {"ok.", ""}:
            raise ServiceError(f"qBittorrent rejected the magnet: {body[:200]}")

    async def add_torrent_file(
        self, filename: str, payload: bytes, route: Route, save_path: str
    ) -> None:
        form = aiohttp.FormData()
        form.add_field(
            "torrents", payload, filename=filename, content_type="application/x-bittorrent"
        )
        form.add_field("tags", f"discord-{route.value}")
        form.add_field("savepath", save_path)
        response = await self._request("POST", "torrents/add", data=form)
        body = await response.text()
        response.release()
        if body.strip().lower() not in {"ok.", ""}:
            raise ServiceError(f"qBittorrent rejected the torrent: {body[:200]}")

    async def torrent(self, info_hash: str) -> TorrentStatus | None:
        response = await self._request("GET", "torrents/info", params={"hashes": info_hash})
        items = await response.json()
        response.release()
        if not items:
            return None
        item = items[0]
        return TorrentStatus(
            info_hash=item["hash"].lower(),
            name=item.get("name", "Unknown"),
            state=item.get("state", "unknown"),
            progress=float(item.get("progress", 0)),
            download_speed=int(item.get("dlspeed", 0)),
            eta=int(item.get("eta", 0)),
            content_path=item.get("content_path") or item.get("save_path", ""),
            category=item.get("category", ""),
        )

    async def action(self, info_hash: str, action: str, delete_files: bool = False) -> None:
        if action not in {"pause", "resume", "delete"}:
            raise ValueError(f"Unsupported qBittorrent action: {action}")
        endpoint = {
            "pause": "torrents/pause",
            "resume": "torrents/resume",
            "delete": "torrents/delete",
        }[action]
        data: dict[str, str] = {"hashes": info_hash}
        if action == "delete":
            data["deleteFiles"] = "true" if delete_files else "false"
        response = await self._request("POST", endpoint, data=data)
        response.release()

    async def set_route(self, info_hash: str, route: Route) -> None:
        clear_category = await self._request(
            "POST",
            "torrents/setCategory",
            data={"hashes": info_hash, "category": ""},
        )
        clear_category.release()
        route_tags = ",".join(f"discord-{value.value}" for value in Route)
        remove_tags = await self._request(
            "POST", "torrents/removeTags", data={"hashes": info_hash, "tags": route_tags}
        )
        remove_tags.release()
        add_tag = await self._request(
            "POST",
            "torrents/addTags",
            data={"hashes": info_hash, "tags": f"discord-{route.value}"},
        )
        add_tag.release()


class ProwlarrClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str):
        self.session = session
        self.base_url = base_url
        self.api_key = api_key

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key}

    async def health(self) -> bool:
        async with self.session.get(
            f"{self.base_url}/api/v1/health", headers=self.headers
        ) as response:
            return response.status == 200

    async def search(self, query: str, limit: int = 25) -> list[SearchResult]:
        params = {"query": query, "type": "search", "limit": str(limit)}
        async with self.session.get(
            f"{self.base_url}/api/v1/search", headers=self.headers, params=params
        ) as response:
            if response.status != 200:
                raise ServiceError(f"Prowlarr search failed ({response.status})")
            payload = await response.json()
        results: list[SearchResult] = []
        # Prowlarr applies the requested limit per indexer, so truncating the
        # combined response here would let the first indexer monopolize results.
        # The service performs the final filtered and ranked 25-result cap.
        for item in payload[:500]:
            title = str(item.get("title") or "Unknown")
            download_url, magnet = normalize_result_links(
                item.get("downloadUrl"), item.get("magnetUrl"), item.get("guid")
            )
            indexer = str(item.get("indexer") or item.get("indexerId") or "Prowlarr")
            categories = item.get("categories") or []
            category = (
                ", ".join(
                    str(value.get("name") if isinstance(value, dict) else value)
                    for value in categories
                )
                or "Unknown"
            )
            results.append(
                SearchResult(
                    result_id=stable_result_id(title, str(download_url), indexer),
                    title=title,
                    size=int(item.get("size") or 0),
                    seeders=int(item.get("seeders") or 0),
                    leechers=int(item.get("leechers") or item.get("peers") or 0),
                    indexer=indexer,
                    category=category,
                    download_url=download_url,
                    magnet=magnet,
                )
            )
        return results

    async def download(self, result: SearchResult) -> tuple[str, bytes] | str:
        if not result.download_url:
            raise ServiceError("Search result has no downloadable torrent")
        url = result.download_url
        if url.lower().startswith("magnet:?"):
            return url
        if url.startswith("/"):
            url = f"{self.base_url}{url}"
        prowlarr_origin = urlsplit(self.base_url)[:2]
        for _ in range(6):
            headers = self.headers if urlsplit(url)[:2] == prowlarr_origin else {}
            async with self.session.get(url, headers=headers, allow_redirects=False) as response:
                if 300 <= response.status < 400:
                    location = response.headers.get("Location", "")
                    if location.startswith("magnet:?"):
                        return location
                    if not location:
                        raise ServiceError("Prowlarr download redirect had no destination")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise ServiceError(f"Prowlarr download failed ({response.status})")
                payload = await response.read()
                content_type = response.headers.get("Content-Type", "")
                if "bittorrent" not in content_type and not payload.startswith(b"d"):
                    raise ServiceError("Prowlarr returned an unexpected download payload")
                return f"{result.result_id}.torrent", payload
        raise ServiceError("Prowlarr download followed too many redirects")


class NyaaProvider:
    def __init__(self, session: aiohttp.ClientSession, base_url: str):
        self.session = session
        self.base_url = base_url

    async def search(self, query: str, limit: int = 25) -> list[SearchResult]:
        params = {"page": "rss", "q": query, "c": "0_0", "f": "0"}
        url = f"{self.base_url}/?{urlencode(params)}"
        async with self.session.get(url) as response:
            if response.status != 200:
                raise ServiceError(f"Nyaa fallback search failed ({response.status})")
            body = await response.read()
        feed = await asyncio.to_thread(feedparser.parse, body)
        results = []
        for entry in feed.entries[:limit]:
            title = str(entry.get("title", "Unknown"))
            info_hash = entry.get("nyaa_infohash")
            magnet = (
                f"magnet:?xt=urn:btih:{info_hash}&dn={entry.get('title', '')}"
                if info_hash
                else None
            )
            size = entry.get("nyaa_size", "0 B")
            try:
                from .utils import parse_size

                parsed_size = parse_size(size)
            except ValueError:
                parsed_size = 0
            results.append(
                SearchResult(
                    result_id=stable_result_id(title, str(magnet), "Nyaa"),
                    title=title,
                    size=parsed_size,
                    seeders=int(entry.get("nyaa_seeders", 0)),
                    leechers=int(entry.get("nyaa_leechers", 0)),
                    indexer="Nyaa fallback",
                    category=str(entry.get("nyaa_category", "Anime")),
                    magnet=magnet,
                    download_url=None,
                )
            )
        return results


class JellyfinClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str):
        self.session = session
        self.base_url = base_url
        self.api_key = api_key

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Emby-Token": self.api_key}

    async def health(self) -> bool:
        async with self.session.get(f"{self.base_url}/System/Info/Public") as response:
            return response.status == 200

    async def refresh_library(self, library_id: str) -> None:
        if library_id:
            url = f"{self.base_url}/Items/{library_id}/Refresh"
            params = {
                "Recursive": "true",
                "MetadataRefreshMode": "Default",
                "ImageRefreshMode": "Default",
                "ReplaceAllMetadata": "false",
                "ReplaceAllImages": "false",
            }
        else:
            url = f"{self.base_url}/Library/Refresh"
            params = None
        async with self.session.post(url, headers=self.headers, params=params) as response:
            if response.status not in {200, 204}:
                raise ServiceError(f"Jellyfin refresh failed ({response.status})")

    async def find_item(self, title: str, parent_id: str = "") -> dict[str, Any] | None:
        for search_term in jellyfin_search_terms(title):
            params = {
                "searchTerm": search_term,
                "recursive": "true",
                "limit": "10",
                "fields": "Path",
            }
            if parent_id:
                params["parentId"] = parent_id
            async with self.session.get(
                f"{self.base_url}/Items", headers=self.headers, params=params
            ) as response:
                if response.status != 200:
                    raise ServiceError(f"Jellyfin item lookup failed ({response.status})")
                payload = await response.json()
            items = payload.get("Items", [])
            if items:
                return items[0]
        return None
