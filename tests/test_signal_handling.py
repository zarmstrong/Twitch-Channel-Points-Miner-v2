import signal
import threading

from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner


class _FakeMiner:
    def end(self, *args):
        pass


def test_register_signal_handlers_is_a_noop_off_the_main_thread():
    # The Windows desktop shell runs the miner on a background thread so
    # pywebview's blocking loop can own the main thread; signal.signal()
    # raises ValueError there, which previously crashed the miner thread
    # right after __init__ on every launch.
    fake = _FakeMiner()
    errors = []

    def run():
        try:
            TwitchChannelPointsMiner._register_signal_handlers(fake)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()

    assert errors == []


def test_register_signal_handlers_installs_handlers_on_main_thread(monkeypatch):
    fake = _FakeMiner()
    registered = []
    monkeypatch.setattr(
        signal, "signal", lambda sig, handler: registered.append((sig, handler))
    )

    TwitchChannelPointsMiner._register_signal_handlers(fake)

    assert len(registered) == 3
    assert all(handler == fake.end for _sig, handler in registered)
