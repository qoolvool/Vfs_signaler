import pytest

from vfs_bot.accounts import (
    Account,
    AccountPool,
    make_mailbox,
    resolve_accounts,
)
from vfs_bot.config import (
    AccountConfig,
    AppConfig,
    IMAPConfig,
    ProxyConfig,
    TelegramConfig,
    VFSConfig,
)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def make_account(email, path="s.json"):
    return Account(
        config=AccountConfig(vfs_email=email, vfs_password="pw"),
        storage_state_path=path,
    )


def base_config(accounts=None):
    return AppConfig(
        vfs=VFSConfig(
            login_url="https://a",
            appointment_url="https://b",
            application_centre="C",
            category="Cat",
            sub_category="Sub",
            email="legacy@x.com",
            password="legacypw",
            storage_state_path="storage_state.json",
        ),
        imap=IMAPConfig(host="imap.x.com", username="glob@x.com", password="globpw"),
        telegram=TelegramConfig(),
        proxy=ProxyConfig(),
        accounts=accounts or [],
    )


class TestAccountPool:
    def test_requires_at_least_one(self):
        with pytest.raises(ValueError):
            AccountPool([])

    def test_single_account_current(self):
        pool = AccountPool([make_account("a@x.com")])
        assert pool.current.label == "a@x.com"
        assert len(pool) == 1

    def test_rotate_prefers_other_account(self):
        clock = FakeClock()
        pool = AccountPool(
            [make_account("a@x.com"), make_account("b@x.com")], clock=clock
        )
        assert pool.current.label == "a@x.com"
        new = pool.rotate()
        assert new.label == "b@x.com"

    def test_rotate_skips_cooling_down_account(self):
        clock = FakeClock()
        a, b, c = (
            make_account("a@x.com"),
            make_account("b@x.com"),
            make_account("c@x.com"),
        )
        pool = AccountPool([a, b, c], clock=clock)
        # Park b — rotating from a should land on c, not b.
        b.cooldown_until = clock.now + 100
        new = pool.rotate()
        assert new.label == "c@x.com"

    def test_rotate_returns_none_when_all_cooling(self):
        clock = FakeClock()
        a, b = make_account("a@x.com"), make_account("b@x.com")
        a.cooldown_until = clock.now + 100
        b.cooldown_until = clock.now + 200
        pool = AccountPool([a, b], clock=clock)
        assert pool.rotate() is None

    def test_set_cooldown_parks_current(self):
        clock = FakeClock()
        pool = AccountPool([make_account("a@x.com")], clock=clock)
        pool.set_cooldown(300)
        assert pool.current.cooldown_until == clock.now + 300
        assert not pool.current.is_available(clock.now)

    def test_single_account_recovers_after_cooldown(self):
        clock = FakeClock()
        pool = AccountPool([make_account("a@x.com")], clock=clock)
        pool.set_cooldown(300)
        assert pool.rotate() is None  # still cooling
        clock.now += 301
        assert pool.rotate().label == "a@x.com"  # now free

    def test_time_until_available(self):
        clock = FakeClock()
        a, b = make_account("a@x.com"), make_account("b@x.com")
        a.cooldown_until = clock.now + 500
        b.cooldown_until = clock.now + 120
        pool = AccountPool([a, b], clock=clock)
        assert pool.time_until_available() == 120

    def test_time_until_available_zero_when_free(self):
        clock = FakeClock()
        pool = AccountPool([make_account("a@x.com")], clock=clock)
        assert pool.time_until_available() == 0

    def test_reset_current_clears_streak(self):
        pool = AccountPool([make_account("a@x.com")])
        pool.current.consecutive_blocks = 3
        pool.reset_current()
        assert pool.current.consecutive_blocks == 0


class TestResolveAccounts:
    def test_fallback_to_single_legacy_account(self):
        accounts = resolve_accounts(base_config())
        assert len(accounts) == 1
        acc = accounts[0]
        assert acc.config.vfs_email == "legacy@x.com"
        assert acc.config.vfs_password == "legacypw"
        # Legacy single account keeps the original storage file unchanged.
        assert acc.storage_state_path == "storage_state.json"

    def test_multiple_accounts_get_distinct_storage_paths(self):
        cfg = base_config(
            accounts=[
                AccountConfig(vfs_email="a@x.com", vfs_password="p1"),
                AccountConfig(vfs_email="b@x.com", vfs_password="p2"),
            ]
        )
        accounts = resolve_accounts(cfg)
        assert len(accounts) == 2
        paths = {a.storage_state_path for a in accounts}
        assert len(paths) == 2
        assert "storage_state_a_x_com.json" in paths
        assert "storage_state_b_x_com.json" in paths

    def test_label_used_for_storage_path(self):
        cfg = base_config(
            accounts=[
                AccountConfig(vfs_email="a@x.com", vfs_password="p1", label="primary"),
            ]
        )
        accounts = resolve_accounts(cfg)
        assert accounts[0].storage_state_path == "storage_state_primary.json"
        assert accounts[0].label == "primary"


class TestMakeMailbox:
    def test_uses_account_imap_when_present(self):
        cfg = base_config()
        acc = Account(
            config=AccountConfig(
                vfs_email="a@x.com",
                vfs_password="p",
                imap_username="amail@x.com",
                imap_password="apw",
            ),
            storage_state_path="s.json",
        )
        mbox = make_mailbox(cfg, acc)
        assert mbox.config.username == "amail@x.com"
        assert mbox.config.password == "apw"
        # Global IMAP host/sender settings are preserved.
        assert mbox.config.host == "imap.x.com"

    def test_falls_back_to_global_imap(self):
        cfg = base_config()
        acc = Account(
            config=AccountConfig(vfs_email="a@x.com", vfs_password="p"),
            storage_state_path="s.json",
        )
        mbox = make_mailbox(cfg, acc)
        assert mbox.config.username == "glob@x.com"
        assert mbox.config.password == "globpw"
