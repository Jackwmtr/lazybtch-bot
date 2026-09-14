# LazyBTch Bot 🎙

Telegram-бот: пересылаешь голосовое (или кружок) → бот транскрибирует через
Groq `whisper-large-v3-turbo` и возвращает текст. Ключ Groq каждого
пользователя хранится зашифрованным (Fernet), повторные пересылки того же
файла берутся из кэша (sha256) и ничего не платят.

**Deploy только через Docker, long polling — бот ничего наружу не
слушает, входящие порты не нужны.**

## Возможности

- голосовые (`voice`), аудио (`audio`), кружки (`video_note` — задел на v2);
- кэш по sha256: повторный forward того же файла = 0 запросов к Groq, работает
  даже у пользователя без ключа;
- персональный ключ Groq на пользователя: `/key gsk_...`, шифрование Fernet,
  в логах/ответах только маска (`gsk_ab…56`);
- язык: `/lang ru|en|de|...` (по умолчанию — язык по умолчанию Groq);
- статистика: `/stats`;
- групповая политика: `off` / `mentions` / `all` (env `GROUP_POLICY`);
- лимиты: Bot API hard-limit 20 МБ (проверка до скачивания), Groq 25 МБ;
- события (save_key / cache_hit / transcribe / groq_error) в SQLite для отладки.

## Быстрый старт на любой VM (Docker + Compose v2)

```bash
git clone https://github.com/Jackwmtr/lazybtch-bot.git /opt/lazybtch-bot
cd /opt/lazybtch-bot
./deploy.sh
```

Скрипт проверит Docker, создаст `.env` (права 0600), сгенерирует
`ENCRYPTION_KEY`, спросит `BOT_TOKEN` (от @BotFather), соберёт образ и
запустит контейнер. Первый build — ~1–3 минуты.

Готово — бот уже поллит. Проверка: открыть бота в Telegram → `/start` →
`/key gsk_...` → переслать голосовое.

## Ручная установка (если не хочется скрипт)

```bash
git clone https://github.com/Jackwmtr/lazybtch-bot.git /opt/lazybtch-bot
cd /opt/lazybtch-bot
cp .env.example .env && chmod 600 .env
# в .env заполнить BOT_TOKEN, сгенерировать ENCRYPTION_KEY:
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
#   (или без cryptography: head -c 32 /dev/urandom | base64 | tr '+/' '-_' | cut -c1-43)
docker compose up -d --build
docker compose logs -f
```

### Переменные окружения (`.env`)

| Переменная | Обяз. | Описание |
|---|---|---|
| `BOT_TOKEN` | да | токен бота от @BotFather |
| `ENCRYPTION_KEY` | да | Fernet-ключ (32 байта, urlsafe base64). Генерация выше. **Если потеряется/сменится — старые сохранённые ключи Groq прочесть нельзя**; пользователи задают `/key` заново |
| `GROUP_POLICY` | нет | `off` (по умолчанию в `.env.example`) / `mentions` (реагировать на @mention и reply) / `all`. Для личных чатов не влияет |
| `GROQ_MODEL` | нет | по умолчанию `whisper-large-v3-turbo` |
| `CACHE_TTL_DAYS` | нет | сколько дней хранить кэш транскриптов, по умолчанию 30 |
| `MAX_FILE_MB` | нет | 20 (hard-limit Bot API на getFile) |
| `GROQ_MAX_FILE_MB` | нет | 25 (лимит Groq) |
| `LOG_LEVEL` | нет | `INFO` |

## Как пользоваться ботом

| Команда | Что делает |
|---|---|
| `/start` | онбординг + текущий статус (ключ задан/нет) |
| `/key gsk_...` | сохранить свой ключ Groq (ответит маской) |
| `/key off` | удалить ключ |
| `/key` | показать сохранённый ключ (маска) |
| `/lang ru` | язык транскрипции (любой код языка whisper) |
| `/stats` | счётчики: транскрипты, кэш hit/miss, ошибки, последний запрос |
| `/help` | справка |
| forward голосового/кружка | транскрипция, ответ — reply к исходному сообщению |

## Обновление

```bash
cd /opt/lazybtch-bot
git pull
docker compose up -d --build
```

Данные (БД, кэш) живут в `./data` на хосте и переживают пересборку.

## Безопасность

- ключи Groq: в SQLite только Fernet-блобы; в ответах, логах и таблицах
  `events` — только маска;
- аудио: в памяти (bytes), на диск не кладётся, в Groq уходит только
  `multipart`-тело запроса;
- `data/lazybtch.db` — права 0600; `.env` — 0600;
- в контейнер не публикуется ни один порт (long polling);
- в `.env.example` нет секретов; `.env` в git не попадает (`.gitignore`).

## Разработка (локально, без Docker)

```bash
uv sync --dev          # venv + зависимости
uv run lazybtch        # запуск с .env рядом (BOT_TOKEN, ENCRYPTION_KEY)
uv run pytest          # 33 теста
```

## Структура

```
src/lazybtch/
  config.py    — env-конфиг (pydantic-settings)
  crypto.py    — Fernet vault для ключей + маска
  store.py     — SQLite: users / transcripts (sha256 кэш) / events
  groq.py      — Groq API клиент (mp3/mp4/ogg, лимиты, маппинг ошибок)
  handlers.py  — aiogram-хендлеры: /start /key /lang /stats + audio-пайплайн
  bot.py       — сборка бота, heartbeat
  __main__.py  — точка входа: логирование, TTL-cleanup, long polling
tests/         — 33 теста (oga→ogg, лимиты, кэш, Fernet, политики)
```

## Устранение неполадок

| Симптом | Решение |
|---|---|
| `docker: 'compose' is not a docker command` | Ubuntu: `apt-get install docker-compose-v2`; docker.com репозиторий: `apt-get install docker-compose-plugin` |
| `docker: permission denied` | добавить юзера в группу `docker` (или работать от root) |
| бот молчит в группе | `GROUP_POLICY` (по умолчанию в `.env.example` — `off`); поставить `mentions` |
| «Похоже, что это файл больше 25 МБ» | лимит Groq; укоротить запись |
| «Не удалось скачать файл (превышен лимит 20 МБ)» | hard-limit Bot API getFile |
| «Некорректный ключ Groq» | проверить токен на https://console.groq.com/keys |
| логи | `docker compose logs -f` |
| пересоздать БД | остановить контейнер, удалить `data/lazybtch.db*` |
