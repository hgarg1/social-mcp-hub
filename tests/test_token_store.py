from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
import threading
import time

from cryptography.fernet import Fernet
import httpx
import pytest

from x_account_mcp.config import Settings
from x_account_mcp.errors import XError
from x_account_mcp.token_store import cipher, database_access_token, decrypt_tokens


def test_encrypted_token_roundtrip_and_wrong_key():
    settings = Settings(token_encryption_key=Fernet.generate_key().decode())
    tokens = {"access_token": "private-access", "refresh_token": "private-refresh"}
    encrypted = cipher(settings).encrypt(json.dumps(tokens).encode()).decode()
    assert "private-access" not in encrypted and "private-refresh" not in encrypted
    assert decrypt_tokens(settings, encrypted) == tokens
    with pytest.raises(XError, match="cannot be decrypted"):
        decrypt_tokens(replace(settings, token_encryption_key=Fernet.generate_key().decode()), encrypted)


def test_concurrent_refresh_persists_once_and_survives_new_provider(monkeypatch):
    settings = Settings(database_url="postgresql://test", client_id="client", token_encryption_key=Fernet.generate_key().decode())
    state = {"encrypted": cipher(settings).encrypt(json.dumps({"access_token": "old", "refresh_token": "old-refresh", "expires_at": 0}).encode()).decode()}
    lock = threading.Lock()
    refreshes = []
    class Result:
        def fetchone(self):
            return (state["encrypted"],)
    class Connection:
        def execute(self, query, params=None):
            if query.startswith("UPDATE"):
                state["encrypted"] = params[0]
            return Result()
    @contextmanager
    def connect(*args, **kwargs):
        with lock:
            yield Connection()
    monkeypatch.setattr("x_account_mcp.token_store.psycopg.connect", connect)
    def refresh(request):
        refreshes.append(request)
        time.sleep(0.05)
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated", "expires_in": 7200})
    transport = httpx.MockTransport(refresh)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(database_access_token, settings, transport) for _ in range(2)]
        assert [future.result() for future in futures] == ["new", "new"]
    assert len(refreshes) == 1
    assert decrypt_tokens(settings, state["encrypted"])["refresh_token"] == "rotated"
    assert database_access_token(settings, transport) == "new"
    assert len(refreshes) == 1


def test_database_errors_do_not_leak_credentials(monkeypatch):
    import psycopg
    def connect(*args, **kwargs):
        raise psycopg.OperationalError("postgres://user:very-secret@host")
    monkeypatch.setattr("x_account_mcp.token_store.psycopg.connect", connect)
    with pytest.raises(XError) as error:
        database_access_token(Settings(database_url="postgres://user:very-secret@host"))
    assert "very-secret" not in str(error.value)
