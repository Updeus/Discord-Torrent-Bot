from __future__ import annotations

import os
from pathlib import Path

import pytest

from torrent_bot.models import Route
from torrent_bot.organizer import (
    build,
    classify,
    movie_name,
    organize_item,
    safe_name,
    season_number,
    series_name,
    video_files,
)


def media(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_single_episode_is_a_show(tmp_path: Path) -> None:
    episode = media(tmp_path / "Example.Show.S02E03.1080p.mkv")
    kind, reason = classify(episode, [episode])
    assert kind == "show"
    assert "retained" in reason
    assert series_name(episode, [episode]) == "Example Show"
    assert season_number(episode, episode) == 2


def test_explicit_route_wins(tmp_path: Path) -> None:
    video = media(tmp_path / "Maybe.S01E01.mkv")
    assert classify(video, [video], Route.MOVIE)[0] == "movie"
    assert classify(video, [video], Route.ANIME)[0] == "show"


def test_season_pack_and_samples(tmp_path: Path) -> None:
    root = tmp_path / "Good Show Season 1"
    files = [media(root / f"Good.Show.S01E{episode:02}.mkv") for episode in range(1, 4)]
    media(root / "Sample" / "sample.mkv")
    discovered = video_files(root)
    assert discovered == files
    assert classify(root, discovered)[0] == "show"


def test_ambiguous_two_video_folder(tmp_path: Path) -> None:
    root = tmp_path / "Unknown"
    files = [media(root / "part-a.mkv"), media(root / "part-b.mkv")]
    assert classify(root, files)[0] == "ambiguous"


def test_movie_with_extras_uses_largest_as_main(tmp_path: Path) -> None:
    source = tmp_path / "Example Movie (2024) + Extras"
    main = media(source / "Example.Movie.2024.mkv", 100)
    extra = media(source / "making-of.mkv", 10)
    subtitle = media(source / "Example.Movie.2024.en.srt", 3)
    view = tmp_path / "view"
    result = organize_item(source, view, apply=True)
    assert result.kind == "movie"
    assert result.destination is not None
    main_link = result.destination / main.name
    extra_link = result.destination / "Extras" / extra.name
    assert main_link.exists() and extra_link.exists()
    assert os.path.samefile(main, main_link)
    assert os.path.samefile(extra, extra_link)
    assert (result.destination / subtitle.name).exists()
    assert main.exists() and extra.exists()


def test_single_episode_hardlink_layout(tmp_path: Path) -> None:
    episode = media(tmp_path / "My.Show.S03E07.1080p.mkv")
    result = organize_item(episode, tmp_path / "view", apply=True)
    expected = tmp_path / "view" / "Shows" / "My Show" / "Season 03" / episode.name
    assert result.kind == "show"
    assert expected.exists()
    assert os.path.samefile(episode, expected)


def test_full_build_prunes_stale_managed_links(tmp_path: Path) -> None:
    source = tmp_path / "source"
    view = tmp_path / "view"
    media(source / "Current Movie (2024).mkv")
    stale = media(view / "Movies" / "Removed Movie" / "removed.mkv")
    build(source, view, apply=True)
    assert not stale.exists()
    assert (view / "Movies" / "Current Movie (2024)" / "Current Movie (2024).mkv").exists()


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("[Group] Example.Show.S01.1080p.HEVC", "Example Show S01"),
        ("Example_Movie_2024_2160p_HDR", "Example Movie 2024"),
    ],
)
def test_release_name_cleanup(raw: str, clean: str) -> None:
    assert safe_name(raw) == clean


def test_movie_name_keeps_year() -> None:
    assert movie_name("Example.Movie.2024.1080p") == "Example Movie (2024)"
