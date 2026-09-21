#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/enable-https.sh cad.example.com"
  exit 1
fi
if [[ $# -ne 1 ]]; then
  echo "Usage: sudo bash deploy/enable-https.sh cad.example.com"
  exit 2
fi

domain="$1"
if [[ ! "$domain" =~ ^[A-Za-z0-9.-]+$ ]] || [[ "$domain" != *.* ]]; then
  echo "Invalid domain: $domain"
  exit 2
fi
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

python3 - "$domain" <<'PY2'
from pathlib import Path
import sys
p=Path('.env')
domain=sys.argv[1]
lines=p.read_text().splitlines()
updates={
 'NEXIS_COOKIE_SECURE':'true',
 'NEXIS_CORS_ORIGINS':f'https://{domain}',
 'NEXIS_ALLOWED_HOSTS':f'{domain},localhost,127.0.0.1',
}
out=[]; seen=set()
for line in lines:
    key=line.split('=',1)[0] if '=' in line and not line.lstrip().startswith('#') else None
    if key in updates:
        out.append(f'{key}={updates[key]}'); seen.add(key)
    else:
        out.append(line)
for key,val in updates.items():
    if key not in seen: out.append(f'{key}={val}')
p.write_text('\n'.join(out)+'\n')
PY2

python3 - "$domain" <<'PY2'
from pathlib import Path
import sys
p=Path('/etc/nginx/sites-available/standalonecad')
s=p.read_text()
s=s.replace('server_name _;', f'server_name {sys.argv[1]};')
p.write_text(s)
PY2

nginx -t
systemctl reload nginx
docker compose up -d --force-recreate app
certbot --nginx -d "$domain" --redirect
nginx -t
systemctl reload nginx

echo "HTTPS enabled: https://${domain}"
