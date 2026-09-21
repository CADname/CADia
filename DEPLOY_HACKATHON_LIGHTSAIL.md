# Deploy CADia Hackathon Edition on AWS Lightsail

These instructions assume the final submission source archive has been uploaded to `/home/ubuntu`. Replace `<ARCHIVE>.tgz` below with the actual downloaded archive name. If deploying from the GitHub repository instead, start from the checked-out repository root and continue at step 2.

## 1. Unpack

```bash
cd /home/ubuntu
tar -xzf <ARCHIVE>.tgz
cd CADia
```

## 2. First server boot by IP

```bash
sudo bash deploy/setup-lightsail.sh
```

This installs Docker, Nginx, Certbot, builds the app, starts PostgreSQL + CADia, and checks Codex App Server availability.

## 3. Point DNS

In DNS, create an A record:

```text
app.cadia.co.kr  ->  <new Lightsail static IP>
```

Use a Lightsail Static IP. Do not rely on the temporary public IP.

## 4. Enable domain + HTTPS

After DNS resolves:

```bash
sudo bash deploy/enable-hackathon-domain.sh app.cadia.co.kr
```

This sets:

```text
NEXIS_COOKIE_SECURE=true
NEXIS_CORS_ORIGINS=https://app.cadia.co.kr
NEXIS_ALLOWED_HOSTS=app.cadia.co.kr,localhost,127.0.0.1
GOOGLE_OAUTH_REDIRECT_URI=https://app.cadia.co.kr/api/auth/google/callback
GITHUB_OAUTH_REDIRECT_URI=https://app.cadia.co.kr/api/ai/copilot/callback
NEXIS_MAX_CODEX_PROCESSES=8
NEXIS_MAX_CONCURRENT_AI_TURNS=2
```

## 5. OAuth console settings

Google OAuth authorized redirect URI:

```text
https://app.cadia.co.kr/api/auth/google/callback
```

GitHub OAuth callback URL:

```text
https://app.cadia.co.kr/api/ai/copilot/callback
```

ChatGPT/Codex does not use your app OAuth callback. It uses OpenAI device login from the Codex App Server flow.

## 6. Health checks

```bash
curl -fsS https://app.cadia.co.kr/api/health
sudo docker compose ps
sudo docker compose logs --tail=120 app
```

Expected health response:

```json
{"ok":true,"service":"cadia-web","cad_kernel":"CadQuery/OCP"}
```

## 7. Judge flow check

Open:

```text
https://app.cadia.co.kr
```

Then test:

1. Click **Launch CADia**.
2. Confirm it opens `Hackathon Starter Workspace`.
3. Click **Connect AI**.
4. Choose **ChatGPT / Codex**.
5. Complete OpenAI device login.
6. Run the spur gear sample prompt.
7. Test Face / Edge / Object selection.
8. Export STEP.
