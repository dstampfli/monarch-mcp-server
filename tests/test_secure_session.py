"""Tests for keyring backend detection and secure session storage."""

import sys
import types
from unittest.mock import MagicMock

import pytest

from monarch_mcp_server import secure_session as ss_module
from monarch_mcp_server.secure_session import _keyring_available


class _FakeKeyring:
    """Minimal stand-in for the `keyring` module used by detection tests."""

    def __init__(
        self,
        *,
        set_raises=None,
        get_returns=None,
        get_raises=None,
        delete_raises=None,
    ):
        self._set_raises = set_raises
        self._get_returns = get_returns
        self._get_raises = get_raises
        self._delete_raises = delete_raises
        self.set_calls = []
        self.get_calls = []
        self.delete_calls = []

    def set_password(self, service, username, value):
        self.set_calls.append((service, username, value))
        if self._set_raises:
            raise self._set_raises

    def get_password(self, service, username):
        self.get_calls.append((service, username))
        if self._get_raises:
            raise self._get_raises
        return self._get_returns

    def delete_password(self, service, username):
        self.delete_calls.append((service, username))
        if self._delete_raises:
            raise self._delete_raises


@pytest.fixture
def install_fake_keyring(monkeypatch):
    """Replace the importable `keyring` module with a controllable fake."""

    def _install(fake):
        module = types.ModuleType("keyring")
        module.set_password = fake.set_password
        module.get_password = fake.get_password
        module.delete_password = fake.delete_password
        monkeypatch.setitem(sys.modules, "keyring", module)
        return fake

    return _install


class TestKeyringAvailable:
    def test_returns_true_when_probe_round_trips(self, install_fake_keyring):
        """A real backend (set + get returns same value + delete) is accepted."""
        fake = install_fake_keyring(_FakeKeyring(get_returns="1"))
        assert _keyring_available() is True
        assert len(fake.set_calls) == 1
        assert len(fake.get_calls) == 1
        assert len(fake.delete_calls) == 1

    def test_macos_keychain_class_name_collision_is_handled(
        self, install_fake_keyring
    ):
        """The macOS Keychain and fail backends share the class name `Keyring`.

        Previously this caused real macOS keyrings to be rejected by name and
        tokens to be written to a plaintext file. The probe roundtrip ignores
        class names entirely and only trusts what the backend can actually do.
        """
        fake = install_fake_keyring(_FakeKeyring(get_returns="1"))
        # Simulate the macOS Keychain class name to prove name has no effect.
        fake.__class__.__name__ = "Keyring"
        assert _keyring_available() is True

    def test_returns_false_when_set_raises(self, install_fake_keyring):
        """The fail backend raises on set_password — we must NOT trust it."""
        install_fake_keyring(_FakeKeyring(set_raises=RuntimeError("no backend")))
        assert _keyring_available() is False

    def test_returns_false_when_get_returns_none(self, install_fake_keyring):
        """A backend that silently drops writes is not safe to use."""
        install_fake_keyring(_FakeKeyring(get_returns=None))
        assert _keyring_available() is False

    def test_returns_false_when_get_returns_wrong_value(self, install_fake_keyring):
        """A backend that corrupts the round-trip is not safe to use."""
        install_fake_keyring(_FakeKeyring(get_returns="not-the-probe-value"))
        assert _keyring_available() is False

    def test_returns_false_when_get_raises(self, install_fake_keyring):
        install_fake_keyring(
            _FakeKeyring(set_raises=None, get_raises=RuntimeError("read failed"))
        )
        assert _keyring_available() is False

    def test_returns_false_when_delete_raises(self, install_fake_keyring):
        """Delete failure means cleanup is broken; don't trust the backend."""
        install_fake_keyring(
            _FakeKeyring(get_returns="1", delete_raises=RuntimeError("rm failed"))
        )
        assert _keyring_available() is False

    def test_returns_false_when_keyring_not_installed(self, monkeypatch):
        """If the keyring package is absent, treat as unavailable, don't crash."""

        real_import = __builtins__["__import__"] if isinstance(
            __builtins__, dict
        ) else __builtins__.__import__

        def fake_import(name, *args, **kwargs):
            if name == "keyring":
                raise ImportError("no keyring installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)
        assert _keyring_available() is False

    def test_probe_uses_dedicated_username(self, install_fake_keyring):
        """The probe must not clobber the real token username."""
        fake = install_fake_keyring(_FakeKeyring(get_returns="1"))
        _keyring_available()
        for _service, username, _value in fake.set_calls:
            assert username != ss_module.KEYRING_USERNAME
        for _service, username in fake.get_calls:
            assert username != ss_module.KEYRING_USERNAME
