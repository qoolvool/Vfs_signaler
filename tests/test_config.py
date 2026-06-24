import textwrap

import pytest

from vfs_bot.config import (
    AppConfig,
    ProxyConfig,
    VFSConfig,
    load_config,
)


@pytest.fixture()
def config_file(tmp_path):
    """Creates a minimal config.yaml and returns its path."""
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent("""\
        vfs:
          login_url: "https://example.com/login"
          appointment_url: "https://example.com/book"
          application_centre: "Centre,City"
          category: "C visa"
          sub_category: "Tourist"
        imap:
          host: "imap.example.com"
    """)
    )
    return str(path)


class TestLoadConfig:
    def test_loads_valid_config(self, config_file):
        cfg = load_config(config_file)
        assert isinstance(cfg, AppConfig)
        assert cfg.vfs.login_url == "https://example.com/login"
        assert cfg.vfs.appointment_url == "https://example.com/book"
        assert cfg.imap.host == "imap.example.com"

    def test_default_values_applied(self, config_file):
        cfg = load_config(config_file)
        assert cfg.vfs.poll_interval_min_seconds == 1800
        assert cfg.vfs.poll_interval_max_seconds == 2400
        assert cfg.vfs.headless is False
        assert cfg.imap.port == 993
        assert cfg.imap.folder == "INBOX"

    def test_missing_optional_sections(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            textwrap.dedent("""\
            vfs:
              login_url: "https://x.com/login"
              appointment_url: "https://x.com/book"
              application_centre: "C"
              category: "C"
              sub_category: "T"
            imap:
              host: "imap.x.com"
        """)
        )
        cfg = load_config(str(path))
        assert cfg.telegram.bot_token == ""
        assert cfg.proxy.server == ""

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path.yaml")

    def test_overrides_from_yaml(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            textwrap.dedent("""\
            vfs:
              login_url: "https://x.com/login"
              appointment_url: "https://x.com/book"
              application_centre: "C"
              category: "C"
              sub_category: "T"
              poll_interval_min_seconds: 60
              poll_interval_max_seconds: 120
              headless: true
            imap:
              host: "imap.x.com"
              port: 1143
        """)
        )
        cfg = load_config(str(path))
        assert cfg.vfs.poll_interval_min_seconds == 60
        assert cfg.vfs.poll_interval_max_seconds == 120
        assert cfg.vfs.headless is True
        assert cfg.imap.port == 1143

    def test_keywords_from_yaml(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            textwrap.dedent("""\
            vfs:
              login_url: "https://x.com/login"
              appointment_url: "https://x.com/book"
              application_centre: "Visa Application Centre,Zagreb"
              centre_keyword: "zagreb"
              category: "D visa"
              category_keyword: "d visa"
              sub_category: "Work"
              sub_category_keyword: "work"
            imap:
              host: "imap.x.com"
        """)
        )
        cfg = load_config(str(path))
        assert cfg.vfs.centre_keyword == "zagreb"
        assert cfg.vfs.category_keyword == "d visa"
        assert cfg.vfs.sub_category_keyword == "work"


class TestProxyConfig:
    def test_disabled_when_empty(self):
        p = ProxyConfig(server="")
        assert p.enabled is False
        assert p.to_playwright() is None

    def test_enabled_with_server(self):
        p = ProxyConfig(server="http://1.2.3.4:8080")
        assert p.enabled is True

    def test_to_playwright_minimal(self):
        p = ProxyConfig(server="http://proxy:8080")
        result = p.to_playwright()
        assert result == {"server": "http://proxy:8080"}

    def test_to_playwright_with_credentials(self):
        p = ProxyConfig(server="socks5://proxy:1080", username="u", password="p")
        result = p.to_playwright()
        assert result == {
            "server": "socks5://proxy:1080",
            "username": "u",
            "password": "p",
        }

    def test_to_playwright_username_only(self):
        p = ProxyConfig(server="http://proxy:80", username="u")
        result = p.to_playwright()
        assert result == {"server": "http://proxy:80", "username": "u"}


class TestVFSConfigDefaults:
    def test_required_fields(self):
        v = VFSConfig(
            login_url="https://a.com",
            appointment_url="https://b.com",
            application_centre="C",
            category="Cat",
            sub_category="Sub",
        )
        assert v.access_denied_backoff_seconds == 1800
        assert v.debug_screenshots is True
        assert v.debug_dir == "debug"
        assert v.storage_state_path == "storage_state.json"

    def test_keyword_defaults_from_full_values(self):
        v = VFSConfig(
            login_url="https://a.com",
            appointment_url="https://b.com",
            application_centre="Visa Application Centre,Belgrade",
            category="C visa",
            sub_category="Tourist, Visit , Business",
        )
        assert v.centre_keyword == "visa application centre,belgrade"
        assert v.category_keyword == "c visa"
        assert v.sub_category_keyword == "tourist, visit , business"

    def test_keyword_explicit_overrides(self):
        v = VFSConfig(
            login_url="https://a.com",
            appointment_url="https://b.com",
            application_centre="Visa Application Centre,Belgrade",
            category="C visa",
            sub_category="Tourist, Visit , Business",
            centre_keyword="belgrade",
            category_keyword="c visa",
            sub_category_keyword="tourist",
        )
        assert v.centre_keyword == "belgrade"
        assert v.category_keyword == "c visa"
        assert v.sub_category_keyword == "tourist"
