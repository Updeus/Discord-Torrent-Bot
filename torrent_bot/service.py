from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import bencodepy

from .clients import JellyfinClient, NyaaProvider, ProwlarrClient, QBittorrentClient, ServiceError
from .config import Settings
from .database import Database
from .models import TERMINAL_STATES, MediaRequest, RequestState, Route, SearchResult
from .organizer import (
    EPISODE_PATTERN,
    NUMBERED_EPISODE_PATTERN,
    SEASON_PATTERN,
    OrganizationResult,
    movie_name,
    organize_item,
    series_name,
)
from .utils import magnet_info_hash

LOGGER = logging.getLogger(__name__)
Notifier = Callable[[MediaRequest], Awaitable[None]]
JELLYFIN_TIMEOUT_ERROR = "Organized, but Jellyfin did not report the item before the timeout"


def awaiting_jellyfin(request: MediaRequest) -> bool:
    return request.state == RequestState.SCANNING or (
        request.state == RequestState.NEEDS_ATTENTION and request.error == JELLYFIN_TIMEOUT_ERROR
    )


def monitor_interval(poll_seconds: int, downloading: bool) -> float:
    return max(1.0, poll_seconds / 2) if downloading else float(poll_seconds)


def poster_lookup(request: MediaRequest) -> tuple[str, bool]:
    source = Path(request.title)
    series = request.route in {Route.SHOW, Route.ANIME} or (
        request.route == Route.AUTO
        and bool(
            EPISODE_PATTERN.search(request.title)
            or SEASON_PATTERN.search(request.title)
            or NUMBERED_EPISODE_PATTERN.search(source.stem)
        )
    )
    cleaned = series_name(source) if series else movie_name(source.stem)
    return cleaned, series


def torrent_file_hash(payload: bytes) -> str:
    decoded = bencodepy.decode(payload)
    if not isinstance(decoded, dict) or b"info" not in decoded:
        raise ValueError("Torrent file has no info dictionary")
    return hashlib.sha1(bencodepy.encode(decoded[b"info"])).hexdigest()


class MediaService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        qbit: QBittorrentClient,
        prowlarr: ProwlarrClient,
        nyaa: NyaaProvider,
        jellyfin: JellyfinClient,
    ):
        self.settings = settings
        self.database = database
        self.qbit = qbit
        self.prowlarr = prowlarr
        self.nyaa = nyaa
        self.jellyfin = jellyfin
        self.notifier: Notifier | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._poster_tasks: dict[int, asyncio.Task[None]] = {}
        self._stop = asyncio.Event()

    async def start(self) -> None:
        try:
            await self.qbit.ensure_categories(str(self.settings.collection_source))
        except Exception:
            LOGGER.exception("Unable to initialize qBittorrent categories; monitor will retry")
        self._monitor_task = asyncio.create_task(self._monitor(), name="media-monitor")

    async def close(self) -> None:
        self._stop.set()
        if self._monitor_task:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        for task in self._poster_tasks.values():
            task.cancel()
        await asyncio.gather(*self._poster_tasks.values(), return_exceptions=True)
        self._poster_tasks.clear()

    async def _notify(self, request: MediaRequest) -> None:
        if self.notifier:
            try:
                await self.notifier(request)
            except Exception:
                LOGGER.exception("Failed to update Discord status for request %s", request.id)

    async def _update(self, request_id: int, **values: object) -> MediaRequest:
        request = await self.database.update_request(request_id, **values)
        await self._notify(request)
        return request

    async def add_magnet(
        self, magnet: str, route: Route, requester_id: int, guild_id: int, channel_id: int
    ) -> tuple[MediaRequest, bool]:
        info_hash = magnet_info_hash(magnet)
        existing = await self.database.get_by_hash(info_hash)
        if existing and existing.state != RequestState.REMOVED:
            return existing, False
        if existing:
            request = await self.database.update_request(
                existing.id,
                title="Resolving metadata",
                route=route,
                state=RequestState.QUEUED,
                error=None,
                source_uri=magnet,
            )
        else:
            request = await self.database.create_request(
                info_hash=info_hash,
                title="Resolving metadata",
                route=route,
                state=RequestState.QUEUED,
                requester_id=requester_id,
                guild_id=guild_id,
                channel_id=channel_id,
                source_uri=magnet,
            )
        try:
            await self.qbit.add_magnet(magnet, route, str(self.settings.collection_source))
            await self.database.audit(requester_id, "add", request.id, route=route.value)
        except Exception as error:
            request = await self._update(request.id, state=RequestState.ERROR, error=str(error))
            return request, True
        return request, True

    async def schedule_magnet(
        self,
        magnet: str,
        route: Route,
        when: datetime,
        requester_id: int,
        guild_id: int,
        channel_id: int,
    ) -> tuple[MediaRequest, bool]:
        info_hash = magnet_info_hash(magnet)
        existing = await self.database.get_by_hash(info_hash)
        if existing and existing.state != RequestState.REMOVED:
            return existing, False
        request = await self.database.create_request(
            info_hash=info_hash,
            title="Scheduled torrent",
            route=route,
            state=RequestState.SCHEDULED,
            requester_id=requester_id,
            guild_id=guild_id,
            channel_id=channel_id,
            scheduled_for=when.astimezone(UTC).isoformat(),
            source_uri=magnet,
        )
        await self.database.audit(requester_id, "schedule", request.id, when=request.scheduled_for)
        return request, True

    async def search(self, query: str, user_id: int) -> list[SearchResult]:
        min_size, max_size = await self.database.get_preferences(user_id)
        try:
            if not self.settings.prowlarr_enabled:
                raise ServiceError("Prowlarr is not configured")
            results = await self.prowlarr.search(query)
        except Exception as error:
            LOGGER.warning("Prowlarr unavailable; using Nyaa fallback: %s", error)
            results = await self.nyaa.search(query)
        filtered = [
            result
            for result in results
            if result.size >= min_size and (max_size is None or result.size <= max_size)
        ]
        normalized_query = " ".join(query.casefold().split())

        def search_rank(result: SearchResult) -> tuple[bool, bool, int, int]:
            normalized_title = " ".join(result.title.casefold().split())
            return (
                normalized_title.startswith(normalized_query),
                normalized_query in normalized_title,
                result.seeders,
                result.size,
            )

        filtered.sort(key=search_rank, reverse=True)
        filtered = filtered[:25]
        await self.database.save_search(user_id, query, filtered)
        return filtered

    async def add_search_result(
        self,
        result: SearchResult,
        route: Route,
        requester_id: int,
        guild_id: int,
        channel_id: int,
    ) -> tuple[MediaRequest, bool]:
        if result.magnet and result.magnet.lower().startswith("magnet:?"):
            return await self.add_magnet(result.magnet, route, requester_id, guild_id, channel_id)
        download = await self.prowlarr.download(result)
        if isinstance(download, str):
            return await self.add_magnet(download, route, requester_id, guild_id, channel_id)
        filename, payload = download
        info_hash = torrent_file_hash(payload)
        existing = await self.database.get_by_hash(info_hash)
        if existing and existing.state != RequestState.REMOVED:
            return existing, False
        request = await self.database.create_request(
            info_hash=info_hash,
            title=result.title,
            route=route,
            state=RequestState.QUEUED,
            requester_id=requester_id,
            guild_id=guild_id,
            channel_id=channel_id,
        )
        try:
            await self.qbit.add_torrent_file(
                filename, payload, route, str(self.settings.collection_source)
            )
            await self.database.audit(
                requester_id, "add_search_result", request.id, result=result.result_id
            )
        except Exception as error:
            request = await self._update(request.id, state=RequestState.ERROR, error=str(error))
        return request, True

    async def control(
        self, request_id: int, action: str, actor_id: int, *, delete_files: bool = False
    ) -> MediaRequest:
        request = await self.database.get_request(request_id)
        if action == "retry":
            if not request.source_uri:
                raise ValueError("This request cannot be retried because its source is not stored")
            await self.qbit.add_magnet(
                request.source_uri, request.route, str(self.settings.collection_source)
            )
            result = await self._update(request.id, state=RequestState.QUEUED, error=None)
        elif action in {"pause", "resume", "delete"}:
            await self.qbit.action(request.info_hash, action, delete_files)
            state = {
                "pause": RequestState.PAUSED,
                "resume": RequestState.DOWNLOADING,
                "delete": RequestState.REMOVED,
            }[action]
            result = await self._update(request.id, state=state, error=None)
        else:
            raise ValueError(f"Unsupported action: {action}")
        await self.database.audit(actor_id, action, request.id, delete_files=delete_files)
        return result

    async def change_route(self, request_id: int, route: Route, actor_id: int) -> MediaRequest:
        request = await self.database.get_request(request_id)
        await self.qbit.set_route(request.info_hash, route)
        result = await self._update(request.id, route=route, error=None)
        await self.database.audit(actor_id, "route", request.id, route=route.value)
        return result

    async def _monitor(self) -> None:
        while not self._stop.is_set():
            downloading = False
            try:
                downloading = await self._monitor_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Media monitor iteration failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=monitor_interval(self.settings.poll_seconds, downloading),
                )
            except TimeoutError:
                pass

    async def _monitor_once(self) -> bool:
        requests = await self.database.list_requests(limit=100)
        now = datetime.now(UTC)
        downloading = False
        for request in requests:
            if request.state in TERMINAL_STATES and not awaiting_jellyfin(request):
                continue
            try:
                if request.state == RequestState.SCHEDULED:
                    if (
                        request.scheduled_for
                        and datetime.fromisoformat(request.scheduled_for) <= now
                    ):
                        if not request.source_uri:
                            await self._update(
                                request.id,
                                state=RequestState.ERROR,
                                error="Scheduled magnet missing",
                            )
                        else:
                            await self.qbit.add_magnet(
                                request.source_uri,
                                request.route,
                                str(self.settings.collection_source),
                            )
                            await self._update(
                                request.id, state=RequestState.QUEUED, scheduled_for=None
                            )
                    continue
                if awaiting_jellyfin(request):
                    await self._verify_jellyfin(request)
                    continue
                status = await self.qbit.torrent(request.info_hash)
                if status is None:
                    if request.state not in {RequestState.ERROR, RequestState.REMOVED}:
                        await self._update(request.id, state=RequestState.QUEUED)
                    continue
                state = RequestState.PAUSED if status.paused else RequestState.DOWNLOADING
                values = {
                    "title": status.name,
                    "qbit_name": status.name,
                    "content_path": status.content_path,
                    "progress": status.progress,
                    "download_speed": status.download_speed,
                    "eta": status.eta,
                    "state": state,
                    "error": None,
                }
                if status.complete:
                    values["state"] = RequestState.ORGANIZING
                request = await self._update(request.id, **values)
                if request.state == RequestState.DOWNLOADING:
                    downloading = True
                    self._schedule_poster(request)
                if status.complete:
                    await self._organize(request)
            except Exception as error:
                LOGGER.exception("Request %s monitor failed", request.id)
                await self._update(request.id, state=RequestState.ERROR, error=str(error)[:500])
        return downloading

    def _schedule_poster(self, request: MediaRequest, *, force: bool = False) -> None:
        if not self.settings.jellyfin_enabled or request.id in self._poster_tasks:
            return
        if request.poster_url is not None and not force:
            return
        task = asyncio.create_task(self._resolve_poster(request), name=f"poster-{request.id}")
        self._poster_tasks[request.id] = task
        task.add_done_callback(lambda _: self._poster_tasks.pop(request.id, None))

    async def _resolve_poster(self, request: MediaRequest) -> None:
        try:
            title, series = poster_lookup(request)
            poster_url = await self.jellyfin.remote_poster(title, series=series)
        except Exception as error:
            LOGGER.warning(
                "Poster lookup failed for request %s (%s)", request.id, type(error).__name__
            )
            poster_url = None
        await self._update(request.id, poster_url=poster_url or "")

    async def _organize(self, request: MediaRequest) -> None:
        path = Path(request.content_path or "")
        if not await asyncio.to_thread(path.exists):
            await self._update(
                request.id,
                state=RequestState.ERROR,
                error=f"Completed content path is unavailable: {path}",
            )
            return
        result: OrganizationResult = await asyncio.to_thread(
            organize_item,
            path,
            self.settings.collection_view,
            route=request.route,
            apply=True,
        )
        if result.kind in {"ambiguous", "unsupported"}:
            await self._update(
                request.id,
                state=RequestState.NEEDS_ATTENTION,
                title=result.title,
                error=result.reason,
            )
            return
        actual_route = request.route
        if request.route == Route.AUTO:
            actual_route = Route.SHOW if result.kind == "show" else Route.MOVIE
            await self.qbit.set_route(request.info_hash, actual_route)
        request = await self._update(
            request.id,
            state=RequestState.SCANNING,
            title=result.title,
            route=actual_route,
            error=None,
        )
        if not self.settings.jellyfin_enabled:
            await self._update(
                request.id,
                state=RequestState.NEEDS_ATTENTION,
                error="Organized successfully; Jellyfin API is not configured",
            )
            return
        library_id = (
            self.settings.jellyfin_shows_library_id
            if result.kind == "show"
            else self.settings.jellyfin_movies_library_id
        )
        await self.jellyfin.refresh_library(library_id)

    async def _verify_jellyfin(self, request: MediaRequest) -> None:
        library_id = (
            self.settings.jellyfin_shows_library_id
            if request.route in {Route.SHOW, Route.ANIME}
            else self.settings.jellyfin_movies_library_id
        )
        item = await self.jellyfin.find_item(request.title, library_id)
        if item:
            await self._update(
                request.id,
                state=RequestState.READY,
                title=str(item.get("Name") or request.title),
                jellyfin_item_id=str(item.get("Id", "")),
                progress=1.0,
                download_speed=0,
                eta=0,
                error=None,
            )
            if not request.poster_url:
                self._schedule_poster(request, force=True)
            return
        updated = datetime.fromisoformat(request.updated_at)
        if (datetime.now(UTC) - updated).total_seconds() >= self.settings.jellyfin_timeout_seconds:
            if request.state != RequestState.NEEDS_ATTENTION:
                await self._update(
                    request.id,
                    state=RequestState.NEEDS_ATTENTION,
                    error=JELLYFIN_TIMEOUT_ERROR,
                )
