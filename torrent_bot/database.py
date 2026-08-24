from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from .models import MediaRequest, RequestState, Route, SearchResult

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
INSERT INTO schema_version(version)
SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);

CREATE TABLE IF NOT EXISTS media_requests(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    info_hash TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT 'Resolving metadata',
    route TEXT NOT NULL,
    state TEXT NOT NULL,
    requester_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    qbit_name TEXT,
    content_path TEXT,
    progress REAL NOT NULL DEFAULT 0,
    download_speed INTEGER NOT NULL DEFAULT 0,
    eta INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    jellyfin_item_id TEXT,
    scheduled_for TEXT,
    source_uri TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_requests_state ON media_requests(state);
CREATE INDEX IF NOT EXISTS idx_media_requests_user ON media_requests(requester_id, created_at);

CREATE TABLE IF NOT EXISTS user_preferences(
    user_id INTEGER PRIMARY KEY,
    min_size_bytes INTEGER NOT NULL DEFAULT 0,
    max_size_bytes INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS guild_settings(
    guild_id INTEGER PRIMARY KEY,
    command_prefix TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    query TEXT NOT NULL,
    results_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    request_id INTEGER,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(request_id) REFERENCES media_requests(id)
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.executescript(SCHEMA)
        await self.connection.commit()

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()
            self.connection = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not connected")
        return self.connection

    async def create_request(
        self,
        *,
        info_hash: str,
        title: str,
        route: Route,
        state: RequestState,
        requester_id: int,
        guild_id: int,
        channel_id: int,
        scheduled_for: str | None = None,
        source_uri: str | None = None,
    ) -> MediaRequest:
        now = utc_now()
        cursor = await self.db.execute(
            """INSERT INTO media_requests(
                info_hash,title,route,state,requester_id,guild_id,channel_id,
                scheduled_for,source_uri,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                info_hash,
                title,
                route.value,
                state.value,
                requester_id,
                guild_id,
                channel_id,
                scheduled_for,
                source_uri,
                now,
                now,
            ),
        )
        await self.db.commit()
        return await self.get_request(cursor.lastrowid)

    async def get_request(self, request_id: int) -> MediaRequest:
        cursor = await self.db.execute("SELECT * FROM media_requests WHERE id=?", (request_id,))
        row = await cursor.fetchone()
        if row is None:
            raise LookupError(f"Request {request_id} was not found")
        return MediaRequest.from_row(row)

    async def get_by_hash(self, info_hash: str) -> MediaRequest | None:
        cursor = await self.db.execute(
            "SELECT * FROM media_requests WHERE info_hash=?", (info_hash.lower(),)
        )
        row = await cursor.fetchone()
        return MediaRequest.from_row(row) if row else None

    async def list_requests(
        self, states: Iterable[RequestState] | None = None, limit: int = 25
    ) -> list[MediaRequest]:
        params: list[Any] = []
        where = ""
        if states:
            state_values = [state.value for state in states]
            where = f"WHERE state IN ({','.join('?' for _ in state_values)})"
            params.extend(state_values)
        params.append(limit)
        cursor = await self.db.execute(
            f"SELECT * FROM media_requests {where} ORDER BY created_at DESC LIMIT ?", params
        )
        return [MediaRequest.from_row(row) for row in await cursor.fetchall()]

    async def update_request(self, request_id: int, **values: Any) -> MediaRequest:
        if not values:
            return await self.get_request(request_id)
        allowed = {
            "title",
            "route",
            "state",
            "message_id",
            "qbit_name",
            "content_path",
            "progress",
            "download_speed",
            "eta",
            "error",
            "jellyfin_item_id",
            "scheduled_for",
            "source_uri",
        }
        invalid = set(values) - allowed
        if invalid:
            raise ValueError(f"Unsupported request fields: {sorted(invalid)}")
        for key, value in list(values.items()):
            if isinstance(value, (Route, RequestState)):
                values[key] = value.value
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in values)
        await self.db.execute(
            f"UPDATE media_requests SET {assignments} WHERE id=?",
            (*values.values(), request_id),
        )
        await self.db.commit()
        return await self.get_request(request_id)

    async def set_preferences(
        self, user_id: int, min_size_bytes: int, max_size_bytes: int | None
    ) -> None:
        await self.db.execute(
            """INSERT INTO user_preferences(user_id,min_size_bytes,max_size_bytes,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            min_size_bytes=excluded.min_size_bytes,max_size_bytes=excluded.max_size_bytes,
            updated_at=excluded.updated_at""",
            (user_id, min_size_bytes, max_size_bytes, utc_now()),
        )
        await self.db.commit()

    async def get_preferences(self, user_id: int) -> tuple[int, int | None]:
        cursor = await self.db.execute(
            "SELECT min_size_bytes,max_size_bytes FROM user_preferences WHERE user_id=?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else (0, None)

    async def set_prefix(self, guild_id: int, prefix: str) -> None:
        await self.db.execute(
            """INSERT INTO guild_settings(guild_id,command_prefix,updated_at) VALUES(?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET command_prefix=excluded.command_prefix,
            updated_at=excluded.updated_at""",
            (guild_id, prefix, utc_now()),
        )
        await self.db.commit()

    async def get_prefix(self, guild_id: int, default: str) -> str:
        cursor = await self.db.execute(
            "SELECT command_prefix FROM guild_settings WHERE guild_id=?", (guild_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else default

    async def save_search(self, user_id: int, query: str, results: list[SearchResult]) -> None:
        payload = json.dumps(
            [{field: getattr(result, field) for field in result.__slots__} for result in results]
        )
        await self.db.execute(
            "INSERT INTO search_history(user_id,query,results_json,created_at) VALUES(?,?,?,?)",
            (user_id, query, payload, utc_now()),
        )
        await self.db.commit()

    async def recent_search(self, user_id: int) -> str | None:
        cursor = await self.db.execute(
            "SELECT query FROM search_history WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def audit(
        self, actor_id: int, action: str, request_id: int | None, **details: Any
    ) -> None:
        await self.db.execute(
            "INSERT INTO audit_log(actor_id,action,request_id,details,created_at) VALUES(?,?,?,?,?)",
            (actor_id, action, request_id, json.dumps(details), utc_now()),
        )
        await self.db.commit()

    async def stats(self) -> dict[str, int]:
        cursor = await self.db.execute(
            "SELECT state,COUNT(*) AS total FROM media_requests GROUP BY state"
        )
        result = {row["state"]: row["total"] for row in await cursor.fetchall()}
        cursor = await self.db.execute("SELECT COUNT(*) FROM search_history")
        result["searches"] = (await cursor.fetchone())[0]
        return result
