from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from torrent_bot.clients import (
    JellyfinClient,
    ProwlarrClient,
    QBittorrentClient,
    jellyfin_search_terms,
    normalize_result_links,
    public_poster_url,
)
from torrent_bot.models import Route, SearchResult, TorrentStatus


class Response:
    def __init__(self, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}

    async def text(self) -> str:
        return "Ok."

    def release(self) -> None:
        return None

    async def __aenter__(self) -> Response:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class RedirectSession:
    def __init__(self, location: str) -> None:
        self.location = location

    def get(self, *args: object, **kwargs: object) -> Response:
        assert kwargs["allow_redirects"] is False
        return Response(302, {"Location": self.location})


class JsonResponse(Response):
    async def json(self) -> list[dict[str, object]]:
        return [
            {
                "Name": "Hamnet",
                "ProductionYear": 2025,
                "ImageUrl": "https://image.tmdb.org/t/p/original/poster.jpg",
            }
        ]


class PosterSession:
    def post(self, url: str, **kwargs: object) -> JsonResponse:
        assert url.endswith("/Items/RemoteSearch/Movie")
        assert kwargs["json"] == {
            "SearchInfo": {"Name": "Hamnet", "Year": 2025},
            "IncludeDisabledProviders": False,
        }
        return JsonResponse()


@pytest.mark.asyncio
async def test_qbittorrent_add_uses_route_tag_without_hiding_category() -> None:
    client = QBittorrentClient(None, "http://qbit", "user", "password")  # type: ignore[arg-type]
    client._request = AsyncMock(return_value=Response())  # type: ignore[method-assign]

    await client.add_magnet("magnet:?xt=urn:btih:" + "ab" * 20, Route.MOVIE, "/media")

    data = client._request.await_args.kwargs["data"]
    assert data["tags"] == "discord-movie"
    assert "category" not in data


@pytest.mark.asyncio
async def test_prowlarr_download_accepts_magnet_redirect() -> None:
    magnet = "magnet:?xt=urn:btih:" + "cd" * 20
    client = ProwlarrClient(
        RedirectSession(magnet),  # type: ignore[arg-type]
        "http://prowlarr",
        "key",
    )
    result = SearchResult(
        result_id="result",
        title="Example",
        size=1,
        seeders=1,
        leechers=0,
        indexer="General",
        category="TV",
        download_url="/download",
    )

    assert await client.download(result) == magnet


def test_prowlarr_links_are_normalized_when_fields_are_swapped() -> None:
    magnet = "magnet:?xt=urn:btih:" + "ef" * 20

    download_url, normalized_magnet = normalize_result_links(
        magnet, "https://indexer.example/download/1", "https://indexer.example/item/1"
    )

    assert normalized_magnet == magnet
    assert download_url == "https://indexer.example/download/1"


def test_jellyfin_search_retries_without_year() -> None:
    assert jellyfin_search_terms("Everything Everywhere All At Once (2022)") == [
        "Everything Everywhere All At Once (2022)",
        "Everything Everywhere All At Once",
    ]


def test_jellyfin_search_retries_without_alternate_title() -> None:
    assert jellyfin_search_terms("Witch Hat Atelier (Tongari Boushi no Atelier") == [
        "Witch Hat Atelier (Tongari Boushi no Atelier",
        "Witch Hat Atelier",
    ]


@pytest.mark.asyncio
async def test_jellyfin_remote_poster_uses_metadata_provider() -> None:
    client = JellyfinClient(PosterSession(), "http://jellyfin", "key")  # type: ignore[arg-type]

    assert await client.remote_poster("Hamnet (2025)", series=False) == (
        "https://image.tmdb.org/t/p/original/poster.jpg"
    )


def test_poster_url_rejects_private_or_insecure_images() -> None:
    assert public_poster_url("http://image.example/poster.jpg") is None
    assert public_poster_url("https://127.0.0.1/poster.jpg") is None
    assert public_poster_url("https://192.168.100.205/poster.jpg") is None
    assert public_poster_url("https://image.tmdb.org/poster.jpg") is not None


def test_torrent_must_be_fully_downloaded_before_organization() -> None:
    status = TorrentStatus(
        info_hash="ab" * 20,
        name="Almost complete",
        state="downloading",
        progress=0.9999,
        download_speed=1,
        eta=1,
        content_path="/media/file",
    )

    assert status.complete is False
    status.progress = 1.0
    status.state = "stalledUP"
    assert status.complete is True
