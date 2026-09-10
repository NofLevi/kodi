"""Credential-bearing values must never reach Kodi logs."""
from katan import kodi


def test_missing_adaptive_log_does_not_expose_signed_stream_path(monkeypatch):
    from katan.ui import listing
    secret = "https://cdn.example/private/SYNTH_PATH_TOKEN/manifest.mpd?sig=QUERY"
    logs = []

    class Item(object):
        def setPath(self, value):
            pass

    monkeypatch.setattr(listing, "make_list_item", lambda item: Item())
    monkeypatch.setattr(kodi, "has_adaptive", lambda: False)
    monkeypatch.setattr(kodi, "log_error", logs.append)
    monkeypatch.setattr(kodi, "notify", lambda *args: None)
    monkeypatch.setattr(kodi, "localize", lambda value: str(value))
    monkeypatch.setattr(listing.xbmcplugin, "setResolvedUrl", lambda *args: None)
    listing.resolve_stream(1, secret, adaptive=True, item={"title": "x"})
    assert "SYNTH_PATH_TOKEN" not in " ".join(logs)


def test_exception_logging_omits_exception_values(monkeypatch):
    logs = []
    monkeypatch.setattr(kodi, "log_error", logs.append)
    try:
        raise ValueError("failed https://cdn.example/file?token=SYNTH_EXCEPTION_TOKEN")
    except ValueError:
        kodi.log_exception("range read failed")
    text = " ".join(logs)
    assert "range read failed" in text
    assert "ValueError" in text
    assert "SYNTH_EXCEPTION_TOKEN" not in text


def test_translation_retry_log_omits_backend_exception_values(monkeypatch):
    from katan.subs import srt
    from katan.subs.ai import translator
    logs = []

    class Backend(object):
        @staticmethod
        def complete(*args):
            raise RuntimeError("Authorization: Bearer SYNTH_TRANSLATOR_BEARER")

    monkeypatch.setattr(translator.kodi, "log",
                        lambda message, *args: logs.append(message))
    batch = [srt.Cue(i, float(i), float(i + 1), "text")
             for i in range(translator.MIN_SPLIT * 2)]
    try:
        translator._translate_batch(Backend(), batch, "Hebrew", 0)
    except translator.TranslationError:
        pass
    assert "SYNTH_TRANSLATOR_BEARER" not in " ".join(logs)


def test_update_failure_log_omits_private_url(monkeypatch):
    from katan import updater
    secret = "https://updates.example/private/SYNTH_UPDATE_TOKEN/k.zip?sig=SYNTH_SIG"
    logs = []

    class Response(object):
        status_code = 500

    monkeypatch.setattr(updater.http, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(updater.kodi, "log_error", logs.append)
    assert updater.download(secret) == ""
    text = " ".join(logs)
    assert "SYNTH_UPDATE_TOKEN" not in text
    assert "SYNTH_SIG" not in text


def test_page_failure_log_omits_url_userinfo(monkeypatch):
    from katan.vod.extractors import page
    secret = "https://user:SYNTH_PASSWORD@vod.example/private"
    logs = []

    class Response(object):
        status_code = 500

    monkeypatch.setattr(page.http, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(page.kodi, "log",
                        lambda message, *args: logs.append(message))
    assert page.fetch(secret) == ""
    assert "SYNTH_PASSWORD" not in " ".join(logs)


def test_extractor_failure_logs_omit_private_urls(monkeypatch):
    from katan.vod.extractors import radio891, sport1
    logs = []
    secret = "https://vod.example/private/SYNTH_PATH?token=SYNTH_QUERY"
    monkeypatch.setattr(kodi, "log", lambda message, *args: logs.append(message))
    monkeypatch.setattr(sport1.page, "fetch", lambda url, **kwargs: "<html></html>")
    monkeypatch.setattr(radio891.page, "absolute", lambda ref, base: secret)
    monkeypatch.setattr(radio891.page, "fetch", lambda url, **kwargs: "<html></html>")
    assert sport1._clips(secret) == []
    assert radio891.stream(secret) == ("", False)
    text = " ".join(logs)
    assert "SYNTH_PATH" not in text
    assert "SYNTH_QUERY" not in text


def test_paste_server_log_omits_private_lan_url(monkeypatch):
    from katan import pastebox
    logs = []

    class Done(object):
        @staticmethod
        def wait(timeout):
            return True

        @staticmethod
        def is_set():
            return False

    class Server(object):
        server_port = 54321

        def __init__(self, *args):
            pass

        def serve_forever(self):
            pass

        def shutdown(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(pastebox, "lan_address", lambda: "192.0.2.55")
    monkeypatch.setattr(pastebox, "HTTPServer", Server)
    monkeypatch.setattr(pastebox.threading, "Thread", lambda target: type(
        "Thread", (), {"daemon": False, "start": lambda self: None})())
    monkeypatch.setattr(kodi, "log", lambda message, *args: logs.append(message))
    monkeypatch.setattr(pastebox, "_handler_for", lambda state, labels: object)
    # Replace the event factory so receive() does not actually wait.
    monkeypatch.setattr(pastebox.threading, "Event", Done)
    assert pastebox.receive("Paste", lifetime=0) == ""
    assert "192.0.2.55" not in " ".join(logs)
    assert "54321" not in " ".join(logs)


def test_signed_url_cache_keys_are_opaque():
    from katan import cache
    secret = "https://cdn.example/SYNTH_CACHE_PATH?sig=SYNTH_CACHE_SIG"
    key = cache.make_key("vod", "sport1", secret)
    assert "SYNTH_CACHE_PATH" not in key
    assert "SYNTH_CACHE_SIG" not in key
    assert key == cache.make_key("vod", "sport1", secret)


def test_cache_failure_context_omits_signed_key(monkeypatch):
    from katan import cache
    secret = "vod|sport1|https://cdn.example/SYNTH_CACHE_PATH?sig=SYNTH_CACHE_SIG"
    contexts = []

    def broken():
        raise cache.sqlite3.DatabaseError("broken")

    monkeypatch.setattr(cache, "_connect", broken)
    monkeypatch.setattr(cache.kodi, "log_exception", contexts.append)
    assert cache.get(secret) is None
    cache.set(secret, {"safe": True}, 60)
    text = " ".join(contexts)
    assert "SYNTH_CACHE_PATH" not in text
    assert "SYNTH_CACHE_SIG" not in text


def test_auth_qr_failure_does_not_log_verification_url(monkeypatch):
    from katan.ui import auth_window, signin
    secret = "https://auth.example/verify?device_code=SYNTH_DEVICE_CODE_7K9Q"
    logs = []

    class Window(object):
        def __init__(self, *args):
            import threading
            self.stop = threading.Event()

        def prepare(self):
            pass

        def doModal(self):
            pass

    monkeypatch.setattr(auth_window, "AuthWindow", Window)
    monkeypatch.setattr(signin, "code_image", lambda url: "")
    monkeypatch.setattr(kodi, "log", lambda message, *args: logs.append(message))
    auth_window.open_auth("Sign in", secret, "CODE", lambda: False)
    assert "SYNTH_DEVICE_CODE_7K9Q" not in " ".join(logs)
