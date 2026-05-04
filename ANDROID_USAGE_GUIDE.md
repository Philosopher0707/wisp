# Wisp Android App — Usage Guide

This guide covers how to build, install, and use the Wisp Android app to control your coding agent remotely.

---

## Prerequisites

- Android Studio Hedgehog (2023.1.1) or newer
- JDK 17
- Android SDK 34
- A running Wisp Cloud server (local or remote)

---

## Option A: Local Network (Same WiFi)

The quickest way to test — your phone and computer share the same network.

### 1. Start the server on your computer

```bash
cd wisp
export WISP_API_KEY="your-secret-key-123"
pip install -e "."
wisp server --host 0.0.0.0 --port 8000
```

The server prints the API key on startup if you didn't set one.

### 2. Build the debug APK

```bash
cd android
./gradlew assembleDebug
```

Output: `android/app/build/outputs/apk/debug/app-debug.apk`

### 3. Install on your phone

- Enable **Developer Options** → **USB Debugging** on your Android device, **OR**
- Transfer the APK via Bluetooth / AirDrop / USB cable / file sharing app
- Tap the APK to install (allow "Install from unknown sources" if prompted)

### 4. Configure the app

Open the app and go to the **Settings** tab:

| Field | Value |
|-------|-------|
| **Server URL** | `ws://YOUR_COMPUTER_IP:8000` |
| **API Key** | `your-secret-key-123` |
| **Model** (optional) | e.g. `deepseek-v4-flash:cloud` |

Find your computer's IP:
- **macOS/Linux**: `ifconfig` or `ip addr`
- **Windows**: `ipconfig`
- Example: `ws://192.168.1.42:8000`

Tap **Connect**. The top-bar indicator turns **green** when connected.

### 5. Start using Wisp

Go to the **Chat** tab and type prompts like:

> "Refactor the auth module to use async/await"

Watch real-time thinking, tool calls, and approve/deny cards appear.

> ⚠️ Your phone and computer must be on the **same WiFi network**. The server binds to `0.0.0.0` to accept LAN connections.

---

## Option B: Cloud Server (Access from Anywhere)

Deploy to a VPS for 24/7 remote access.

### 1. Deploy the server

```bash
# On your VPS (Ubuntu/Debian example)
git clone <your-wisp-repo>
cd wisp
export WISP_API_KEY="a-strong-random-key-32-chars"
docker-compose up -d
```

This starts Wisp Server on port `8000` and Ollama internally.

### 2. (Recommended) Add TLS with Nginx

For secure `wss://` connections:

1. Point your domain to the VPS IP
2. Get a certificate (Let's Encrypt):
   ```bash
   certbot certonly --standalone -d your-domain.com
   ```
3. Uncomment the **nginx** service in `docker-compose.yml`
4. Update `nginx.conf` with your domain and certificate paths
5. Restart:
   ```bash
   docker-compose up -d
   ```

### 3. Build & install the APK

Same as Option A:

```bash
cd android
./gradlew assembleDebug
```

Transfer `app-debug.apk` to your phone and install.

### 4. Configure the app

Open **Settings**:

| Field | Value |
|-------|-------|
| **Server URL** | `wss://your-domain.com` (with TLS) or `ws://your-domain.com:8000` (without) |
| **API Key** | The same key you set on the server |
| **Model** (optional) | Override default if desired |

Tap **Connect**.

---

## Option C: Direct from Android Studio (Development)

Best for active development and debugging.

1. Open the `android/` folder in **Android Studio**
2. Connect your phone via USB (enable **USB Debugging**)
3. Click **Run** ▶️ — Android Studio builds, installs, and launches automatically
4. Configure server URL and API key in the app's **Settings** tab

---

## Using the App

### Chat Screen
- Type a prompt and tap the send button
- Watch Wisp's **thinking** stream in real time
- When Wisp wants to run a tool, an **Approve / Deny** card appears
- Tap **Approve** to let it execute, **Deny** to skip
- Tap the **⏹ Stop** button while Wisp is thinking to send an interrupt

### Files Screen
- Browse the server's workspace directory tree
- Tap **folders** to navigate deeper
- Tap **files** to view their contents in the side panel
- Use the **back arrow** in the top bar to go up a directory

### Settings Screen
- **Server URL**: WebSocket endpoint (`ws://` or `wss://`)
- **API Key**: Pre-shared key from server logs / env var
- **Default Model**: Optional model override
- **Connection status**: Shows Connected / Connecting / Error / Reconnecting…
- **Save Settings**: Persists to DataStore (encrypted preferences)
- **Connect / Disconnect**: Manual control

---

## Troubleshooting

| Problem | Cause & Fix |
|---------|-------------|
| "Connection failed" | Wrong IP, firewall blocking port 8000, or not on same WiFi. Check `ufw allow 8000` on the server. |
| "Invalid API key" | Key mismatch. Copy the exact key from server startup logs. |
| App won't install | Enable **Install unknown apps** for your file manager / browser in Android settings. |
| WebSocket drops frequently | Mobile networks kill idle connections. The app auto-reconnects (up to 10 attempts with exponential backoff). |
| Server not reachable from phone | Server must bind to `0.0.0.0` (not `127.0.0.1`). Use `--host 0.0.0.0`. |
| Cleartext blocked | For local `ws://` without TLS, `AndroidManifest.xml` has `android:usesCleartextTraffic="true"`. |
| Ollama model not found | SSH into the server and run `ollama pull <model-name>`. |
| Markdown not rendering | Ensure you're on the latest `main` branch — Markdown support was added recently. |

---

## Security Checklist

- [ ] Use `wss://` (TLS) in production — never `ws://` over the internet
- [ ] Use a strong API key (32+ random characters)
- [ ] Restrict server firewall to your IP when possible:
  ```bash
  ufw allow from YOUR_IP to any port 8000
  ```
- [ ] The server blocks dangerous commands (`rm -rf /`, `mkfs`, etc.) at API and agent layers
- [ ] File paths are sandboxed to `WISP_WORKSPACE` — clients cannot escape
- [ ] Bash commands timeout after 60 seconds

---

## Building a Release APK

For distribution outside the Play Store:

```bash
cd android
./gradlew assembleRelease
```

Output: `app/build/outputs/apk/release/app-release.apk`

> Note: The release build uses ProGuard rules defined in `app/proguard-rules.pro`. Minification is currently disabled (`isMinifyEnabled = false`) for easier debugging.

---

## Architecture Reminder

```
┌─────────────────┐      WebSocket / HTTPS      ┌─────────────────────────────┐
│   Android App   │  ◄──────────────────────►  │   Cloud / Local Server        │
│  (Jetpack       │                           │  ┌─────────────────────┐      │
│   Compose)      │                           │  │  Wisp Server        │      │
│                 │                           │  │  (FastAPI + Agent)  │      │
│  • Chat UI      │                           │  └──────────┬──────────┘      │
│  • File tree    │                           │             │                 │
│  • Tool         │                           │  ┌──────────▼──────────┐      │
│    approvals    │                           │  │  Ollama / External  │      │
│  • Settings     │                           │  │  LLM API            │      │
└─────────────────┘                           │  └─────────────────────┘      │
                                              └─────────────────────────────┘
```

---

*Last updated: after fixing all 10 Android/server issues (commit `237d17b`).*
