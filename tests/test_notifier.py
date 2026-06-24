from unittest.mock import patch

import pytest

from vfs_bot.config import TelegramConfig
from vfs_bot.notifier import TelegramNotifier


@pytest.fixture()
def configured():
    return TelegramNotifier(TelegramConfig(bot_token="tok123", chat_id="42"))


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
        assert "bot" in call_kwargs[0][0]

    @patch("vfs_bot.notifier.requests.post")
    def test_skips_when_no_token(self, mock_post, unconfigured):
        unconfigured.send("hello")
        mock_post.assert_not_called()

    @patch("vfs_bot.notifier.requests.post")
    def test_does_not_raise_on_request_error(self, mock_post, configured):
        import requests as req

        mock_post.side_effect = req.RequestException("network")
        configured.send("hello")


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
    def test_skips_when_no_token(self, mock_post, unconfigured, tmp_path):
        photo = tmp_path / "shot.png"
        photo.write_bytes(b"\x89PNG")
        unconfigured.send_photo(str(photo))
        mock_post.assert_not_called()

    @patch("vfs_bot.notifier.requests.post")
    def test_handles_missing_file(self, mock_post, configured):
        configured.send_photo("/nonexistent/file.png", "cap")
        mock_post.assert_not_called()
