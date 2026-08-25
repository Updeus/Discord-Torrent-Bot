from torrent_bot.discord_app import status_embed
from torrent_bot.models import MediaRequest, RequestState, Route


def request_with(*, poster_url: str | None) -> MediaRequest:
    return MediaRequest(
        id=12,
        info_hash="ab" * 20,
        title="Hamnet",
        route=Route.MOVIE,
        state=RequestState.DOWNLOADING,
        requester_id=1,
        guild_id=2,
        channel_id=3,
        progress=0.5,
        download_speed=1024,
        eta=120,
        poster_url=poster_url,
    )


def test_status_embed_omits_missing_poster() -> None:
    assert status_embed(request_with(poster_url=None)).thumbnail.url is None


def test_status_embed_shows_real_poster() -> None:
    poster = "https://image.tmdb.org/t/p/original/poster.jpg"
    assert status_embed(request_with(poster_url=poster)).thumbnail.url == poster
