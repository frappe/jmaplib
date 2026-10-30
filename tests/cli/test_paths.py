"""Where the command keeps its files, on every platform."""

from __future__ import annotations

from pathlib import Path

from jmap.cli.paths import config_path, state_dir


class TestConfigPath:
    def test_jmap_config_wins(self):
        environ = {"JMAP_CONFIG": "/etc/jmap.toml", "XDG_CONFIG_HOME": "/x"}
        assert config_path(environ) == Path("/etc/jmap.toml")

    def test_xdg_is_honoured_on_windows_too(self):
        environ = {"XDG_CONFIG_HOME": "/x", "APPDATA": "C:/AppData"}
        assert config_path(environ, os_name="nt") == Path("/x/jmap/config.toml")

    def test_appdata_on_windows(self):
        environ = {"APPDATA": "C:/AppData", "HOME": "/h"}
        assert config_path(environ, os_name="nt") == Path("C:/AppData/jmap/config.toml")

    def test_home_elsewhere(self):
        environ = {"APPDATA": "C:/AppData", "HOME": "/h"}
        assert config_path(environ, os_name="posix") == Path("/h/.config/jmap/config.toml")

    def test_userprofile_when_there_is_no_home(self):
        environ = {"USERPROFILE": "/u"}
        assert config_path(environ, os_name="nt") == Path("/u/.config/jmap/config.toml")

    def test_the_real_home_as_a_last_resort(self):
        assert config_path({}) == Path.home() / ".config/jmap/config.toml"


class TestStateDir:
    def test_xdg(self):
        assert state_dir({"XDG_STATE_HOME": "/s"}) == Path("/s/jmap")

    def test_localappdata_on_windows(self):
        assert state_dir({"LOCALAPPDATA": "C:/L"}, os_name="nt") == Path("C:/L/jmap")

    def test_home(self):
        assert state_dir({"HOME": "/h"}, os_name="posix") == Path("/h/.local/state/jmap")
