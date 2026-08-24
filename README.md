# Discord Media Bot

A persistent Discord → Prowlarr → qBittorrent → Jellyfin automation service.

The original `!add <magnet>` command is preserved. The rebuilt bot also tracks
downloads, maintains a Jellyfin-friendly hard-link view, verifies completed
items in Jellyfin, and exposes equivalent Discord slash commands.

## How media flows

1. A Discord member adds a magnet or selects a Prowlarr search result.
2. qBittorrent downloads into `/mnt/AWA/Collection` with a routing category.
3. The bot waits for qBittorrent to report completion.
4. Media is hard-linked into `JellyfinView2/Movies` or `JellyfinView2/Shows`.
5. The correct Jellyfin library is refreshed and queried until the item appears.
6. One Discord status card is updated through the entire lifecycle.

Source downloads are never renamed or moved, so they remain seedable. Isolated
episodes such as `Show.Name.S02E04.mkv` are retained and placed under the
inferred show and season.

## Commands

- `!add <magnet>` / `/add` — add with automatic routing.
- `!addmovie`, `!addshow`, `!addanime` — explicit legacy routing commands.
- `!search <query>` / `/search` — aggregated Prowlarr search with Nyaa fallback.
- `!downloads`, `!status`, `!pause`, `!resume`, `!retry`, `!route`, `!remove`.
- `!schedule`, `!recent_searches`, `!recent_additions`, `!setfilter`, `!stats`.
- `!test_qbittorrent` — check qBittorrent, Prowlarr, and Jellyfin health.

All primary commands have slash-command equivalents. Removing downloaded files
requires typing `DELETE` in a confirmation modal and is written to the audit log.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\ruff check bot.py torrent_bot tests
```

Copy `.env.example` to `.env`, fill in the credentials, and run:

```powershell
.\.venv\Scripts\python bot.py
```

The SQLite database is created automatically. Schema migrations are applied on
startup, and WAL mode allows the organizer and bot to inspect state safely.

## Homeserver deployment

Deployment templates are in `deploy/`. Prowlarr binds only to
`127.0.0.1:9696`; use an SSH tunnel when its web interface needs configuration:

```powershell
ssh -L 9696:127.0.0.1:9696 jarod@192.168.100.205
```

The system service runs the bot from `/home/jarod/Discord-Torrent-Bot`. The user
path and timer units retain the filesystem fallback organizer. Secrets stay in
the existing mode-`0600` `.env` file and are never committed.

Only configure Prowlarr indexers and download content you are authorized to use.
