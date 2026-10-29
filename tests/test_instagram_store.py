from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import threading
import time

from cryptography.fernet import Fernet
import httpx
import psycopg
import pytest

from social_mcp_hub.instagram import InstagramError, InstagramSettings, InstagramTokens


def test_database_refresh_serialized_and_persisted(monkeypatch):
    settings = InstagramSettings(database_url="postgresql://test", encryption_key=Fernet.generate_key().decode())
    provider = InstagramTokens(settings)
    state = {"encrypted": provider._encode({"access_token": "old", "user_id": "42",
                "expires_at": time.time() + 86400, "refreshed_at": time.time() - 59 * 86400})}
    lock = threading.Lock()
    queries, refreshes = [], []
    class Result:
        def fetchone(self):
            return (state["encrypted"],)
    class Connection:
        def execute(self, query, params=None):
            queries.append(query)
            assert "x_mcp.oauth_tokens" not in query
            if query.startswith("UPDATE"):
                state["encrypted"] = params[0]
            return Result()
    @contextmanager
    def connect(*args, **kwargs):
        with lock:
            yield Connection()
    monkeypatch.setattr("social_mcp_hub.instagram.psycopg.connect", connect)
    def refresh(request):
        refreshes.append(request)
        time.sleep(0.03)
        return httpx.Response(200, json={"access_token": "new", "expires_in": 5184000})
    transport = httpx.MockTransport(refresh)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(InstagramTokens(settings, transport)._load) for _ in range(2)]
        assert [f.result()["access_token"] for f in futures] == ["new", "new"]
    assert len(refreshes) == 1
    assert any("FOR UPDATE" in query for query in queries)
    assert InstagramTokens(settings, transport)._load()["access_token"] == "new"
    assert len(refreshes) == 1


def test_database_failure_and_wrong_key_do_not_leak(monkeypatch):
    settings = InstagramSettings(database_url="postgresql://user:private@host", encryption_key=Fernet.generate_key().decode())
    provider = InstagramTokens(settings)
    encrypted = provider._encode({"access_token": "secret", "user_id": "42"})
    with pytest.raises(InstagramError, match="decrypted"):
        InstagramTokens(replace(settings, encryption_key=Fernet.generate_key().decode()))._decode(encrypted)
    def fail(*args, **kwargs):
        raise psycopg.OperationalError("postgresql://user:private@host")
    monkeypatch.setattr("social_mcp_hub.instagram.psycopg.connect", fail)
    with pytest.raises(InstagramError) as exc:
        provider._load()
    assert "private" not in str(exc.value)
