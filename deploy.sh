#!/usr/bin/env bash
# One-command deploy of LazyBTch Bot on any Linux VM with Docker.
#
#   git clone https://github.com/Jackwmtr/lazybtch-bot.git /opt/lazybtch-bot
#   cd /opt/lazybtch-bot && ./deploy.sh
#
# The script: checks docker, creates .env (0600) from .env.example,
# generates ENCRYPTION_KEY if empty, asks for BOT_TOKEN if empty,
# builds and starts the container (long polling — no ports exposed).
set -euo pipefail
cd "$(dirname "$0")"

info() { echo "[lazybtch] $*"; }
fail() { echo "[lazybtch] ERROR: $*" >&2; exit 1; }

# --- 1. prerequisites -------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "docker not found. Install: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 missing. Ubuntu: apt-get install docker-compose-v2 (or docker-compose-plugin on docker.com repos)"
docker info >/dev/null 2>&1 || fail "docker daemon not reachable (is it running? do you have permission?)"
command -v git >/dev/null 2>&1 || fail "git not found"

# --- 2. .env ----------------------------------------------------------------
if [ ! -f .env ]; then
  info "creating .env from .env.example"
  cp .env.example .env
  chmod 600 .env
fi
chmod 600 .env

# generate Fernet key if empty (32 random bytes, urlsafe base64)
set_envar() { sed -i "s|^$1=.*|$1=$2|" .env; }

if [ -z "$(grep '^ENCRYPTION_KEY=' .env | cut -d= -f2-)" ]; then
  KEY=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | cut -c1-43)
  set_envar ENCRYPTION_KEY "$KEY"
  info "generated ENCRYPTION_KEY"
fi

# ask for bot token if empty
if [ -z "$(grep '^BOT_TOKEN=' .env | cut -d= -f2-)" ]; then
  read -rp "Enter bot token from @BotFather (input hidden): " TOKEN
  [ -n "$TOKEN" ] || fail "BOT_TOKEN is empty — aborting"
  set_envar BOT_TOKEN "$TOKEN"
  info "saved BOT_TOKEN"
fi

GROUP_POLICY_DEFAULT="mentions"
if [ -z "$(grep '^GROUP_POLICY=' .env | cut -d= -f2-)" ]; then
  set_envar GROUP_POLICY "$GROUP_POLICY_DEFAULT"
fi

# --- 3. build & start ---------------------------------------------------------
info "building and starting container (first build ~1-3 min)"
docker compose up -d --build

# --- 4. status -----------------------------------------------------------------
sleep 5
info "container status:"
docker compose ps
echo
info "recent logs:"
docker compose logs --tail 10
echo
info "done. Test it: open @lazybtch_bot in Telegram, /start, /key gsk_..., forward a voice note."
info "logs: docker compose logs -f"
