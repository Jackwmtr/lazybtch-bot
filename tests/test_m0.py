import pytest

from lazybtch import handlers
from tests.conftest import FakeMessage


@pytest.mark.parametrize("text", ["/start", "/help"])
async def test_command_texts(text):
    h = handlers.Handlers(bot=None, settings=None)
    m = FakeMessage(text=text)
    if text == "/start":
        await h.start(m)
    else:
        await h.help(m)
    assert m.answers
    reply = m.answers[0][0][0]
    assert "LazyBTch" in reply or "Перешли голосовое" in reply
