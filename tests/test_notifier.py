import json
from unittest.mock import patch

import pytest
import requests as req

from vfs_bot.config import TelegramConfig
from vfs_bot.notifier import TelegramNotifier


@pytest.fixture()
def configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return TelegramNotifier(TelegramConfig(bot_token="tok123", chat_id="42"))


@pytest.fixture()
def multi_chat(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return TelegramNotifier(TelegramConfig(bot_token="tok123", chat_id="42, 99,101"))


@pytest.fixture()
def unconfigured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return TelegramNotifier(TelegramConfig(bot_token="", chat_id=""))


class TestSubscribers:
    def test_loads_from_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "subscribers.json").write_text('["111","222"]')
        n = TelegramNotifier(TelegramConfig(bot_token="tok", chat_id=""))
        assert sorted(n.chat_ids) == ["111", "222"]

    def test_config_chat_id_seeds_subscribers(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        n = TelegramNotifier(TelegramConfig(bot_token="tok", chat_id="42"))
        assert "42" in n.chat_ids

    def test_merges_file_and_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "subscribers.json").write_text('["111"]')
        n = TelegramNotifier(TelegramConfig(bot_token="tok", chat_id="42"))
        assert sorted(n.chat_ids) == ["111", "42"]

    def test_saves_subscribers(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        n = TelegramNotifier(TelegramConfig(bot_token="tok", chat_id=""))
        n._subscribers.add("55")
        n._save_subscribers()
        data = json.loads((tmp_path / "subscribers.json").read_text())
        assert data == ["55"]

    def test_no_file_starts_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        n = TelegramNotifier(TelegramConfig(bot_token="tok", chat_id=""))
        assert n.chat_ids == []


class TestSend:
    @patch("vfs_bot.notifier.requests.post")
    def test_sends_message(self, mock_post, configured):
        configured.send("hello")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["data"]["text"] == "hello"
        assert call_kwargs[1]["data"]["chat_id"] == "42"

    @patch("vfs_bot.notifier.requests.post")
    def test_sends_to_all_chat_ids(self, mock_post, multi_chat):
        multi_chat.send("hello")
        assert mock_post.call_count == 3
        chat_ids_sent = [c[1]["data"]["chat_id"] for c in mock_post.call_args_list]
        assert sorted(chat_ids_sent) == ["101", "42", "99"]

    @patch("vfs_bot.notifier.requests.post")
    def test_skips_when_no_token(self, mock_post, unconfigured):
        unconfigured.send("hello")
        mock_post.assert_not_called()

    @patch("vfs_bot.notifier.requests.post")
    def test_does_not_raise_on_request_error(self, mock_post, configured):
        mock_post.side_effect = req.RequestException("network")
        configured.send("hello")

    @patch("vfs_bot.notifier.requests.post")
    def test_continues_on_partial_failure(self, mock_post, multi_chat):
        mock_post.side_effect = [req.RequestException("fail"), None, None]
        multi_chat.send("hello")
        assert mock_post.call_count == 3


class TestSendPhoto:
    @patch("vfs_bot.notifier.requests.post")
    def test_sends_photo(self, mock_post, configured, tmp_path):
        photo = tmp_path / "shot.png"
        photo.write_bytes(b"\x89PNG")

        configured.send_photo(str(photo), "caption")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["data"]["caption"] == "caption"
        assert "photo" in call_kwargs[1]["files"]

    @patch("vfs_bot.notifier.requests.post")
    def test_sends_photo_to_all_chat_ids(self, mock_post, multi_chat, tmp_path):
        photo = tmp_path / "shot.png"
        photo.write_bytes(b"\x89PNG")

        multi_chat.send_photo(str(photo), "cap")
        assert mock_post.call_count == 3
        chat_ids_sent = [c[1]["data"]["chat_id"] for c in mock_post.call_args_list]
        assert sorted(chat_ids_sent) == ["101", "42", "99"]

    @patch("vfs_bot.notifier.requests.post")
    def test_skips_when_no_token(self, mock_post, unconfigured, tmp_path):
        photo = tmp_path / "shot.png"
        photo.write_bytes(b"\x89PNG")
        unconfigured.send_photo(str(photo))
        mock_post.assert_not_called()

    @patch("vfs_bot.notifier.requests.post")
    def test_handles_missing_file(self, mock_post, configured):
        configured.send_photo("/nonexistent/file.png", "cap")
        mock_post.assert_not_called()
