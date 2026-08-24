from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .models import MediaRequest, RequestState, Route, SearchResult
from .service import MediaService
from .utils import human_bytes, parse_schedule

if TYPE_CHECKING:
    from .app import MediaBot


LOGGER = logging.getLogger(__name__)
ROUTE_CHOICES = [
    app_commands.Choice(name="Automatic", value="auto"),
    app_commands.Choice(name="Movie", value="movie"),
    app_commands.Choice(name="TV show", value="show"),
    app_commands.Choice(name="Anime", value="anime"),
]


def as_route(value: str) -> Route:
    try:
        return Route(value.lower())
    except ValueError as error:
        raise commands.BadArgument("Route must be auto, movie, show, or anime") from error


def status_embed(request: MediaRequest) -> discord.Embed:
    colors = {
        RequestState.READY: discord.Color.green(),
        RequestState.ERROR: discord.Color.red(),
        RequestState.NEEDS_ATTENTION: discord.Color.orange(),
        RequestState.PAUSED: discord.Color.gold(),
        RequestState.REMOVED: discord.Color.dark_grey(),
    }
    embed = discord.Embed(
        title=request.title or "Resolving metadata",
        description=f"Request **#{request.id}**",
        color=colors.get(request.state, discord.Color.blurple()),
    )
    embed.add_field(name="State", value=request.state.value.replace("_", " ").title())
    embed.add_field(name="Route", value=request.route.value.title())
    embed.add_field(name="Requester", value=f"<@{request.requester_id}>")
    if request.state in {
        RequestState.QUEUED,
        RequestState.DOWNLOADING,
        RequestState.PAUSED,
    }:
        progress = max(0.0, min(1.0, request.progress))
        blocks = round(progress * 10)
        embed.add_field(
            name="Progress",
            value=f"`{'█' * blocks}{'░' * (10 - blocks)}` {progress:.1%}",
            inline=False,
        )
        embed.add_field(name="Speed", value=f"{human_bytes(request.download_speed)}/s")
        eta = (
            "Unknown"
            if request.eta <= 0 or request.eta >= 8_640_000
            else f"{request.eta // 60} min"
        )
        embed.add_field(name="ETA", value=eta)
    if request.scheduled_for:
        embed.add_field(name="Scheduled", value=request.scheduled_for, inline=False)
    if request.error:
        embed.add_field(name="Details", value=request.error[:1000], inline=False)
    if request.jellyfin_item_id:
        embed.add_field(name="Jellyfin", value="Visible in library", inline=False)
    embed.set_footer(text=f"Info hash {request.info_hash[:12]}…")
    return embed


class SearchView(discord.ui.View):
    def __init__(
        self,
        service: MediaService,
        results: list[SearchResult],
        requester_id: int,
        guild_id: int,
        channel_id: int,
    ):
        super().__init__(timeout=600)
        self.service = service
        self.results = results
        self.requester_id = requester_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.page = 0
        self.message: discord.Message | None = None

    def embed(self) -> discord.Embed:
        result = self.results[self.page]
        embed = discord.Embed(
            title=f"Search result {self.page + 1}/{len(self.results)}",
            description=result.title,
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Size", value=human_bytes(result.size))
        embed.add_field(name="Seeders", value=str(result.seeders))
        embed.add_field(name="Leechers", value=str(result.leechers))
        embed.add_field(name="Indexer", value=result.indexer)
        embed.add_field(name="Category", value=result.category)
        return embed

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = (self.page - 1) % len(self.results)
        await self._refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = (self.page + 1) % len(self.results)
        await self._refresh(interaction)

    async def _add(self, interaction: discord.Interaction, route: Route) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = self.results[self.page]
        try:
            request, created = await self.service.add_search_result(
                result,
                route,
                interaction.user.id,
                interaction.guild_id or self.guild_id,
                interaction.channel_id or self.channel_id,
            )
        except Exception as error:
            # Some providers redirect download endpoints to magnet URIs or fail
            # transiently. Always finish the interaction without logging URLs.
            LOGGER.error(
                "Search result add failed for %s from %s (%s)",
                result.result_id,
                result.indexer,
                type(error).__name__,
            )
            await interaction.followup.send(
                "I couldn't add that search result. Please try it again or choose another result.",
                ephemeral=True,
            )
            return
        if created:
            message = await interaction.channel.send(embed=status_embed(request))
            request = await self.service.database.update_request(request.id, message_id=message.id)
            await interaction.followup.send(f"Added as request #{request.id}.", ephemeral=True)
        else:
            if request.route != route:
                request = await self.service.change_route(request.id, route, interaction.user.id)
                await interaction.followup.send(
                    f"Request #{request.id} already existed; its route is now {route.value.title()}.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"That torrent already exists as request #{request.id}.", ephemeral=True
            )

    @discord.ui.button(label="Add auto", style=discord.ButtonStyle.success)
    async def add_auto(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._add(interaction, Route.AUTO)

    @discord.ui.button(label="Movie", style=discord.ButtonStyle.primary)
    async def add_movie(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._add(interaction, Route.MOVIE)

    @discord.ui.button(label="Show", style=discord.ButtonStyle.primary)
    async def add_show(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._add(interaction, Route.SHOW)

    @discord.ui.button(label="Anime", style=discord.ButtonStyle.primary)
    async def add_anime(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._add(interaction, Route.ANIME)


class DeleteModal(discord.ui.Modal):
    def __init__(self, service: MediaService, request_id: int, request_title: str):
        super().__init__(title="Confirm permanent deletion")
        self.service = service
        self.request_id = request_id
        self.request_title = request_title
        self.media_title = discord.ui.TextInput(
            label="Media title (leave unchanged)",
            default=request_title[:4000],
            max_length=4000,
        )
        self.confirmation = discord.ui.TextInput(
            label="Type DELETE to remove downloaded files",
            placeholder="DELETE",
            max_length=10,
        )
        self.add_item(self.media_title)
        self.add_item(self.confirmation)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (
            str(self.media_title) != self.request_title[:4000]
            or str(self.confirmation).strip() != "DELETE"
        ):
            await interaction.response.send_message(
                "Deletion cancelled: confirmation did not match.", ephemeral=True
            )
            return
        request = await self.service.control(
            self.request_id, "delete", interaction.user.id, delete_files=True
        )
        await interaction.response.send_message(
            f"Request #{request.id} and its downloaded data were removed.", ephemeral=True
        )


class RemoveView(discord.ui.View):
    def __init__(self, service: MediaService, request_id: int, request_title: str):
        super().__init__(timeout=120)
        self.service = service
        self.request_id = request_id
        self.request_title = request_title

    @discord.ui.button(label="Remove torrent only", style=discord.ButtonStyle.secondary)
    async def keep_files(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        request = await self.service.control(
            self.request_id, "delete", interaction.user.id, delete_files=False
        )
        await interaction.response.edit_message(
            content=f"Request #{request.id} was removed from qBittorrent; files were kept.",
            view=None,
        )

    @discord.ui.button(label="Delete torrent and files", style=discord.ButtonStyle.danger)
    async def delete_files(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            DeleteModal(self.service, self.request_id, self.request_title)
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Removal cancelled.", view=None)


class MediaCommands(commands.Cog):
    def __init__(self, bot: MediaBot):
        self.bot = bot
        self.service = bot.service

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage(
                "This bot is available only inside its configured server."
            )
        if (
            self.bot.settings.discord_guild_id
            and ctx.guild.id != self.bot.settings.discord_guild_id
        ):
            raise commands.CheckFailure("This Discord server is not configured for the bot.")
        return True

    async def _send_request(self, ctx: commands.Context, magnet: str, route: Route) -> None:
        assert ctx.guild and ctx.channel
        async with ctx.typing():
            request, created = await self.service.add_magnet(
                magnet, route, ctx.author.id, ctx.guild.id, ctx.channel.id
            )
        if not created:
            await ctx.send(
                f"That torrent already exists as request #{request.id}.",
                embed=status_embed(request),
            )
            return
        message = await ctx.send(embed=status_embed(request))
        await self.service.database.update_request(request.id, message_id=message.id)

    @commands.hybrid_command(name="add", description="Add a magnet link to qBittorrent")
    @app_commands.choices(route=ROUTE_CHOICES)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def add_command(self, ctx: commands.Context, magnet: str, route: str = "auto") -> None:
        """Usage: !add <magnet>"""
        await self._send_request(ctx, magnet, as_route(route))

    @commands.command(name="addmovie")
    async def add_movie(self, ctx: commands.Context, *, magnet: str) -> None:
        await self._send_request(ctx, magnet, Route.MOVIE)

    @commands.command(name="addshow")
    async def add_show(self, ctx: commands.Context, *, magnet: str) -> None:
        await self._send_request(ctx, magnet, Route.SHOW)

    @commands.command(name="addanime")
    async def add_anime(self, ctx: commands.Context, *, magnet: str) -> None:
        await self._send_request(ctx, magnet, Route.ANIME)

    @commands.hybrid_command(name="search", description="Search configured torrent indexers")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def search_command(self, ctx: commands.Context, *, query: str) -> None:
        async with ctx.typing():
            results = await self.service.search(query, ctx.author.id)
        if not results:
            await ctx.send("No results matched your search and size filters.")
            return
        assert ctx.guild and ctx.channel
        view = SearchView(self.service, results, ctx.author.id, ctx.guild.id, ctx.channel.id)
        view.message = await ctx.send(embed=view.embed(), view=view)

    @commands.hybrid_command(name="status", description="Show a media request")
    async def status_command(self, ctx: commands.Context, request_id: int) -> None:
        request = await self.service.database.get_request(request_id)
        await ctx.send(embed=status_embed(request))

    @commands.hybrid_command(name="downloads", description="List active media requests")
    async def downloads_command(self, ctx: commands.Context) -> None:
        active = await self.service.database.list_requests(
            states=[
                RequestState.SCHEDULED,
                RequestState.QUEUED,
                RequestState.DOWNLOADING,
                RequestState.PAUSED,
                RequestState.ORGANIZING,
                RequestState.SCANNING,
                RequestState.ERROR,
                RequestState.NEEDS_ATTENTION,
            ],
            limit=20,
        )
        if not active:
            await ctx.send("There are no active or attention-needed requests.")
            return
        lines = [
            f"`#{item.id}` **{item.state.value}** — {item.title[:70]} ({item.progress:.0%})"
            for item in active
        ]
        await ctx.send("\n".join(lines))

    async def _control(self, ctx: commands.Context, request_id: int, action: str) -> None:
        request = await self.service.control(request_id, action, ctx.author.id)
        await ctx.send(embed=status_embed(request))

    @commands.hybrid_command(name="pause")
    async def pause_command(self, ctx: commands.Context, request_id: int) -> None:
        await self._control(ctx, request_id, "pause")

    @commands.hybrid_command(name="resume")
    async def resume_command(self, ctx: commands.Context, request_id: int) -> None:
        await self._control(ctx, request_id, "resume")

    @commands.hybrid_command(name="retry")
    async def retry_command(self, ctx: commands.Context, request_id: int) -> None:
        await self._control(ctx, request_id, "retry")

    @commands.hybrid_command(name="route", description="Change a request's media route")
    @app_commands.choices(route=ROUTE_CHOICES)
    async def route_command(self, ctx: commands.Context, request_id: int, route: str) -> None:
        request = await self.service.change_route(request_id, as_route(route), ctx.author.id)
        await ctx.send(embed=status_embed(request))

    @commands.hybrid_command(
        name="remove", description="Remove a torrent, optionally deleting data"
    )
    async def remove_command(self, ctx: commands.Context, request_id: int) -> None:
        request = await self.service.database.get_request(request_id)
        await ctx.send(
            f"Remove request #{request.id}: **{request.title}**? Choose carefully.",
            view=RemoveView(self.service, request.id, request.title),
        )

    @commands.hybrid_command(name="schedule", description="Schedule a magnet addition")
    @app_commands.choices(route=ROUTE_CHOICES)
    async def schedule_command(
        self, ctx: commands.Context, magnet: str, *, when: str, route: str = "auto"
    ) -> None:
        scheduled = parse_schedule(when, self.bot.settings.timezone)
        assert ctx.guild and ctx.channel
        request, created = await self.service.schedule_magnet(
            magnet, as_route(route), scheduled, ctx.author.id, ctx.guild.id, ctx.channel.id
        )
        if not created:
            await ctx.send(f"That torrent already exists as request #{request.id}.")
            return
        message = await ctx.send(embed=status_embed(request))
        await self.service.database.update_request(request.id, message_id=message.id)

    @commands.hybrid_command(name="recent_searches")
    async def recent_searches(self, ctx: commands.Context) -> None:
        query = await self.service.database.recent_search(ctx.author.id)
        await ctx.send(f"Your recent search: {query}" if query else "No recent searches found.")

    @commands.hybrid_command(name="recent_additions")
    async def recent_additions(self, ctx: commands.Context) -> None:
        requests = await self.service.database.list_requests(limit=10)
        if not requests:
            await ctx.send("No recent additions found.")
            return
        await ctx.send(
            "\n".join(
                f"`#{item.id}` **{item.state.value}** — {item.title[:75]}" for item in requests
            )
        )

    @commands.hybrid_command(name="setfilter")
    async def set_filter(
        self, ctx: commands.Context, min_size_mb: float = 0.0, max_size_mb: float = 0.0
    ) -> None:
        if min_size_mb < 0 or max_size_mb < 0:
            raise commands.BadArgument("Sizes cannot be negative")
        maximum = int(max_size_mb * 1024**2) if max_size_mb else None
        minimum = int(min_size_mb * 1024**2)
        if maximum is not None and maximum < minimum:
            raise commands.BadArgument("Maximum size must be greater than minimum size")
        await self.service.database.set_preferences(ctx.author.id, minimum, maximum)
        await ctx.send(
            f"Filter saved: {human_bytes(minimum)} to "
            f"{human_bytes(maximum) if maximum else 'unlimited'}."
        )

    @commands.hybrid_command(name="setprefix")
    async def set_prefix(self, ctx: commands.Context, prefix: str) -> None:
        if not 1 <= len(prefix) <= 5 or any(character.isspace() for character in prefix):
            raise commands.BadArgument("Prefix must be 1–5 non-space characters")
        assert ctx.guild
        await self.service.database.set_prefix(ctx.guild.id, prefix)
        await ctx.send(f"Command prefix set to `{prefix}`. Slash commands are unchanged.")

    @commands.hybrid_command(name="stats")
    async def stats_command(self, ctx: commands.Context) -> None:
        stats = await self.service.database.stats()
        await ctx.send(
            "\n".join(
                f"**{key.replace('_', ' ').title()}:** {value}"
                for key, value in sorted(stats.items())
            )
        )

    @commands.hybrid_command(name="test_qbittorrent")
    async def test_qbittorrent(self, ctx: commands.Context) -> None:
        app, api = await self.service.qbit.version()
        prowlarr = (
            await self.service.prowlarr.health() if self.bot.settings.prowlarr_enabled else False
        )
        jellyfin = await self.service.jellyfin.health()
        await ctx.send(
            f"qBittorrent **{app}** / Web API **{api}** ✅\n"
            f"Prowlarr {'✅' if prowlarr else '⚠️'}\nJellyfin {'✅' if jellyfin else '⚠️'}"
        )

    @commands.hybrid_command(name="help_command")
    async def help_command(self, ctx: commands.Context) -> None:
        await ctx.send(
            "**Media Bot Commands**\n"
            "`!add <magnet>` • `!addmovie` • `!addshow` • `!addanime`\n"
            "`!search <query>` • `!downloads` • `!status <id>`\n"
            "`!pause <id>` • `!resume <id>` • `!retry <id>` • `!route <id> <route>`\n"
            "`!remove <id>` • `!schedule <magnet> <time>`\n"
            "`!recent_searches` • `!recent_additions` • `!setfilter` • `!stats`\n"
            "All primary commands also have `/` slash-command equivalents."
        )
