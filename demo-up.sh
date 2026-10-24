#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

for file in .env .env.tunnel; do
  [[ -f $file ]] || { echo "missing $file; see README" >&2; exit 1; }
done
grep -qE '^NGROK_AUTHTOKEN=.+' .env.tunnel || { echo "add NGROK_AUTHTOKEN to .env.tunnel" >&2; exit 1; }
grep -qi '^APP_ENV=production$' .env || { echo "set APP_ENV=production in .env for the public demo" >&2; exit 1; }
grep -q '^PREVIEW_HOST=' .env || { echo "add PREVIEW_HOST to .env" >&2; exit 1; }

compose=(
  docker compose
  --env-file .env
  -f docker-compose.yml
  --profile ngrok --profile cloudflare-preview
)

"${compose[@]}" up -d --build

preview_url=""
for _ in {1..15}; do
  preview_url=$("${compose[@]}" logs --no-color cloudflared-preview 2>/dev/null \
    | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n1 || true)
  [[ -n $preview_url ]] && break
  sleep 2
done
[[ -n $preview_url ]] || { echo "preview URL not found; check cloudflared-preview logs" >&2; exit 1; }

if ! grep -q "^PREVIEW_HOST=$preview_url$" .env; then
  sed -i "s|^PREVIEW_HOST=.*|PREVIEW_HOST=$preview_url|" .env
  "${compose[@]}" up -d app worker
fi

echo "app:     $(grep '^PUBLIC_URL=' .env | cut -d= -f2-)"
echo "preview: $preview_url"
