#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-app.cadia.co.kr}"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/enable-hackathon-domain.sh app.cadia.co.kr"
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -f .env ]]; then
  echo "ERROR: $project_dir/.env not found. Run sudo bash deploy/setup-lightsail.sh first." >&2
  exit 2
fi

public_ip="$(curl -4fsS --max-time 8 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
dns_ip="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"

if [[ -z "$public_ip" || ! "$public_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: Could not determine this Lightsail server public IPv4." >&2
  exit 3
fi
if [[ -z "$dns_ip" ]]; then
  echo "ERROR: $DOMAIN does not resolve yet." >&2
  echo "Set DNS A record: $DOMAIN -> $public_ip, wait for propagation, then retry." >&2
  exit 4
fi
if [[ "$dns_ip" != "$public_ip" ]]; then
  echo "ERROR: DNS mismatch. $DOMAIN -> $dns_ip, but this server -> $public_ip" >&2
  echo "Set DNS A record: $DOMAIN -> $public_ip, wait for propagation, then retry." >&2
  exit 5
fi

python3 - "$DOMAIN" <<'PY'
from pathlib import Path
import sys
p=Path('.env')
domain=sys.argv[1]
updates={
    'NEXIS_COOKIE_SECURE':'true',
    'NEXIS_CORS_ORIGINS':f'https://{domain}',
    'NEXIS_ALLOWED_HOSTS':f'{domain},localhost,127.0.0.1',
    'GOOGLE_OAUTH_REDIRECT_URI':f'https://{domain}/api/auth/google/callback',
    'GITHUB_OAUTH_REDIRECT_URI':f'https://{domain}/api/ai/copilot/callback',
    'NEXIS_MAX_CODEX_PROCESSES':'8',
    'NEXIS_MAX_CONCURRENT_AI_TURNS':'2',
}
lines=p.read_text().splitlines()
out=[]; seen=set()
for line in lines:
    key=line.split('=',1)[0] if '=' in line and not line.lstrip().startswith('#') else None
    if key in updates:
        out.append(f'{key}={updates[key]}'); seen.add(key)
    else:
        out.append(line)
for key,val in updates.items():
    if key not in seen:
        out.append(f'{key}={val}')
p.write_text('\n'.join(out)+'\n')
PY
chmod 600 .env

# Render nginx config for the selected domain.
tmp_conf="/tmp/cadia-hackathon-nginx.conf"
cp deploy/nginx/app.cadia.co.kr.conf "$tmp_conf"
sed -i "s/server_name app.cadia.co.kr;/server_name ${DOMAIN};/" "$tmp_conf"
install -m 0644 "$tmp_conf" "/etc/nginx/sites-available/${DOMAIN}"
ln -sfn "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"
rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/standalonecad /etc/nginx/sites-enabled/cadia.co.kr /etc/nginx/sites-enabled/cad.nexisai.tech
nginx -t
systemctl reload nginx

docker compose up -d --force-recreate app

if ! command -v certbot >/dev/null 2>&1; then
  apt-get update
  apt-get install -y certbot python3-certbot-nginx
fi
certbot --nginx -d "$DOMAIN" --redirect
nginx -t
systemctl reload nginx

echo "CADIA_HACKATHON_DOMAIN_ENABLED"
echo "URL: https://$DOMAIN"
echo "Google OAuth redirect: https://$DOMAIN/api/auth/google/callback"
echo "GitHub OAuth redirect: https://$DOMAIN/api/ai/copilot/callback"
