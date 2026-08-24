# Reliable Discord-to-Jellyfin Media Bot

A persistent, asynchronous media automation service connecting:

```text
Discord → Prowlarr → qBittorrent → hard-link organizer → Jellyfin
```

The original `!add <magnet>` command remains available. The bot adds persistent
request tracking, interactive searches, routing controls, editable status
cards, scheduled additions, safe organization, and Jellyfin readiness checks.

## Highlights

- Asynchronous clients built with `aiohttp`, including timeouts, retry handling,
  authentication recovery, and graceful shutdown.
- SQLite persistence for requests, schedules, preferences, Discord message IDs,
  state transitions, searches, routing changes, and audit events.
- Duplicate detection for BitTorrent v1 and v2 info hashes before submission.
- Prowlarr-backed multi-indexer search with a Nyaa-only fallback during outages.
- qBittorrent sync monitoring and dedicated routing categories.
- Hard-link organization that leaves the original seeding files unchanged.
- Separate Jellyfin Movies and Shows views, library scans, and readiness checks.
- Native `discord.ui` buttons, pagination, status embeds, and deletion modals.
- systemd service, path watcher, 15-minute fallback timer, and journald logging.
- Secret redaction and a mode-`0600` environment file.

## Media lifecycle

Each request follows a recoverable state machine:

```text
queued → downloading → organizing → scanning → ready
                         ├→ needs_attention
                         └→ error
```

Requests can also be paused, resumed, rerouted, or retried. State and the
original Discord status message survive service restarts.

## Organization and routing

All qBittorrent categories retain the same source download directory:

| Category | Purpose |
| --- | --- |
| `discord-auto` | Classify from the release name and files |
| `discord-movie` | Force movie routing |
| `discord-show` | Force TV-show routing |
| `discord-anime` | Force anime/show routing |

Explicit categories take precedence over filename classification. Completed,
stable media is hard-linked into:

```text
JellyfinView2/
├── Movies/
└── Shows/
    └── Show Name/
        └── Season 02/
```

Single episodes such as `Show.Name.S02E04.mkv` or `Show.Name.2x04.mkv` are
retained and placed in the inferred show and season. Subtitles and extras are
preserved. Samples, incomplete files, non-media downloads, and genuinely
ambiguous folders remain untouched and are reported for attention.

The organizer never renames, moves, or modifies qBittorrent's source files.
Stale managed view links are pruned without deleting the source content.

## Discord commands

Legacy prefix commands and slash-command equivalents are available.

### Add and search

| Command | Description |
| --- | --- |
| `!add <magnet>` / `/add` | Add a magnet with auto, movie, show, or anime routing |
| `!addmovie <magnet>` | Add with movie routing |
| `!addshow <magnet>` | Add with show routing |
| `!addanime <magnet>` | Add with anime routing |
| `!search <query>` / `/search` | Search Prowlarr with paginated add buttons |
| `!schedule` / `/schedule` | Schedule a persistent future addition |

### Operations

| Command | Description |
| --- | --- |
| `!downloads` / `/downloads` | List active and attention-needed requests |
| `!status <id>` / `/status` | Show the current request status |
| `!pause`, `!resume`, `!retry` | Control a request |
| `!route <id> <route>` | Change its routing override |
| `!remove <id>` / `/remove` | Remove a torrent or request confirmed file deletion |

File deletion displays the media title and requires typing `DELETE`. The actor,
timestamp, target, action, and outcome are written to the audit log.

### Preferences and diagnostics

- `!recent_searches` and `!recent_additions`
- `!setprefix`
- `!setfilter <minimum_mb> <maximum_mb>`
- `!stats`
- `!test_qbittorrent`
- `!help_command`

## Configuration

Copy `.env.example` to `.env` and supply local credentials:

```bash
cp .env.example .env
chmod 600 .env
```

Important variables:

| Variable | Description |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Discord application bot token |
| `DISCORD_GUILD_ID` | Optional server restriction; `0` allows every joined server |
| `QBITTORRENT_BASE_URL` | qBittorrent Web UI/API address |
| `QBITTORRENT_USERNAME` / `QBITTORRENT_PASSWORD` | qBittorrent credentials |
| `PROWLARR_BASE_URL` / `PROWLARR_API_KEY` | Prowlarr API configuration |
| `JELLYFIN_BASE_URL` / `JELLYFIN_API_KEY` | Jellyfin API configuration |
| `JELLYFIN_MOVIES_LIBRARY_ID` | Jellyfin Movies library ID |
| `JELLYFIN_SHOWS_LIBRARY_ID` | Jellyfin Shows library ID |
| `COLLECTION_SOURCE` | qBittorrent source directory |
| `COLLECTION_VIEW` | Hard-link view root consumed by Jellyfin |
| `DATABASE_PATH` | Persistent SQLite database path |
| `TZ` | Timezone used for legacy schedules |

Do not commit `.env`. Magnet links and API credentials are never included in
Discord messages or normal application logs.

## Local development

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python bot.py
```

Linux/macOS:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python bot.py
```

The database and schema are initialized automatically at startup.

## Deployment

Deployment templates are under [`deploy/`](deploy/):

- `compose.prowlarr.yml` runs a digest-pinned LinuxServer Prowlarr image and
  binds its interface to `127.0.0.1:9696` only.
- `discord-bot.service` runs the persistent Discord service.
- `jellyfin-collection-organizer.path` watches for top-level source changes.
- `jellyfin-collection-organizer.timer` runs the 15-minute fallback scan.
- `configure_runtime.py` configures local integrations without displaying keys.

The runtime configurator idempotently adds Nyaa (anime), YTS (movies), and
multiple general-purpose public indexers. Providers that fail live validation
are skipped rather than breaking the working search set. Only enable and use
indexers whose terms and content you are authorized to access.

Review and replace the example user names, paths, UID/GID, timezone, and ports
before installing the templates on another server.

Run an organizer dry-run before enabling its service:

```bash
python -m torrent_bot.organizer \
  /path/to/Collection \
  /path/to/Collection/JellyfinView2 \
  --min-age-seconds 120
```

Add `--apply` only after reviewing the classification counts. Applying creates
and prunes hard links; it does not modify source downloads.

## Tests

The test suite covers:

- magnet validation and v1/v2 info hashes;
- duplicate detection, state transitions, and database recovery;
- release-name cleanup, size parsing, routing precedence, and season inference;
- isolated episodes, season packs, movies, extras, subtitles, and ambiguity;
- hard-link creation, unchanged source files, and safe stale-link cleanup;
- mocked qBittorrent, Prowlarr, Jellyfin, and Discord failure paths.

```bash
python -m pytest -q
```

## Responsible use

Configure only indexers you are authorized to access and add only media you are
legally permitted to download and share. Prowlarr should remain private and
must not be exposed directly to the public internet.
