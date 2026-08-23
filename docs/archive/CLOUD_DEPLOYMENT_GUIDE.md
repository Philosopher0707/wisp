# Wisp Cloud Deployment Guide

Complete guide for deploying Wisp on a VPS to use with the Android app remotely.

---

## Recommended Setup: Docker Compose on VPS

This is the most reliable and full-featured option — Wisp server + Ollama run together in containers.

### VPS Providers (ranked by value)

| Provider | Specs | Cost | Best For |
|----------|-------|------|----------|
| **Hetzner Cloud** | 4 vCPU, 8GB RAM | ~€6/month | Best price/performance |
| **DigitalOcean** | 4 vCPU, 8GB RAM | ~$24/month | Simple, good docs |
| **Linode** | 4 vCPU, 8GB RAM | ~$24/month | Reliable support |
| **AWS Lightsail** | 4 vCPU, 8GB RAM | ~$20/month | Already using AWS |

> **Minimum:** 4GB RAM for small models. **8GB+** recommended for `deepseek-v4-flash:cloud`.

---

## Step-by-Step Deployment

### 1. Provision VPS & Install Docker

```bash
ssh root@your-vps-ip
apt update && apt install -y docker.io docker-compose git
```

### 2. Clone Wisp

```bash
git clone https://github.com/your-username/wisp.git
cd wisp
```

### 3. Set API Key

```bash
export WISP_API_KEY="wisp-$(openssl rand -hex 16)"
echo "WISP_API_KEY=$WISP_API_KEY" > .env
echo "Your API key: $WISP_API_KEY"
```

**Save this key** — you'll need it in the Android app.

### 4. Start Services

```bash
docker-compose up -d
```

This starts:
- `wisp-server` on port `8000`
- `ollama` on port `11434` (internal only, not exposed to internet)

### 5. Pull a Model

```bash
docker exec -it wisp-ollama ollama pull deepseek-v4-flash:cloud
```

Other options:
```bash
docker exec -it wisp-ollama ollama pull kimi-k2.5:cloud
docker exec -it wisp-ollama ollama pull kimi-k2.6:cloud
```

### 6. Verify Server

```bash
curl http://localhost:8000/
# Expected: {"service":"wisp-cloud","version":"0.1.0"}
```

---

## TLS / HTTPS Setup (Required for Production)

Without TLS, the Android app must use `ws://` (unencrypted). With TLS, use `wss://` (encrypted).

### Option A: Nginx + Let's Encrypt (Traditional)

**1. Point your domain to the VPS IP**

**2. Get a certificate**

```bash
certbot certonly --standalone -d wisp.yourdomain.com
```

**3. Uncomment nginx in `docker-compose.yml`**

```yaml
  nginx:
    image: nginx:alpine
    container_name: wisp-nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certbot/conf:/etc/letsencrypt:ro
      - ./certbot/www:/var/www/certbot:ro
    depends_on:
      - wisp
    restart: unless-stopped
```

**4. Update `nginx.conf`** with your domain and certificate paths.

**5. Restart**

```bash
docker-compose up -d
```

### Option B: Cloudflare Tunnel (Easiest — No Certificate Management)

Best for quick setup without dealing with certificates.

```bash
# Install cloudflared
dpkg -i cloudflared-linux-amd64.deb

# Create a tunnel
cloudflared tunnel create wisp-server

# Route traffic
cloudflared tunnel route dns wisp-server wisp.yourdomain.com

# Run tunnel (points to local Wisp server)
cloudflared tunnel run --url http://localhost:8000 wisp-server
```

Cloudflare automatically provides HTTPS. Android app uses `wss://wisp.yourdomain.com`.

### Option C: Built-in Auto-TLS (Simplest)

If you don't want nginx or Cloudflare, use a reverse proxy like **Caddy**:

```bash
# Install Caddy
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install caddy

# Caddyfile
cat > /etc/caddy/Caddyfile << 'EOF'
wisp.yourdomain.com
reverse_proxy localhost:8000
EOF

systemctl restart caddy
```

Caddy automatically obtains and renews Let's Encrypt certificates.

---

## Android App Configuration

Open the app → **Settings** tab:

| Field | Without TLS | With TLS |
|-------|-------------|----------|
| **Server URL** | `ws://your-vps-ip:8000` | `wss://wisp.yourdomain.com` |
| **API Key** | Your `WISP_API_KEY` | Your `WISP_API_KEY` |
| **Model** | Optional override | Optional override |

Tap **Connect**. The top-bar indicator turns **green** when connected.

---

## Quick Testing Alternative: Local Tunnel

If you don't want a VPS yet, expose your local machine temporarily:

### ngrok

```bash
# Terminal 1: Start Wisp server
wisp server --host 0.0.0.0 --port 8000

# Terminal 2: Tunnel
ngrok http 8000
# Gives you: https://abc123.ngrok.io
```

Android app: `wss://abc123.ngrok.io`

> ⚠️ Free ngrok URLs change on every restart. Use a VPS for permanent access.

### Cloudflare Tunnel (Local)

```bash
cloudflared tunnel --url http://localhost:8000
# Gives you: https://random.trycloudflare.com
```

---

## Security Checklist

- [ ] Use `wss://` (TLS) in production — never `ws://` over the internet
- [ ] Use a strong API key (32+ random characters)
- [ ] Restrict firewall to your IP when possible:
  ```bash
  ufw allow from YOUR_IP to any port 8000
  ```
- [ ] Ollama is **not exposed externally** (only internal Docker network)
- [ ] Dangerous commands blocked at API and agent layers
- [ ] File access sandboxed to `WISP_WORKSPACE`
- [ ] Bash commands timeout after 60 seconds

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Connection failed" | Check firewall (`ufw allow 8000`), verify URL uses `ws://` or `wss://` correctly |
| "Invalid API key" | Key must match exactly between `.env` file and Android app |
| Model not found | SSH into VPS and run `docker exec -it wisp-ollama ollama pull <model>` |
| WebSocket drops | Mobile networks kill idle connections — app auto-reconnects with backoff |
| Cleartext blocked | For local `ws://`, `AndroidManifest.xml` has `usesCleartextTraffic="true"` |
| Server won't start | Check `docker-compose logs wisp` for errors |

---

## My Recommendation by Use Case

| Use Case | Setup | Cost |
|----------|-------|------|
| **Permanent daily use** | Hetzner CPX21 + Docker Compose + Cloudflare Tunnel | ~€6/month |
| **Quick testing** | Local machine + ngrok / Cloudflare Tunnel | Free |
| **Already have Ollama** | Wisp server only (set `OLLAMA_HOST` to external URL) | VPS only |
| **Enterprise / Team** | VPS + Nginx + Let's Encrypt + custom domain | ~$20-40/month |

---

*Last updated: after session compaction + recall tool implementation.*
