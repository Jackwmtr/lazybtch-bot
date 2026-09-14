import pytest


class FakeUser:
    def __init__(self, id: int, username: str = "alice"):
        self.id = id
        self.username = username
        self.first_name = "Alice"
        self.last_name = None
        self.is_bot = False
        self.language_code = "ru"


class FakeChat:
    def __init__(self, id: int, type_: str = "private"):
        self.id = id
        self.type = type_
        self.title = None
        self.username = None


class FakeMessage:
    """Minimal stand-in for aiogram types.Message."""

    def __init__(
        self,
        user: FakeUser | None = None,
        chat: FakeChat | None = None,
        text: str | None = None,
        **kw,
    ):
        self.from_user = user or FakeUser(1)
        self.chat = chat or FakeChat(self.from_user.id)
        self.text = text
        self.caption = None
        self.reply_to_message = None
        self.message_id = 1
        self.date = 0
        self.entities = []
        self.content_type = "text"
        for k, v in kw.items():
            setattr(self, k, v)

    async def answer(self, *args, **kwargs):
        self.answers = getattr(self, "answers", [])
        self.answers.append((args, kwargs))
        return args[0] if args else None


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from lazybtch.config import Settings

    monkeypatch.setenv("BOT_TOKEN", "123456:TEST-token-not-real")
    monkeypatch.setenv("ENCRYPTION_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
    s = Settings(db_path=tmp_path / "test.db")
    return s
