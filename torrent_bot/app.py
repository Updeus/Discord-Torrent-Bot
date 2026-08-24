from __future__ import annotations

import logging
import sys

import aiohttp
import discord
from discord.ext import commands

from .clients import JellyfinClient, NyaaProvider, ProwlarrClient, QBittorrentClient
from .config import Settings
from .database import Database
from .discord_app import MediaCommands, status_embed
from .models import MediaRequest
from .service import MediaService

LOGGER = logging.getLogger(__name__)


async def dynamic_prefix(bot: MediaBot, message: discord.Message) -> str:
    if message.guild and hasattr(bot, "database"):
        return await bot.database.get_prefix(message.guild.id, bot.settings.command_prefix)
    return bot.settings.command_prefix


class MediaBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=dynamic_prefix, intents=intents, help_command=None)
        self.settings = settings
        self.database = Database(settings.database_path)
        self.http_session: aiohttp.ClientSession
        self.service: MediaService

    async def setup_hook(self) -> None:
        await self.database.connect()
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self.http_session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "Discord-Torrent-Bot/2.0"},
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        )
        qbit = QBittorrentClient(
            self.http_session,
            self.settings.qbit_base_url,
            self.settings.qbit_username,
            self.settings.qbit_password,
        )
        prowlarr = ProwlarrClient(
            self.http_session, self.settings.prowlarr_base_url, self.settings.prowlarr_api_key
        )
        nyaa = NyaaProvider(self.http_session, self.settings.nyaa_url)
        jellyfin = JellyfinClient(
            self.http_session, self.settings.jellyfin_base_url, self.settings.jellyfin_api_key
        )
        self.service = MediaService(self.settings, self.database, qbit, prowlarr, nyaa, jellyfin)
        self.service.notifier = self.update_status_message
        await self.add_cog(MediaCommands(self))
        await self.service.start()
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def update_status_message(self, request: MediaRequest) -> None:
        if not request.message_id:
            return
        channel = self.get_channel(request.channel_id)
        if channel is None:
            channel = await self.fetch_channel(request.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            message = await channel.fetch_message(request.message_id)
            await message.edit(embed=status_embed(request))
        except discord.NotFound:
            LOGGER.warning("Status message %s was deleted", request.message_id)

    async def close(self) -> None:
        if hasattr(self, "service"):
            await self.service.close()
        if hasattr(self, "http_session"):
            await self.http_session.close()
        await self.database.close()
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")
        LOGGER.info(
            "Connected Discord servers: %s",
            ", ".join(f"{guild.name} ({guild.id})" for guild in self.guilds) or "none",
        )

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, commands.CommandNotFound):
            await ctx.send("Command not found. Use `!help_command` or `/help_command`.")
        elif isinstance(original, commands.CommandOnCooldown):
            await ctx.send(f"Please wait {original.retry_after:.1f} seconds before trying again.")
        elif isinstance(original, commands.MissingRequiredArgument):
            await ctx.send(f"Missing `{original.param.name}`. Use `!help_command` for examples.")
        elif isinstance(
            original, (commands.BadArgument, ValueError, LookupError, commands.CheckFailure)
        ):
            await ctx.send(str(original))
        else:
            LOGGER.exception("Unhandled command error", exc_info=original)
            await ctx.send("The command failed. The error was recorded in the service log.")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def run() -> None:
    configure_logging()
    settings = Settings.load()
    bot = MediaBot(settings)
    bot.run(settings.discord_token, log_handler=None)
