from unittest.mock import patch

import pytest
import requests as req

from vfs_bot.config import TelegramConfig
from vfs_bot.notifier import TelegramNotifier


@pytest.fixture()
def configured():
    return TelegramNotifier(TelegramConfig(bot_token="tok123", chat_id="42"))


@pytest.fixture()
def multi_chat():
    return TelegramNotifier(TelegramConfig(bot_token="tok123", chat_id="42,99,  101"))


@pytest.fixture()
def unconfigured():
    return TelegramNotifier(TelegramConfig(bot_token="", chat_id=""))


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
        assert chat_ids_sent == ["42", "99", "101"]

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
        assert chat_ids_sent == ["42", "99", "101"]

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
