"""Groq transcription client + Telegram file download.

Known pitfall (chigwell/telegram-mcp#219): Telegram voice notes have
extension ".oga" and Groq rejects it with 400 unsupported_audio_format.
We upload as ".ogg" instead — see map_extension().
"""
from __future__ import annotations

import httpx

# Extensions Groq accepts for whisper. Unknown/Telegram-specific ones map
# to the closest supported container.
_EXT_MAP = {
    "oga": "ogg",   # THE known pitfall: Telegram voice notes
    "ogg": "ogg",
    "opus": "ogg",
    "mp3": "mp3",
    "m4a": "m4a",
    "aac": "aac",
    "wav": "wav",
    "flac": "flac",
    "mp4": "mp4",
    "webm": "webm",
}


def map_extension(ext: str | None) -> str:
    """Map a file extension to one Groq accepts; unknown -> ogg."""
    ext = (ext or "").lower().lstrip(".")
    return _EXT_MAP.get(ext, "ogg")


class GroqError(Exception):
    """Base class for Groq failures (message is user-safe)."""


class GroqAuthError(GroqError):
    pass


class GroqRateLimitError(GroqError):
    pass


class GroqBadRequest(GroqError):
    def __init__(self, detail: str = ""):
        super().__init__(detail)
        self.detail = detail


class GroqTimeoutError(GroqError):
    pass


class FileTooLarge(GroqError):
    def __init__(self, size: int, limit_bytes: int):
        super().__init__(f"{size / 1024 / 1024:.0f} MB > limit")
        self.size = size
        self.limit_bytes = limit_bytes


class Transcript:
    def __init__(self, text: str, language: str | None) -> None:
        self.text = text
        self.language = language


class GroqClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float = 60.0,
        max_bytes: int = 25 * 1024 * 1024,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_bytes = max_bytes
        # Injectable for tests (httpx.MockTransport).
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- key validation ----------

    async def validate_key(self, key: str) -> bool:
        """Lightweight check: GET /models with the user's key."""
        try:
            r = await self._client.get(
                f"{self._base_url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
        except httpx.HTTPError:
            return False
        return r.status_code == 200

    # ---------- transcription ----------

    async def transcribe(
        self,
        key: str,
        data: bytes,
        ext: str | None = "ogg",
        lang: str = "auto",
        mime: str = "audio/ogg",
    ) -> Transcript:
        if len(data) > self._max_bytes:
            raise FileTooLarge(len(data), self._max_bytes)

        name = f"voice.{map_extension(ext)}"
        payload: dict[str, str] = {"model": self._model}
        if lang and lang != "auto":
            payload["language"] = lang

        headers = {"Authorization": f"Bearer {key}"}
        files = {"file": (name, data, mime)}
        try:
            r = await self._client.post(
                f"{self._base_url}/audio/transcriptions",
                headers=headers,
                data=payload,
                files=files,
            )
        except httpx.TimeoutException as e:
            raise GroqTimeoutError() from e
        except httpx.HTTPError as e:
            raise GroqError(f"network: {type(e).__name__}") from e

        if r.status_code == 401:
            raise GroqAuthError()
        if r.status_code == 429:
            raise GroqRateLimitError()
        if r.status_code >= 400:
            raise GroqBadRequest(detail=f"HTTP {r.status_code}")

        try:
            body = r.json()
        except ValueError:
            raise GroqBadRequest(detail="bad JSON")
        text = (body.get("text") or "").strip()
        return Transcript(text=text, language=body.get("language"))


async def download_telegram_file(bot, file_id: str) -> bytes:
    """Bot API getFile (hard limit 20 MB). Returns bytes, in memory only."""
    buf = await bot.download_file_by_id(file_id, timeout=120)
    return buf.read()


__all__ = [
    "GroqClient",
    "GroqError",
    "GroqAuthError",
    "GroqRateLimitError",
    "GroqBadRequest",
    "GroqTimeoutError",
    "FileTooLarge",
    "Transcript",
    "map_extension",
    "download_telegram_file",
]
