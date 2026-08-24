from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

MAGNET_HASH = re.compile(r"^[0-9a-fA-F]{40}$")
BASE32_HASH = re.compile(r"^[A-Z2-7]{32}$", re.IGNORECASE)
V2_HASH = re.compile(r"^[0-9a-fA-F]{64}$")


def magnet_info_hash(magnet: str) -> str:
    parsed = urlparse(magnet.strip())
    if parsed.scheme.lower() != "magnet":
        raise ValueError("A magnet link must start with magnet:?")
    xt_values = parse_qs(parsed.query).get("xt", [])
    for xt in xt_values:
        lower = xt.lower()
        if lower.startswith("urn:btih:"):
            value = xt.rsplit(":", 1)[-1]
            if MAGNET_HASH.fullmatch(value):
                return value.lower()
            if BASE32_HASH.fullmatch(value):
                return base64.b32decode(value.upper()).hex()
        if lower.startswith("urn:btmh:1220"):
            value = xt[len("urn:btmh:1220") :]
            if V2_HASH.fullmatch(value):
                return value.lower()
    raise ValueError("Magnet link does not contain a supported BitTorrent info hash")


def redact_url(value: str) -> str:
    if value.lower().startswith("magnet:"):
        try:
            return f"magnet:?xt=urn:btih:{magnet_info_hash(value)[:12]}…"
        except ValueError:
            return "magnet:[invalid]"
    return re.sub(r"(?i)(apikey|api_key|token|passkey)=([^&\s]+)", r"\1=[redacted]", value)


def parse_size(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?i?B)\s*", value, re.I)
    if not match:
        raise ValueError(f"Invalid size: {value}")
    units = {
        "B": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KIB": 1024,
        "MIB": 1024**2,
        "GIB": 1024**3,
        "TIB": 1024**4,
    }
    return int(float(match.group(1)) * units[match.group(2).upper()])


def human_bytes(value: int) -> str:
    amount = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TiB"


def parse_schedule(value: str, timezone: str) -> datetime:
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


def stable_result_id(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:20]
