"""The configuration file, and resolving one account from file, environment and flags."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from jmap.cli.config import (
    AccountConfig,
    Config,
    Defaults,
    Overrides,
    load_config,
    resolve_account,
    save_config,
)
from jmap.cli.errors import ConfigError


class TestLoad:
    def test_a_missing_file_is_an_empty_config(self, tmp_path):
        assert load_config(tmp_path / "nope.toml") == Config()

    def test_bad_toml_names_the_file(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text("this is = = not toml")
        with pytest.raises(ConfigError, match=r"c\.toml"):
            load_config(path)

    def test_an_unknown_key_says_where(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text('[accounts.work]\nurl = "x"\ncolour = "red"\n')
        with pytest.raises(ConfigError, match=r"accounts\.work\.colour"):
            load_config(path)

    def test_an_account_that_is_not_a_table(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text('[accounts]\nwork = "x"\n')
        with pytest.raises(ConfigError, match=r"accounts\.work"):
            load_config(path)

    @pytest.mark.parametrize("key", ["password", "token"])
    def test_a_secret_in_the_file_is_refused(self, tmp_path, key):
        path = tmp_path / "c.toml"
        path.write_text(f'[accounts.work]\n{key} = "hunter2"\n')
        with pytest.raises(ConfigError, match="jmap account login") as caught:
            load_config(path)
        assert "Value error" not in str(caught.value)


class TestSave:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "sub" / "config.toml"
        config = Config(
            default_account="work",
            defaults=Defaults(output="json"),
            accounts={"work": AccountConfig(url="https://x", trusted_srv_targets=["h"])},
        )
        save_config(config, path)
        assert load_config(path) == config
        # Defaults are left out, so the file stays short.
        assert "send_move_to_sent" not in path.read_text()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
    def test_only_the_owner_can_read_it(self, tmp_path):
        path = tmp_path / "config.toml"
        save_config(Config(), path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_a_failed_write_leaves_no_temporary_file(self, tmp_path, monkeypatch):
        def broken(src: str, dst: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", broken)
        with pytest.raises(OSError, match="disk full"):
            save_config(Config(), tmp_path / "config.toml")
        assert os.listdir(tmp_path) == []


def configured(**accounts: AccountConfig) -> Config:
    return Config(accounts=accounts)


class TestResolve:
    def test_the_flag_beats_the_environment_and_the_default(self):
        config = Config(
            default_account="a",
            accounts={"a": AccountConfig(url="u1"), "b": AccountConfig(url="u2")},
        )
        environ = {"JMAP_ACCOUNT": "a"}
        assert resolve_account(config, Overrides(account="b"), environ).url == "u2"
        assert resolve_account(config, Overrides(), {"JMAP_ACCOUNT": "b"}).url == "u2"
        assert resolve_account(config, Overrides(), {}).url == "u1"

    def test_a_lone_account_needs_no_default(self):
        resolved = resolve_account(configured(only=AccountConfig(url="u")), Overrides(), {})
        assert resolved.name == "only"

    def test_the_username_defaults_to_the_address(self):
        config = configured(a=AccountConfig(address="alice@example.com"))
        assert resolve_account(config, Overrides(), {}).username == "alice@example.com"

    def test_timeout_falls_back_to_the_defaults(self):
        config = Config(
            defaults=Defaults(timeout=5),
            accounts={"a": AccountConfig(), "b": AccountConfig(timeout=9)},
        )
        assert resolve_account(config, Overrides(account="a"), {}).timeout == 5
        assert resolve_account(config, Overrides(account="b"), {}).timeout == 9

    def test_environment_and_flags_override_the_file(self):
        config = configured(a=AccountConfig(url="file", username="u", account_id="x"))
        environ = {
            "JMAP_URL": "env",
            "JMAP_USER": "v",
            "JMAP_EXPERIMENTAL": "yes",
            "JMAP_INSECURE": "1",
        }
        resolved = resolve_account(config, Overrides(account_id="y"), environ)
        assert (resolved.url, resolved.username, resolved.account_id) == ("env", "v", "y")
        assert resolved.experimental
        assert resolved.insecure

    def test_read_only_from_any_source_and_never_switched_off(self):
        def read_only(config: Config, overrides: Overrides, environ: dict[str, str]) -> bool:
            return resolve_account(config, overrides, environ).read_only

        plain = configured(a=AccountConfig())
        assert not read_only(plain, Overrides(), {})
        assert read_only(plain, Overrides(read_only=True), {})
        assert read_only(plain, Overrides(), {"JMAP_READ_ONLY": "1"})
        assert read_only(configured(a=AccountConfig(read_only=True)), Overrides(), {})
        everywhere = Config(defaults=Defaults(read_only=True), accounts={"a": AccountConfig()})
        assert read_only(everywhere, Overrides(), {"JMAP_READ_ONLY": "0"})
        assert read_only(Config(defaults=Defaults(read_only=True)), Overrides(), {"JMAP_URL": "u"})

    def test_flags_alone_switch_on(self):
        resolved = resolve_account(
            configured(a=AccountConfig()), Overrides(experimental=True, insecure=True), {}
        )
        assert resolved.experimental
        assert resolved.insecure

    def test_jmap_url_alone_makes_an_account(self):
        resolved = resolve_account(Config(), Overrides(), {"JMAP_URL": "https://x"})
        assert resolved.name == "env"
        assert resolved.synthetic
        assert resolved.auth == "password"

    def test_a_token_alone_means_bearer(self):
        environ = {"JMAP_URL": "https://x", "JMAP_TOKEN": "t"}
        assert resolve_account(Config(), Overrides(), environ).auth == "token"
        environ["JMAP_PASSWORD"] = "p"
        assert resolve_account(Config(), Overrides(), environ).auth == "password"

    def test_an_unknown_name_with_jmap_url_takes_that_name(self):
        environ = {"JMAP_URL": "https://x"}
        assert resolve_account(Config(), Overrides(account="ci"), environ).name == "ci"

    def test_an_unknown_name(self):
        with pytest.raises(ConfigError, match=r"no account named 'x' \(known: a\)"):
            resolve_account(configured(a=AccountConfig()), Overrides(account="x"), {})
        with pytest.raises(ConfigError, match=r"no account named 'x'$"):
            resolve_account(Config(), Overrides(account="x"), {})

    def test_several_and_no_default(self):
        config = configured(a=AccountConfig(), b=AccountConfig())
        with pytest.raises(ConfigError, match="several accounts") as caught:
            resolve_account(config, Overrides(), {})
        assert caught.value.hint is not None
        assert "set-default" in caught.value.hint

    def test_nothing_configured_names_a_command_that_works(self):
        with pytest.raises(ConfigError, match="no account is configured") as caught:
            resolve_account(Config(), Overrides(), {})
        assert caught.value.hint is not None
        assert "--username ADDRESS" in caught.value.hint
