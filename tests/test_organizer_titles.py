from pathlib import Path

from torrent_bot.organizer import series_name


def test_series_name_preserves_balanced_alternate_title() -> None:
    source = Path(
        "[Judas] Witch Hat Atelier (Tongari Boushi no Atelier) (Season 01) [1080p][HEVC x265 10bit]"
    )

    assert series_name(source) == "Witch Hat Atelier (Tongari Boushi no Atelier)"
