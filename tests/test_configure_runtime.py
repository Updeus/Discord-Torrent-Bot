from __future__ import annotations

from pathlib import Path
from typing import Any

from deploy import configure_runtime


def test_prowlarr_configures_general_indexers_idempotently(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config.xml"
    config.write_text("<Config><ApiKey>hidden</ApiKey></Config>", encoding="utf-8")
    created: list[str] = []

    def fake_request(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        payload: Any | None = None,
    ) -> Any:
        assert headers == {"X-Api-Key": "hidden"}
        if url.endswith("/indexer") and method == "GET":
            return [{"name": "Nyaa"}]
        if url.endswith("/indexer/schema"):
            return [
                {"definitionName": name, "name": display, "fields": []}
                for name, display in (
                    ("nyaa", "Nyaa"),
                    ("yts", "YTS"),
                    ("TorrentsCSV", "TorrentsCSV"),
                    ("torrentdownloads", "Torrent Downloads"),
                    ("thepiratebay", "The Pirate Bay"),
                )
            ]
        if url.endswith("/appprofile"):
            return [{"id": 7}]
        assert method == "POST"
        created.append(payload["name"])
        assert payload["appProfileId"] == 7
        assert payload["enableInteractiveSearch"] is True
        return {"id": len(created) + 1}

    monkeypatch.setattr(configure_runtime, "api_request", fake_request)
    updates: dict[str, str] = {}
    configure_runtime.configure_prowlarr("http://prowlarr", config, updates)

    assert created == ["YTS", "TorrentsCSV", "Torrent Downloads", "The Pirate Bay"]
    assert updates["PROWLARR_BASE_URL"] == "http://prowlarr"
    assert updates["PROWLARR_API_KEY"] == "hidden"
