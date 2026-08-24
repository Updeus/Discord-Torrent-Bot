#!/usr/bin/env python3
"""Configure local service integrations without printing secret values."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def update_env(path: Path, updates: dict[str, str]) -> None:
    original = path.stat() if path.exists() else None
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(line)
    if output and output[-1]:
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    if original:
        os.chown(temporary, original.st_uid, original.st_gid)
    os.replace(temporary, path)


def api_request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    payload: Any | None = None,
) -> Any:
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read()
    return json.loads(data) if data else None


def configure_prowlarr(base_url: str, config_path: Path, updates: dict[str, str]) -> None:
    root = ET.parse(config_path).getroot()
    api_key = root.findtext("ApiKey")
    if not api_key:
        raise RuntimeError("Prowlarr config does not contain an API key")
    headers = {"X-Api-Key": api_key}
    updates["PROWLARR_BASE_URL"] = base_url
    updates["PROWLARR_API_KEY"] = api_key

    configured = api_request(f"{base_url}/api/v1/indexer", headers=headers)
    if any(indexer.get("name", "").casefold() == "nyaa" for indexer in configured):
        print("Prowlarr: Nyaa already configured")
        return

    schemas = api_request(f"{base_url}/api/v1/indexer/schema", headers=headers)
    schema = next(
        (
            item
            for item in schemas
            if item.get("definitionName", "").casefold() in {"nyaa", "nyaasi"}
            or item.get("name", "").casefold() == "nyaa"
        ),
        None,
    )
    if schema is None:
        raise RuntimeError("Prowlarr does not expose a Nyaa indexer schema")
    profiles = api_request(f"{base_url}/api/v1/appprofile", headers=headers)
    if not profiles:
        raise RuntimeError("Prowlarr does not expose an application profile")
    schema.update(
        {
            "name": "Nyaa",
            "enableRss": True,
            "enableAutomaticSearch": True,
            "enableInteractiveSearch": True,
            "priority": 25,
            "appProfileId": profiles[0]["id"],
        }
    )
    api_request(f"{base_url}/api/v1/indexer", headers=headers, method="POST", payload=schema)
    print("Prowlarr: configured Nyaa")


def jellyfin_token(db_path: Path, base_url: str) -> str:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as database:
        row = database.execute(
            "SELECT AccessToken FROM ApiKeys WHERE Name = ? ORDER BY DateCreated DESC LIMIT 1",
            ("Discord Media Bot",),
        ).fetchone()
        if row:
            return str(row[0])
        row = database.execute(
            "SELECT AccessToken FROM ApiKeys ORDER BY DateLastActivity DESC LIMIT 1"
        ).fetchone()
    if not row:
        token = secrets.token_hex(16)
        now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with sqlite3.connect(db_path) as database:
            database.execute(
                "INSERT INTO ApiKeys "
                "(DateCreated, DateLastActivity, Name, AccessToken) "
                "VALUES (?, ?, ?, ?)",
                (now, now, "Discord Media Bot", token),
            )
        print("Jellyfin: bootstrapped a scoped API key")
        return token

    bootstrap_token = str(row[0])
    api_request(
        f"{base_url}/Auth/Keys?{urllib.parse.urlencode({'app': 'Discord Media Bot'})}",
        headers={"X-Emby-Token": bootstrap_token},
        method="POST",
    )
    for _ in range(10):
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as database:
            created = database.execute(
                "SELECT AccessToken FROM ApiKeys WHERE Name = ? ORDER BY DateCreated DESC LIMIT 1",
                ("Discord Media Bot",),
            ).fetchone()
        if created:
            return str(created[0])
        time.sleep(0.25)
    raise RuntimeError("Jellyfin accepted the key request but it was not persisted")


def configure_jellyfin(base_url: str, db_path: Path, updates: dict[str, str]) -> None:
    api_key = jellyfin_token(db_path, base_url)
    libraries = api_request(f"{base_url}/Library/VirtualFolders", headers={"X-Emby-Token": api_key})

    def select_library(kind: str) -> dict[str, Any] | None:
        suffix = f"/{kind.casefold()}"
        for library in libraries:
            name = library.get("Name", "").casefold()
            locations = [str(item).rstrip("/").casefold() for item in library.get("Locations", [])]
            if name == f"collection {kind.casefold()}" or any(
                location.endswith(suffix) for location in locations
            ):
                return library
        return None

    movies = select_library("Movies")
    shows = select_library("Shows")
    if not movies or not shows:
        raise RuntimeError("Jellyfin Collection Movies/Shows libraries were not found")
    updates.update(
        {
            "JELLYFIN_BASE_URL": base_url,
            "JELLYFIN_API_KEY": api_key,
            "JELLYFIN_MOVIES_LIBRARY_ID": str(movies["ItemId"]),
            "JELLYFIN_SHOWS_LIBRARY_ID": str(shows["ItemId"]),
        }
    )
    print("Jellyfin: configured Collection Movies and Collection Shows")


def configure_discord(env: dict[str, str], updates: dict[str, str]) -> None:
    current = env.get("DISCORD_GUILD_ID", "").strip()
    if current and current != "0":
        print("Discord: retained configured server ID")
        return
    token = env.get("DISCORD_TOKEN") or env.get("DISCORD_BOT_TOKEN") or env.get("TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing from the environment file")
    try:
        guilds = api_request(
            "https://discord.com/api/v10/users/@me/guilds",
            headers={"Authorization": f"Bot {token}"},
        )
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise
        updates["DISCORD_BOT_TOKEN"] = token
        print("Discord: server ID will be discovered from the gateway connection")
        return
    if len(guilds) != 1:
        visible = ", ".join(f"{guild['name']} ({guild['id']})" for guild in guilds)
        raise RuntimeError(f"Expected one Discord server; found: {visible or 'none'}")
    updates["DISCORD_GUILD_ID"] = str(guilds[0]["id"])
    updates["DISCORD_BOT_TOKEN"] = token
    print(f"Discord: selected {guilds[0]['name']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument(
        "--prowlarr-config",
        type=Path,
        default=Path("/DATA/AppData/prowlarr/config/config.xml"),
    )
    parser.add_argument(
        "--jellyfin-db",
        type=Path,
        default=Path("/DATA/AppData/jellyfin/config/data/data/jellyfin.db"),
    )
    args = parser.parse_args()
    env = read_env(args.env)
    updates = {
        "DATABASE_PATH": "/home/jarod/Discord-Torrent-Bot/data/torrent-bot.sqlite3",
        "DOWNLOAD_ROOT": "/mnt/AWA/Collection",
        "JELLYFIN_VIEW_ROOT": "/mnt/AWA/Collection/JellyfinView2",
        "TZ": "America/Port_of_Spain",
    }
    try:
        configure_prowlarr("http://127.0.0.1:9696", args.prowlarr_config, updates)
        configure_jellyfin("http://127.0.0.1:8097", args.jellyfin_db, updates)
        configure_discord(env, updates)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Integration request failed with HTTP {error.code}") from error
    update_env(args.env, updates)
    print(f"Updated {args.env} with mode 0600; secret values were not displayed")


if __name__ == "__main__":
    main()
