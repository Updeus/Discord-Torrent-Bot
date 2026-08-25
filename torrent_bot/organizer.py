from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .models import Route

VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".ts", ".webm", ".wmv"}
SUBTITLE_EXTENSIONS = {".ass", ".idx", ".smi", ".srt", ".ssa", ".sub", ".vtt"}
EPISODE_PATTERN = re.compile(
    r"(?ix)(?:\bS(?P<s1>\d{1,2})[ ._-]*E(?P<e1>\d{1,3})\b|"
    r"\b(?P<s2>\d{1,2})x(?P<e2>\d{1,3})\b)"
)
SEASON_PATTERN = re.compile(r"(?i)\b(?:season[ ._-]*|S)(\d{1,2})\b")
NUMBERED_EPISODE_PATTERN = re.compile(
    r"(?ix)(?:\b(?:ep(?:isode)?)[ ._-]*(\d{1,3})\b|"
    r"(?:^|[ ._-])(\d{1,3})(?:v\d+)?(?:[ ._-]|$))"
)
MOVIE_PACK_PATTERN = re.compile(
    r"(?ix)\b(?:box[ ._-]*set|movie[ ._-]*collection|trilogy|quad(?:rilogy)?|"
    r"complete[ ._-]*\d+[ ._-]*movie)\b"
)
MOVIE_HINT_PATTERN = re.compile(r"(?i)\b(?:extras?|featurettes?|trailers?)\b")
RELEASE_PREFIX_PATTERN = re.compile(r"^\[[^]]+\]\s*")
TECHNICAL_SUFFIX_PATTERN = re.compile(
    r"(?ix)\s*(?:[\[(]\s*)?(?:\d{3,4}p|4k|web(?:[- .]?(?:dl|rip))?|"
    r"blu[- .]?ray|bdrip|bdremux|remux|hdr(?:10\+?)?|dv|dolby[ ._-]?vision|"
    r"hevc|av1|x26[45]|h\.?26[45]|10[ ._-]?bit|dual[ ._-]?audio|multi[ ._-]?audio|"
    r"multi[ ._-]?subs?|aac|opus|ddp?|eac3)\b.*$"
)
ORDINAL_SEASON_PATTERN = re.compile(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)[ ._-]+season\b")


@dataclass(slots=True)
class OrganizationResult:
    kind: str
    title: str
    destination: Path | None
    videos: int
    reason: str
    links: tuple[Path, ...] = ()


def video_files(path: Path) -> list[Path]:
    def is_sample(item: Path) -> bool:
        return any(
            part.lower() == "sample"
            or Path(part).stem.lower() == "sample"
            or "(sample)" in part.lower()
            for part in item.parts
        )

    if path.is_file():
        return [path] if path.suffix.lower() in VIDEO_EXTENSIONS else []
    return sorted(
        (
            item
            for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS and not is_sample(item)
        ),
        key=lambda item: str(item).lower(),
    )


def looks_like_numbered_pack(files: list[Path]) -> bool:
    numbered = sum(
        bool(EPISODE_PATTERN.search(item.name) or NUMBERED_EPISODE_PATTERN.search(item.stem))
        for item in files
    )
    return len(files) >= 3 and numbered >= max(2, len(files) // 2)


def classify(item: Path, files: list[Path], override: Route = Route.AUTO) -> tuple[str, str]:
    if override == Route.MOVIE:
        return "movie", "explicit movie route"
    if override in {Route.SHOW, Route.ANIME}:
        return "show", f"explicit {override.value} route"
    if len(files) == 1 and EPISODE_PATTERN.search(files[0].name):
        return "show", "isolated episode retained as requested"
    if item.is_file() or len(files) == 1:
        return "movie", "single video"
    explicit = sum(bool(EPISODE_PATTERN.search(file.name)) for file in files)
    if explicit >= 2:
        return "show", f"{explicit} explicit episode names"
    if MOVIE_PACK_PATTERN.search(item.name):
        return "movies", "movie collection keyword"
    if looks_like_numbered_pack(files):
        return "show", "sequential numbered episode pack"
    if MOVIE_HINT_PATTERN.search(item.name):
        return "movie", "movie with extras keyword"
    if len(files) >= 3:
        return "show", "multi-video series pack"
    return "ambiguous", "two videos without reliable episode/movie marker"


def safe_name(value: str) -> str:
    value = RELEASE_PREFIX_PATTERN.sub("", value).strip()
    value = re.sub(r"(?<=\w)[._]+(?=\w)", " ", value)
    value = TECHNICAL_SUFFIX_PATTERN.sub("", value).strip(" ._-")
    return re.sub(r"\s+", " ", value) or "Unknown"


def series_name(source: Path, files: list[Path] | None = None) -> str:
    value = safe_name(source.stem if source.is_file() else source.name)
    match = EPISODE_PATTERN.search(value)
    if match:
        value = value[: match.start()]
    elif files and len(files) == 1:
        filename = safe_name(files[0].stem)
        match = EPISODE_PATTERN.search(filename)
        if match:
            value = filename[: match.start()]
    value = re.sub(
        r"(?i)\s*[\[(]?\s*(?:complete[ ._-]*)?(?:season|series|S)\s*0?\d+.*$",
        "",
        value,
    )
    value = ORDINAL_SEASON_PATTERN.sub("", value)
    value = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip(" ._-") or safe_name(source.name)


def movie_name(value: str) -> str:
    value = safe_name(value)
    value = re.sub(r"^\d{1,2}[ ._-]+(?=[A-Za-z])", "", value)
    year = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", value)
    if year and year.start() > 0:
        title = value[: year.start()].strip(" ._-([{\t")
        if title:
            return f"{title} ({year.group(1)})"
    return value


def season_number(file: Path, source: Path) -> int:
    match = EPISODE_PATTERN.search(file.name)
    if match:
        return int(match.group("s1") or match.group("s2"))
    for candidate in (file.parent.name, source.name):
        match = SEASON_PATTERN.search(candidate) or ORDINAL_SEASON_PATTERN.search(candidate)
        if match:
            return int(match.group(1))
    return 1


def unique_destination(path: Path, source: Path) -> Path:
    if not path.exists():
        return path
    try:
        if os.path.samefile(path, source):
            return path
    except OSError:
        pass
    suffix = hashlib.sha1(str(source).encode()).hexdigest()[:8]
    return path.with_name(f"{path.stem} [{suffix}]{path.suffix}")


def hardlink(source: Path, destination: Path, apply: bool) -> Path:
    destination = unique_destination(destination, source)
    if apply:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            os.link(source, destination)
    return destination


def link_subtitles(video: Path, destination_dir: Path, apply: bool) -> list[Path]:
    return [
        hardlink(item, destination_dir / item.name, apply)
        for item in video.parent.glob(f"{video.stem}.*")
        if item.suffix.lower() in SUBTITLE_EXTENSIONS
    ]


def organize_item(
    item: Path,
    view_root: Path,
    *,
    route: Route = Route.AUTO,
    apply: bool = False,
    min_age_seconds: int = 0,
) -> OrganizationResult:
    files = video_files(item)
    if min_age_seconds:
        cutoff = time.time() - min_age_seconds
        files = [file for file in files if file.stat().st_mtime <= cutoff]
    if not files:
        return OrganizationResult(
            "unsupported", safe_name(item.name), None, 0, "no stable video files"
        )
    kind, reason = classify(item, files, route)
    if kind == "ambiguous":
        return OrganizationResult(kind, safe_name(item.name), None, len(files), reason)
    destinations: list[Path] = []
    if kind == "show":
        title = series_name(item, files)
        root = view_root / "Shows" / title
        for video in files:
            destination_dir = root / f"Season {season_number(video, item):02d}"
            destinations.append(hardlink(video, destination_dir / video.name, apply))
            destinations.extend(link_subtitles(video, destination_dir, apply))
        return OrganizationResult(kind, title, root, len(files), reason, tuple(destinations))
    if kind == "movies":
        for video in files:
            root = view_root / "Movies" / movie_name(video.stem)
            destinations.append(hardlink(video, root / video.name, apply))
            destinations.extend(link_subtitles(video, root, apply))
        return OrganizationResult(
            kind,
            movie_name(item.name),
            view_root / "Movies",
            len(files),
            reason,
            tuple(destinations),
        )
    title = movie_name(item.stem if item.is_file() else item.name)
    root = view_root / "Movies" / title
    main = max(files, key=lambda file: file.stat().st_size)
    for video in files:
        destination_dir = root if video == main else root / "Extras"
        destinations.append(hardlink(video, destination_dir / video.name, apply))
        destinations.extend(link_subtitles(video, destination_dir, apply))
    return OrganizationResult(kind, title, root, len(files), reason, tuple(destinations))


def build(
    source_root: Path, view_root: Path, apply: bool, min_age_seconds: int = 0
) -> dict[str, object]:
    records = []
    counts: dict[str, int] = {}
    expected: set[Path] = set()
    for item in sorted(source_root.iterdir(), key=lambda path: path.name.lower()):
        if (
            item.name.startswith(".")
            or item == view_root
            or (item.is_dir() and (item / ".ignore").exists())
        ):
            continue
        result = organize_item(item, view_root, apply=apply, min_age_seconds=min_age_seconds)
        if result.videos == 0:
            continue
        counts[result.kind] = counts.get(result.kind, 0) + 1
        expected.update(result.links)
        records.append(
            {
                "kind": result.kind,
                "source": item.name,
                "title": result.title,
                "videos": result.videos,
                "reason": result.reason,
                "destination": str(result.destination) if result.destination else None,
            }
        )
    pruned = 0
    if apply:
        managed_extensions = VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS
        for section_name in ("Movies", "Shows"):
            section = view_root / section_name
            if not section.exists():
                continue
            for existing in section.rglob("*"):
                if (
                    existing.is_file()
                    and existing.suffix.lower() in managed_extensions
                    and existing not in expected
                ):
                    existing.unlink()
                    pruned += 1
            for directory in sorted(
                (path for path in section.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
    manifest = {
        "source": str(source_root),
        "view": str(view_root),
        "counts": counts,
        "managed_links": len(expected),
        "pruned_links": pruned,
        "items": records,
    }
    if apply:
        view_root.mkdir(parents=True, exist_ok=True)
        (view_root / "organizer-manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("view", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--min-age-seconds", type=int, default=0)
    args = parser.parse_args()
    manifest = build(
        args.source.resolve(), args.view.resolve(), args.apply, max(0, args.min_age_seconds)
    )
    print(json.dumps(manifest["counts"], indent=2))
    print("Applied." if args.apply else "Dry run only; no files were changed.")


if __name__ == "__main__":
    main()
