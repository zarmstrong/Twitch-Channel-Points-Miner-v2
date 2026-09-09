from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner


def _miner(streamer_mutex):
    miner = TwitchChannelPointsMiner.__new__(TwitchChannelPointsMiner)
    miner.running = True
    miner.streamers = [
        SimpleNamespace(
            username="alice",
            irc_chat=None,
            mutex=streamer_mutex,
        )
    ]
    miner.twitch = SimpleNamespace(running=True)
    miner.drop_badge_catalog_stop_event = MagicMock()
    miner.ws_pool = None
    miner.minute_watcher_thread = None
    miner.sync_campaigns_thread = None
    miner.drop_badge_catalog_thread = None
    miner.queue_listener = MagicMock()
    return miner


def test_end_releases_a_still_locked_streamer_mutex_normally():
    mutex = MagicMock()
    mutex.locked.return_value = True
    mutex.acquire.return_value = True
    miner = _miner(mutex)

    warning_path = "TwitchChannelPointsMiner.TwitchChannelPointsMiner.logger.warning"
    report_path = (
        "TwitchChannelPointsMiner.TwitchChannelPointsMiner."
        "TwitchChannelPointsMiner._TwitchChannelPointsMiner__print_report"
    )
    with patch(warning_path) as warning, patch(report_path):
        with pytest.raises(SystemExit):
            miner.end(None, None)

    mutex.acquire.assert_called_once_with(timeout=30)
    mutex.release.assert_called_once()
    warning.assert_not_called()


def test_end_does_not_hang_forever_when_a_streamer_mutex_never_releases():
    # Reproduces a real report: a save still in progress (e.g. a slow/hung
    # disk write) held streamer.mutex, and a bare acquire() here - unlike
    # every other join in this method, which already tolerates its thread
    # not stopping in time - blocked shutdown (and the whole GUI thread
    # driving it, in the Windows shell) forever. Must time out and continue
    # instead of blocking indefinitely.
    mutex = MagicMock()
    mutex.locked.return_value = True
    mutex.acquire.return_value = False  # Simulates a timed-out acquire.
    miner = _miner(mutex)

    warning_path = "TwitchChannelPointsMiner.TwitchChannelPointsMiner.logger.warning"
    report_path = (
        "TwitchChannelPointsMiner.TwitchChannelPointsMiner."
        "TwitchChannelPointsMiner._TwitchChannelPointsMiner__print_report"
    )
    with patch(warning_path) as warning, patch(report_path) as report:
        with pytest.raises(SystemExit):
            miner.end(None, None)

    mutex.acquire.assert_called_once_with(timeout=30)
    mutex.release.assert_not_called()  # Never acquired - nothing to release.
    warning.assert_called_once()
    # Shutdown must still complete rather than getting stuck on this one
    # unresponsive streamer.
    report.assert_called_once()
    miner.queue_listener.stop.assert_called_once()
