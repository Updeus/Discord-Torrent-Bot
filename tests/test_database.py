from pathlib import Path

import pytest

from torrent_bot.database import Database
from torrent_bot.models import RequestState, Route, SearchResult


@pytest.mark.asyncio
async def test_request_persistence_and_duplicate_hash(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    database = Database(path)
    await database.connect()
    request = await database.create_request(
        info_hash="ab" * 20,
        title="Test",
        route=Route.AUTO,
        state=RequestState.QUEUED,
        requester_id=1,
        guild_id=2,
        channel_id=3,
        source_uri="magnet:?xt=urn:btih:" + "ab" * 20,
    )
    await database.update_request(
        request.id, state=RequestState.DOWNLOADING, progress=0.5, qbit_name="Resolved"
    )
    await database.close()

    reopened = Database(path)
    await reopened.connect()
    restored = await reopened.get_by_hash("ab" * 20)
    assert restored is not None
    assert restored.state == RequestState.DOWNLOADING
    assert restored.progress == 0.5
    assert restored.source_uri.startswith("magnet:")
    await reopened.close()


@pytest.mark.asyncio
async def test_preferences_prefix_search_and_audit(tmp_path: Path) -> None:
    database = Database(tmp_path / "bot.db")
    await database.connect()
    await database.set_preferences(10, 100, 200)
    await database.set_prefix(20, "?")
    result = SearchResult("id", "Title", 150, 5, 1, "Indexer", "Anime", magnet="magnet")
    await database.save_search(10, "query", [result])
    await database.audit(10, "test", None, safe=True)
    assert await database.get_preferences(10) == (100, 200)
    assert await database.get_prefix(20, "!") == "?"
    assert await database.recent_search(10) == "query"
    stats = await database.stats()
    assert stats["searches"] == 1
    await database.close()
