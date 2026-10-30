"""Secret stores. The OS keyring is never touched: a backend is installed per test."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import keyring
import keyring.backend
import keyring.errors
import pytest

from jmap.cli.secrets import SERVICE, KeyringStore, KeyringUnavailableError, MemoryStore

if TYPE_CHECKING:
    from collections.abc import Iterator


class DictKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[service, username] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.values:
            raise keyring.errors.PasswordDeleteError(username)
        del self.values[service, username]


class NoKeyring(keyring.backend.KeyringBackend):
    priority = 0

    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.NoKeyringError("no backend")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.NoKeyringError("no backend")

    def delete_password(self, service: str, username: str) -> None:
        raise keyring.errors.InitError("locked")


@pytest.fixture
def backend() -> Iterator[DictKeyring]:
    previous = keyring.get_keyring()
    installed = DictKeyring()
    keyring.set_keyring(installed)
    yield installed
    keyring.set_keyring(previous)


@pytest.fixture
def broken() -> Iterator[None]:
    previous = keyring.get_keyring()
    keyring.set_keyring(NoKeyring())  # type: ignore[no-untyped-call]
    yield
    keyring.set_keyring(previous)


class TestKeyringStore:
    def test_round_trip_under_the_service_name(self, backend):
        store = KeyringStore()
        assert store.get("work", "password") is None
        store.set("work", "password", "pw")
        assert store.get("work", "password") == "pw"
        assert backend.values == {(SERVICE, "work:password"): "pw"}
        store.delete("work", "password")
        assert backend.values == {}

    def test_deleting_nothing_is_fine(self, backend):
        KeyringStore().delete("work", "token")

    @pytest.mark.usefixtures("broken")
    def test_no_backend_is_one_error_type(self):
        store = KeyringStore()
        with pytest.raises(KeyringUnavailableError, match="no usable keyring"):
            store.get("work", "password")
        with pytest.raises(KeyringUnavailableError):
            store.set("work", "password", "pw")
        with pytest.raises(KeyringUnavailableError):
            store.delete("work", "password")

    def test_keyring_not_installed(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "keyring", None)
        with pytest.raises(KeyringUnavailableError) as caught:
            KeyringStore().get("work", "password")
        assert caught.value.hint is not None
        assert "JMAP_PASSWORD" in caught.value.hint


def test_memory_store():
    store = MemoryStore()
    store.set("a", "token", "t")
    assert store.get("a", "token") == "t"
    store.delete("a", "token")
    store.delete("a", "token")
    assert store.get("a", "token") is None
