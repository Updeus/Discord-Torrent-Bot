from datetime import datetime

import pytest

from torrent_bot.utils import magnet_info_hash, parse_schedule, parse_size, redact_url

HEX_HASH = "0123456789abcdef0123456789abcdef01234567"


def test_magnet_hex_hash() -> None:
    magnet = f"magnet:?xt=urn:btih:{HEX_HASH.upper()}&dn=Example"
    assert magnet_info_hash(magnet) == HEX_HASH


def test_magnet_base32_hash() -> None:
    assert magnet_info_hash("magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA") == "00" * 20


def test_magnet_v2_hash() -> None:
    value = "ab" * 32
    assert magnet_info_hash(f"magnet:?xt=urn:btmh:1220{value}") == value


@pytest.mark.parametrize("value", ["https://example.com", "magnet:?dn=no-hash", "garbage"])
def test_invalid_magnet(value: str) -> None:
    with pytest.raises(ValueError):
        magnet_info_hash(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1 KiB", 1024), ("1.5 MiB", 1572864), ("2 GB", 2_000_000_000)],
)
def test_parse_size(value: str, expected: int) -> None:
    assert parse_size(value) == expected


def test_redact_magnet_and_api_keys() -> None:
    assert "dn=" not in redact_url(f"magnet:?xt=urn:btih:{HEX_HASH}&dn=Secret")
    assert "secret" not in redact_url("https://host/x?apikey=secret").lower()


def test_schedule_formats() -> None:
    legacy = parse_schedule("2026-08-24 20:00:00", "America/Port_of_Spain")
    iso = parse_schedule("2026-08-24T20:00:00-04:00", "UTC")
    assert legacy.tzinfo is not None
    assert iso == datetime.fromisoformat("2026-08-24T20:00:00-04:00")
