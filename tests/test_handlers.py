"""Integration: handler pipeline with mocked Groq (httpx.MockTransport) and fake bot."""
import hashlib
import httpx
import pytest

from lazybtch.crypto import KeyVault
from lazybtch.groq import GroqClient
from lazybtch.handlers import Handlers
from lazybtch.store import Store
from tests.conftest import FakeChat, FakeMessage, FakeUser

MASTER = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


class FakeBot:
    token = "TEST"
    username = "lazybtch_bot"

    def __init__(self, file_bytes: bytes = b"fake-audio-bytes"):
        self._file_bytes = file_bytes

    async def get_file(self, file_id):
        class F:
            file_path = "file_id"
        return F()


def make_groq(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GroqClient(
        base_url="https://api.groq.com/openai/v1",
        model="whisper-large-v3-turbo",
        client=client,
    )


@pytest.fixture
def env(tmp_path, settings):
    store = Store(tmp_path / "t.db")
    vault = KeyVault(MASTER)
    state = {"transcribe_calls": 0}

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        state["transcribe_calls"] += 1
        return httpx.Response(200, json={"text": "Передаю привет на конверте", "language": "ru"})

    groq = make_groq(handler)
    bot = FakeBot()
    handlers = Handlers(bot=bot, settings=settings, store=store, vault=vault, groq=groq)
    # Monkeypatch download to avoid real network in tests.
    import lazybtch.handlers as H

    orig = H.download_telegram_file
    H.download_telegram_file = lambda bot, file_id: _fake_download(bot, file_id)
    try:
        yield handlers, store, state
    finally:
        H.download_telegram_file = orig


async def _fake_download(bot, file_id):
    return bot._file_bytes


def voice_message(user_id=1, **kw):
    class Voice:
        file_id = "vid1"
        duration = kw.get("duration", 5)
        file_size = kw.get("file_size", 1024)
        mime_type = "audio/ogg"
    m = FakeMessage(user=FakeUser(user_id), text=None)
    m.voice = Voice()
    return m


async def test_no_key(env):
    handlers, store, _ = env
    m = voice_message()
    reply = await handlers.process_audio(m, 1)
    assert "ключа Groq" in reply
    assert store.user_stats(1)["counts"].get("no_key") == 1


async def test_ok_then_cache_hit(env):
    handlers, store, state = env
    store.set_groq_key(1, KeyVault(MASTER).encrypt("gsk_test"))
    m = voice_message()
    await handlers.process_audio(m, 1)
    assert state["transcribe_calls"] == 1

    m2 = voice_message(user_id=2)  # same audio, different user -> cache
    reply2 = await handlers.process_audio(m2, 2)
    assert state["transcribe_calls"] == 1  # no second Groq call
    assert "кэше" not in reply2
    assert "Передаю привет" in reply2
    assert store.user_stats(2)["counts"].get("cache_hit") == 1
    assert store.user_stats(1)["counts"].get("ok") == 1


async def test_too_large_rejected_before_download(env):
    handlers, store, state = env
    big = voice_message(file_size=47 * 1024 * 1024)
    reply = await handlers.process_audio(big, 1)
    assert "лимит" in reply and "47" in reply
    assert state["transcribe_calls"] == 0
    assert store.user_stats(1)["counts"].get("too_large") == 1


@pytest.mark.parametrize("status,kind,needle", [
    (401, "groq_401", "401"),
    (429, "groq_429", "лимит"),
    (400, "groq_400", "400"),
])
async def test_groq_errors(env, status, kind, needle):
    handlers, store, _ = env
    import lazybtch.handlers as H
    from lazybtch.crypto import KeyVault as KV

    store.set_groq_key(1, KV(MASTER).encrypt("gsk_whatever"))

    orig = H.GroqClient.transcribe

    async def raise_error(self, key, data, ext=None, lang="auto", mime="audio/ogg"):
        if status == 401:
            from lazybtch.groq import GroqAuthError
            raise GroqAuthError()
        if status == 429:
            from lazybtch.groq import GroqRateLimitError
            raise GroqRateLimitError()
        from lazybtch.groq import GroqBadRequest
        raise GroqBadRequest(detail=f"HTTP {status}")

    H.GroqClient.transcribe = raise_error
    try:
        reply = await handlers.process_audio(voice_message(), 1)
    finally:
        H.GroqClient.transcribe = orig
    assert needle in reply
    assert store.user_stats(1)["counts"].get(kind) == 1


async def test_empty_transcript(env):
    handlers, store, _ = env
    import lazybtch.handlers as H
    from lazybtch.groq import Transcript

    store.set_groq_key(1, None)
    from lazybtch.crypto import KeyVault as KV
    store.set_groq_key(1, KV(MASTER).encrypt("gsk_whatever"))

    orig = H.GroqClient.transcribe

    async def empty(self, key, data, ext=None, lang="auto", mime="audio/ogg"):
        return Transcript(text="", language=None)

    H.GroqClient.transcribe = empty
    try:
        reply = await handlers.process_audio(voice_message(), 1)
    finally:
        H.GroqClient.transcribe = orig
    assert "нет речи" in reply
    assert store.user_stats(1)["counts"].get("empty") == 1
    assert store.user_stats(1)["counts"].get("ok") is None


async def test_video_note_extraction(env):
    """Regression: aiogram 2 VideoNote has no mime_type attribute."""
    handlers, store, _ = env

    class VideoNote:
        file_id = "vn1"
        duration = 9
        file_size = 50_000
        # no mime_type on purpose

    m = FakeMessage(user=FakeUser(1), text=None)
    m.voice = None
    m.video_note = VideoNote()
    file_id, duration, size, ext, mime = handlers._extract_audio(m)
    assert (file_id, duration, size, ext, mime) == ("vn1", 9, 50_000, "mp4", "video/mp4")


async def test_long_text_chunked(env):
    from lazybtch.handlers import _chunk
    parts = _chunk("x" * 9000)
    assert len(parts) == 3
    assert len(parts[0]) == 4096


async def test_group_policy_mentions(env, settings):
    handlers, store, _ = env
    settings.group_policy = "mentions"

    def group_msg(text=None, user_id=7):
        class Voice:
            file_id = "v"
            duration = 1
            file_size = 100
            mime_type = "audio/ogg"
        m = FakeMessage(user=FakeUser(user_id), chat=FakeChat(100, "supergroup"), text=text)
        m.voice = Voice()
        m.entities = []
        return m

    # without mention -> ignored
    assert not handlers._group_allowed(group_msg())
    # with mention -> allowed
    assert handlers._group_allowed(group_msg(text="расшифруй @lazybtch_bot"))

    settings.group_policy = "all"
    assert handlers._group_allowed(group_msg())
    settings.group_policy = "off"
    assert not handlers._group_allowed(group_msg())
