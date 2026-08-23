# Wisp Cloud + Android Setup Guide

This guide walks you through deploying Wisp on a cloud server and building the Android app to control it remotely.

## Architecture Overview

```
┌─────────────────┐      WebSocket/HTTPS      ┌─────────────────────────────┐
│   Android App   │  ◄──────────────────────►  │   Cloud VPS                 │
│  (Jetpack       │                           │  ┌─────────────────────┐    │
│   Compose)      │                           │  │  Wisp Server        │    │
│                 │                           │  │  (FastAPI + Agent)  │    │
│  • Chat UI      │                           │  └──────────┬──────────┘    │
│  • File tree    │                           │             │               │
│  • Tool         │                           │  ┌──────────▼──────────┐    │
│    approvals    │                           │  │  Ollama (optional)  │    │
│  • Settings     │                           │  │  or external API    │    │
└─────────────────┘                           │  └─────────────────────┘    │
                                              └─────────────────────────────┘
```

## Part 1: Deploy the Cloud Server

### Prerequisites

- A VPS with at least 2 CPU cores, 4GB RAM (8GB+ recommended for local Ollama)
- Docker + Docker Compose installed
- Domain name (optional, for TLS)
- API key for your LLM provider (if not using local Ollama)

### Option A: Docker Compose (Recommended)

1. **Clone Wisp and navigate to the project:**
   ```bash
   git clone <your-wisp-repo>
   cd wisp
   ```

2. **Set your API key:**
   ```bash
   export WISP_API_KEY="your-secure-random-key-here"
   ```
   Or create a `.env` file:
   ```
   WISP_API_KEY=your-secure-random-key-here
   ```

3. **Start the services:**
   ```bash
   docker-compose up -d
   ```
   This starts:
   - `wisp-server` on port 8000
   - `ollama` on port 11434 (internal only)

4. **Pull a model (if using local Ollama):**
   ```bash
   docker exec -it wisp-ollama ollama pull deepseek-v4-flash:cloud
   ```

5. **Verify the server:**
   ```bash
   curl http://localhost:8000/
   # Expected: {"service":"wisp-cloud","version":"0.1.0"}
   ```

### Option B: Manual Python Install

1. **Install Python 3.10+ and dependencies:**
   ```bash
   cd wisp
   pip install -e "."
   ```

2. **Set environment variables:**
   ```bash
   export WISP_API_KEY="your-key"
   export WISP_WORKSPACE="/var/wisp/workspace"
   export OLLAMA_HOST="http://localhost:11434"
   ```

3. **Run the server:**
   ```bash
   wisp server --host 0.0.0.0 --port 8000
   ```

### Option C: TLS with Nginx (Production)

1. **Get a certificate** (Let's Encrypt):
   ```bash
   certbot certonly --standalone -d your-domain.com
   ```

2. **Update `nginx.conf`** with your domain and certificate paths.

3. **Uncomment the nginx service** in `docker-compose.yml`.

4. **Restart:**
   ```bash
   docker-compose up -d
   ```

## Part 2: Build the Android App

### Prerequisites

- Android Studio Hedgehog (2023.1.1) or newer
- JDK 17
- Android SDK 34
- Kotlin 1.9+

### Setup

1. **Open the Android project:**
   ```bash
   cd wisp/android
   ```
   Open this folder in Android Studio.

2. **Sync Gradle.** If you see dependency errors, check that the Compose BOM version matches your Kotlin version.

3. **Configure the app:**
   - Open `app/src/main/java/com/wisp/app/MainActivity.kt`
   - The app uses DataStore for settings (server URL, API key)
   - No hardcoded credentials needed

4. **Build and run:**
   - Connect your Android device or start an emulator
   - Click **Run** in Android Studio

### APK Build (for distribution)

```bash
./gradlew assembleRelease
```

The APK will be at `app/build/outputs/apk/release/app-release.apk`.

## Part 3: Connect the App to Your Server

1. **Find your server address:**
   - If running locally: `ws://YOUR_PC_IP:8000`
   - If on VPS with TLS: `wss://your-domain.com`
   - If on VPS without TLS: `ws://your-domain.com:8000`

2. **Open the Android app** and go to **Settings** tab.

3. **Enter:**
   - **Server URL**: `wss://your-domain.com` (or `ws://...` for local)
   - **API Key**: The same `WISP_API_KEY` you set on the server
   - **Model** (optional): Override the default model

4. **Tap Connect.** The indicator in the top bar should turn green.

5. **Go to Chat** and start typing prompts!

## Part 4: Using the App

### Chat Screen
- Type a prompt and send
- Watch Wisp think in real-time
- When Wisp wants to run a tool, you'll see an **Approve/Deny** card
- Tap **Approve** to let it execute, **Deny** to skip

### Files Screen
- Browse the server's workspace directory
- Tap files to view contents
- Navigate folders by tapping them
- Use the back arrow to go up

### Settings Screen
- Server URL and API key
- Default model selection
- Connection status indicator

## Security Considerations

1. **Always use TLS in production** (wss://, not ws://)
2. **Use a strong API key** (32+ random characters)
3. **Restrict firewall rules**:
   ```bash
   # Only allow your IP
   ufw allow from YOUR_IP to any port 8000
   ```
4. **The server blocks dangerous commands** (`rm -rf /`, `mkfs`, etc.) at both the API and agent layers
5. **File paths are sandboxed** to `WISP_WORKSPACE` — clients cannot escape
6. **Bash commands timeout** after 60 seconds
7. **Rate limiting** is configured in nginx (10 req/s per IP)

## Troubleshooting

### Server won't start
```bash
docker-compose logs wisp
# Check if Ollama is reachable:
curl http://localhost:11434/api/tags
```

### Android app can't connect
- Check that the URL uses `ws://` for HTTP or `wss://` for HTTPS
- Verify the API key matches exactly
- Check `AndroidManifest.xml` has `android:usesCleartextTraffic="true"` for local dev
- Check that port 8000 is open on your server firewall

### Ollama model not found
```bash
docker exec -it wisp-ollama ollama list
docker exec -it wisp-ollama ollama pull <model-name>
```

### WebSocket disconnects
- The server sends ping/pong every 30s
- Check nginx `proxy_read_timeout` if using reverse proxy
- Some mobile networks kill idle WebSockets — this is handled by auto-reconnect in the app

## Advanced: Customizing the Server

### Using OpenAI/Anthropic instead of Ollama

Edit `wisp/ollama_client.py` to point to an OpenAI-compatible API, or set:
```bash
export OLLAMA_HOST="https://api.openai.com/v1"
```
(You may need to adapt the client for OpenAI's response format.)

### Multi-user support

The current server uses a single API key. For multi-user:
1. Add a user database (SQLite/PostgreSQL)
2. Replace `verify_api_key` with JWT or OAuth
3. Add user-specific workspace isolation

### Persistent sessions

Sessions are already persisted to disk (`~/.config/wisp/sessions/` inside the container). Mount a volume to preserve them across restarts:
```yaml
volumes:
  - wisp-sessions:/root/.config/wisp/sessions
```

## Development Roadmap

- [ ] Push notifications for long-running tasks
- [ ] File editing in the app (not just viewing)
- [ ] Git integration (commit, push, diff)
- [ ] Multi-workspace support
- [ ] Voice input
- [ ] Offline mode with on-device tiny LLM

## License

MIT — same as Wisp.
