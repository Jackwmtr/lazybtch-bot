import httpx
import pytest

from lazybtch.groq import (
    FileTooLarge,
    GroqAuthError,
    GroqBadRequest,
    GroqClient,
    GroqRateLimitError,
    map_extension,
)


def make_groq(handler, max_bytes=25 * 1024 * 1024):
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return GroqClient(
        base_url="https://api.groq.com/openai/v1",
        model="whisper-large-v3-turbo",
        max_bytes=max_bytes,
        client=client,
    )


# ---------- extension mapping (THE known pitfall) ----------

def test_map_extension_oga_to_ogg():
    assert map_extension("oga") == "ogg"
    assert map_extension("Oga") == "ogg"
    assert map_extension(".oga") == "ogg"
    assert map_extension("opus") == "ogg"
    assert map_extension("mp3") == "mp3"
    assert map_extension("weirdext") == "ogg"  # unknown -> ogg
    assert map_extension(None) == "ogg"


async def test_oga_never_uploaded():
    """Telegram .oga must reach Groq as .ogg (chigwell/telegram-mcp#219)."""
    seen = {}

    def handler(request):
        body = request.content
        seen["body"] = body
        return httpx.Response(200, json={"text": "hi", "language": "en"})

    g = make_groq(handler)
    await g.transcribe("gsk_x", b"audio", ext="oga")
    assert b'filename="voice.ogg"' in seen["body"]
    assert b"oga" not in seen["body"].replace(b"voice.ogg", b"")


# ---------- transcription scenarios ----------

async def test_200_ok():
    g = make_groq(lambda r: httpx.Response(200, json={"text": "привет", "language": "ru"}))
    t = await g.transcribe("gsk_x", b"audio", ext="oga")
    assert t.text == "привет"
    assert t.language == "ru"


async def test_empty_text_is_not_error():
    g = make_groq(lambda r: httpx.Response(200, json={"text": "   ", "language": None}))
    t = await g.transcribe("gsk_x", b"audio")
    assert t.text == ""


async def test_401():
    g = make_groq(lambda r: httpx.Response(401, text="invalid key"))
    with pytest.raises(GroqAuthError):
        await g.transcribe("gsk_bad", b"audio")


async def test_429():
    g = make_groq(lambda r: httpx.Response(429, text="rate limit"))
    with pytest.raises(GroqRateLimitError):
        await g.transcribe("gsk_x", b"audio")


async def test_400_bad_request():
    g = make_groq(lambda r: httpx.Response(400, json={"error": "unsupported_audio_format"}))
    with pytest.raises(GroqBadRequest) as ei:
        await g.transcribe("gsk_x", b"audio")
    assert "400" in ei.value.detail


async def test_too_large_before_upload():
    def handler(request):
        raise AssertionError("must not call Groq for oversized file")

    g = make_groq(handler, max_bytes=100)
    with pytest.raises(FileTooLarge):
        await g.transcribe("gsk_x", b"a" * 200)


async def test_language_sent_only_when_not_auto():
    seen = {}

    def handler(request):
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "x"})

    g = make_groq(handler)
    await g.transcribe("gsk_x", b"audio", lang="auto")
    assert b'name="language"' not in seen["body"]
    await g.transcribe("gsk_x", b"audio", lang="ru")
    assert b'name="language"' in seen["body"]


# ---------- telegram download ----------

async def test_download_telegram_file_uses_bot_api():
    """Regression: bot.token doesn't exist in aiogram 2 (AttributeError).
    Must go through bot.download_file_by_id, audio stays in memory."""
    import io

    from lazybtch.groq import download_telegram_file

    class Bot:
        # no .token attribute — like aiogram 2's Bot
        async def download_file_by_id(self, file_id, timeout=30):
            assert file_id == "f1"
            buf = io.BytesIO(b"audio-bytes")
            buf.seek(0)
            return buf

    data = await download_telegram_file(Bot(), "f1")
    assert data == b"audio-bytes"


# ---------- key validation ----------

async def test_validate_key_ok():
    g = make_groq(lambda r: httpx.Response(200, json={"data": []}))
    assert await g.validate_key("gsk_ok") is True


async def test_validate_key_rejected():
    g = make_groq(lambda r: httpx.Response(401))
    assert await g.validate_key("gsk_bad") is False
