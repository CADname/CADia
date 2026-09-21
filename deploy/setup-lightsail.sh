#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/setup-lightsail.sh"
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

apt-get update
apt-get install -y ca-certificates curl gnupg nginx certbot python3-certbot-nginx openssl

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --batch --yes --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

if [[ ! -f .env ]]; then
  cp .env.example .env
  sed -i "s/CHANGE_ME_WITH_OPENSSL_RAND_HEX_32/$(openssl rand -hex 32)/" .env
  session_secret="$(openssl rand -base64 48 | tr -d '\n')"
  sed -i "s#CHANGE_ME_WITH_OPENSSL_RAND_BASE64_48#${session_secret}#" .env
fi

if grep -q "CHANGE_ME_" .env; then
  echo "Refusing to deploy: .env still contains placeholder secrets."
  exit 1
fi

# First boot is intentionally HTTP-by-IP.  Detect the server's public IPv4 so
# session cookies, TrustedHost, CORS, and WebSocket origin checks all agree.
public_ip="$(curl -4fsS --max-time 8 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
if [[ "$public_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  if grep -q '^NEXIS_COOKIE_SECURE=' .env; then sed -i 's/^NEXIS_COOKIE_SECURE=.*/NEXIS_COOKIE_SECURE=false/' .env; else echo 'NEXIS_COOKIE_SECURE=false' >> .env; fi
  if grep -q '^NEXIS_CORS_ORIGINS=' .env; then sed -i "s#^NEXIS_CORS_ORIGINS=.*#NEXIS_CORS_ORIGINS=http://${public_ip}#" .env; else echo "NEXIS_CORS_ORIGINS=http://${public_ip}" >> .env; fi
  if grep -q '^NEXIS_ALLOWED_HOSTS=' .env; then sed -i "s#^NEXIS_ALLOWED_HOSTS=.*#NEXIS_ALLOWED_HOSTS=${public_ip},localhost,127.0.0.1#" .env; else echo "NEXIS_ALLOWED_HOSTS=${public_ip},localhost,127.0.0.1" >> .env; fi
fi
chmod 600 .env

install -m 0644 deploy/nginx/cadia.conf /etc/nginx/sites-available/cadia
ln -sfn /etc/nginx/sites-available/cadia /etc/nginx/sites-enabled/cadia
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

docker compose build --pull
docker compose up -d
docker compose ps

# Fail deployment immediately if the Codex binary/App Server is not usable.
docker compose exec -T app /usr/local/bin/codex --version
docker compose exec -T app /usr/local/bin/codex app-server --help >/dev/null
docker compose exec -T app python -m standalonecad_web.codex_preflight

health="$(curl -fsS http://127.0.0.1:8000/api/health)"
echo "$health"
echo
if [[ -n "$public_ip" ]]; then
  echo "CADia Hackathon Edition is running at: http://${public_ip}"
else
  echo "CADia Hackathon Edition is running on HTTP port 80."
fi
echo "For app.cadia.co.kr + HTTPS: sudo bash deploy/enable-hackathon-domain.sh app.cadia.co.kr"
