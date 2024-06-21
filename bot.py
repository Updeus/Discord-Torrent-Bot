import discord
from discord.ext import commands, menus, tasks
import requests
from bs4 import BeautifulSoup
import logging
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
from discord.ext.commands import CommandNotFound, CommandOnCooldown, MissingRequiredArgument
import os

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

NYAA_URL = "https://nyaa.si/"
SEARCH_HISTORY = {}
ADDED_TORRENTS = {}
USER_PREFERENCES = {}

scheduler = AsyncIOScheduler()
scheduler.start()

logging.basicConfig(level=logging.INFO)

# Create a global session object
session = requests.Session()

def login_to_qbittorrent():
    base_url = os.getenv("QBITTORRENT_BASE_URL")
    login_url = f"{base_url}/api/v2/auth/login"
    login_payload = {
        'username': os.getenv("QBITTORRENT_USERNAME"),
        'password': os.getenv("QBITTORRENT_PASSWORD")
    }
    response = session.post(login_url, data=login_payload)
    return response.status_code == 200

def search_nyaa(query, min_size=0, max_size=float('inf')):
    try:
        search_url = f"{NYAA_URL}?f=0&c=0_0&q={query}&s=seeders&o=desc"
        response = requests.get(search_url)
        response.raise_for_status()  # Check for request errors
        soup = BeautifulSoup(response.content, "html.parser")

        torrents = []
        rows = soup.find_all("tr", class_="default")
        for row in rows:
            title_tag = row.find("a", class_="title")
            title = title_tag.text if title_tag else "N/A"
            magnet_tag = row.find("a", title="Magnet Link")
            magnet = magnet_tag["href"] if magnet_tag else "N/A"
            size_tag = row.find_all("td", class_="text-center")[1]
            size = size_tag.text if size_tag else "N/A"
            size_value = parse_size(size)
            if min_size <= size_value <= max_size:
                torrents.append((title, magnet, size))
            if len(torrents) == 5:
                break

        return torrents
    except requests.exceptions.RequestException as e:
        logging.error(f"Request error: {e}")
        return []
    except Exception as e:
        logging.error(f"Error parsing Nyaa.si response: {e}")
        return []

def parse_size(size_str):
    size_units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    size, unit = size_str.split()
    return float(size) * size_units[unit]

class TorrentMenu(menus.Menu):
    def __init__(self, data):
        super().__init__(timeout=60.0)
        self.data = data
        self.current_page = 0

    async def send_initial_message(self, ctx, channel):
        return await channel.send(embed=self.create_embed())

    def create_embed(self):
        embed = discord.Embed(title=f"Result {self.current_page + 1}/{len(self.data)}")
        title, magnet, size = self.data[self.current_page]
        embed.add_field(name="Title", value=title, inline=False)
        embed.add_field(name="Size", value=size, inline=True)
        embed.add_field(name="Magnet", value=magnet, inline=False)
        return embed

    @menus.button("\U000025C0")  # ⬅️
    async def on_left_arrow(self, payload):
        if self.current_page > 0:
            self.current_page -= 1
            await self.message.edit(embed=self.create_embed())

    @menus.button("\U000025B6")  # ➡️
    async def on_right_arrow(self, payload):
        if self.current_page < len(self.data) - 1:
            self.current_page += 1
            await self.message.edit(embed=self.create_embed())

    @menus.button("\U0001F4E5")  # 📥
    async def on_add_torrent(self, payload):
        title, magnet, size = self.data[self.current_page]
        response = add_torrent(magnet)
        if response.status_code == 200:
            await self.ctx.send(f"Torrent '{title}' added successfully.")
        else:
            await self.ctx.send(f"Failed to add torrent '{title}'. Status code: {response.status_code}")

def add_torrent(magnet):
    base_url = os.getenv("QBITTORRENT_BASE_URL")
    add_url = f"{base_url}/api/v2/torrents/add"
    payload = {"urls": magnet}
    response = session.post(add_url, data=payload)
    return response

@bot.command()
@commands.cooldown(1, 10, commands.BucketType.user)
async def search(ctx, *, query: str):
    user_prefs = USER_PREFERENCES.get(ctx.author.id, {})
    min_size = user_prefs.get("min_size", 0)
    max_size = user_prefs.get("max_size", float('inf'))

    torrents = search_nyaa(query, min_size, max_size)
    if not torrents:
        await ctx.send("No results found.")
        return

    SEARCH_HISTORY[ctx.author.id] = query
    menu = TorrentMenu(torrents)
    await menu.start(ctx)

@bot.command()
@commands.cooldown(1, 10, commands.BucketType.user)
async def add(ctx, *, magnet: str):
    response = add_torrent(magnet)
    if response.status_code == 200:
        await ctx.send("Torrent added successfully.")
    else:
        await ctx.send(f"Failed to add torrent. Status code: {response.status_code}")

@bot.command()
async def recent_searches(ctx):
    query = SEARCH_HISTORY.get(ctx.author.id, "No recent searches found.")
    await ctx.send(f"Your recent search: {query}")

@bot.command()
async def recent_additions(ctx):
    additions = ADDED_TORRENTS.get(ctx.author.id, [])
    if not additions:
        await ctx.send("No recent additions found.")
        return

    embed = discord.Embed(title="Recent Additions")
    for title, magnet, size in additions:
        embed.add_field(name=title, value=f"Size: {size}\nMagnet: {magnet}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def setprefix(ctx, *, prefix: str):
    bot.command_prefix = prefix
    await ctx.send(f"Command prefix set to {prefix}")

@bot.command()
async def setfilter(ctx, min_size: float = 0, max_size: float = float('inf')):
    USER_PREFERENCES[ctx.author.id] = {"min_size": min_size, "max_size": max_size}
    await ctx.send(f"Filter set. Min size: {min_size} MB, Max size: {max_size} MB")

@bot.command()
async def schedule(ctx, magnet: str, time: str):
    download_time = datetime.strptime(time, "%Y-%m-%d %H:%M:%S")
    scheduler.add_job(add_torrent, 'date', run_date=download_time, args=[magnet])
    await ctx.send(f"Scheduled download for {download_time}")

@bot.command()
async def stats(ctx):
    stats_message = f"Total searches: {len(SEARCH_HISTORY)}\nTotal added torrents: {sum(len(v) for v in ADDED_TORRENTS.values())}"
    await ctx.send(stats_message)

@bot.command()
async def help_command(ctx):
    help_message = """
    **Torrent Bot Commands**
    `!search <query>` - Search for torrents
    `!add <magnet>` - Add a torrent by magnet link
    `!recent_searches` - Show recent searches
    `!recent_additions` - Show recent added torrents
    `!setprefix <prefix>` - Set a custom command prefix
    `!setfilter <min_size> <max_size>` - Set file size filter in MB
    `!schedule <magnet> <time>` - Schedule a torrent download (format: YYYY-MM-DD HH:MM:SS)
    `!stats` - Show bot statistics
    `!help_command` - Show this help message
    """
    await ctx.send(help_message)

@bot.command()
async def test_qbittorrent(ctx):
    if login_to_qbittorrent():
        base_url = os.getenv("QBITTORRENT_BASE_URL")
        info_url = f"{base_url}/api/v2/torrents/info"
        try:
            response = session.get(info_url)
            if response.status_code == 200:
                await ctx.send("Successfully connected to qBittorrent WebUI.")
            else:
                await ctx.send(f"Failed to connect to qBittorrent WebUI. Status code: {response.status_code}")
        except requests.exceptions.RequestException as e:
            await ctx.send(f"Error connecting to qBittorrent WebUI: {e}")
    else:
        await ctx.send("Failed to log in to qBittorrent WebUI.")

@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user}!')

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, CommandNotFound):
        await ctx.send("Command not found. Use `!help_command` to see the list of available commands.")
    elif isinstance(error, CommandOnCooldown):
        await ctx.send(f"Command is on cooldown. Try again in {round(error.retry_after, 2)} seconds.")
    elif isinstance(error, MissingRequiredArgument):
        await ctx.send("Missing required argument. Use `!help_command` to see the correct usage.")
    else:
        await ctx.send("An error occurred while processing the command.")

bot.run(os.getenv('DISCORD_BOT_TOKEN'))
