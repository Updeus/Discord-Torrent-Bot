# Discord Torrent Bot

This is a Discord bot that allows users to search for torrents on Nyaa.si and add them to qBittorrent. The bot supports various commands for searching, adding torrents, viewing recent searches and additions, setting filters, scheduling downloads, and more.

## Prerequisites

- Python 3.8 or higher
- `discord.py` library
- `requests` library
- `beautifulsoup4` library
- `apscheduler` library
- A qBittorrent Web UI instance running

## Installation

1. Clone this repository:
    ```bash
    git clone https://github.com/Updeus/Discord-Torrent-Bot.git
    cd discord-torrent-bot
    ```

2. Install the required Python packages:
    ```bash
    pip install discord.py requests beautifulsoup4 apscheduler
    ```

3. Create a `.env` file in the root directory and add your environment variables:
    ```env
    QBITTORRENT_USERNAME=your_qbittorrent_username
    QBITTORRENT_PASSWORD=your_qbittorrent_password
    QBITTORRENT_BASE_URL=http://your_qbittorrent_ip:port
    DISCORD_BOT_TOKEN=your_discord_bot_token
    ```

4. Create a `start.bat` file in the root directory to run your bot easily:
    ```batch
    @echo off
    set "QBITTORRENT_USERNAME=your_qbittorrent_username"
    set "QBITTORRENT_PASSWORD=your_qbittorrent_password"
    set "QBITTORRENT_BASE_URL=http://your_qbittorrent_ip:port"
    set "DISCORD_BOT_TOKEN=your_discord_bot_token"
    python bot.py
    pause
    ```

Replace the placeholders with your actual qBittorrent credentials, base URL, and Discord bot token.

## Running the Bot

1. Make sure your qBittorrent Web UI is running and accessible.
2. Double-click the `start.bat` file to run the bot.

## Commands

- `!search <query>` - Search for torrents on Nyaa.si.
- `!add <magnet>` - Add a torrent to qBittorrent by magnet link.
- `!recent_searches` - Show recent searches.
- `!recent_additions` - Show recent added torrents.
- `!setprefix <prefix>` - Set a custom command prefix.
- `!setfilter <min_size> <max_size>` - Set file size filter in MB.
- `!schedule <magnet> <time>` - Schedule a torrent download (format: YYYY-MM-DD HH:MM:SS).
- `!stats` - Show bot statistics.
- `!help_command` - Show this help message.
- `!test_qbittorrent` - Test connection to qBittorrent Web UI.

## Code Explanation

### Main Script

The main script initializes the bot, sets up the necessary intents, defines various commands, and handles interactions with Nyaa.si and qBittorrent.

### Functions

- `login_to_qbittorrent()`: Logs in to qBittorrent Web UI using the credentials provided in the environment variables.
- `search_nyaa(query, min_size=0, max_size=float('inf'))`: Searches for torrents on Nyaa.si based on the query and size filters, and parses the results.
- `parse_size(size_str)`: Parses the size string and converts it to bytes.
- `add_torrent(magnet)`: Adds a torrent to qBittorrent using the magnet link.

### TorrentMenu Class

The `TorrentMenu` class handles the interactive menu in Discord for browsing search results. It uses buttons for navigation and adding torrents.

### Commands

Various commands are defined using the `@bot.command()` decorator. These commands allow users to interact with the bot to search for torrents, add them to qBittorrent, view recent searches and additions, set filters, and schedule downloads.

---
