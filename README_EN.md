# DeskBeam

Browser-based Windows desktop streaming and remote control.

Two deployment modes:
- **Full**: desktop streaming (H.264) + remote control (mouse/keyboard/text/voice)
- **Remote-only**: mouse/keyboard/text/voice — no GPU or screen capture deps

Hardware H.264 encoding via NVENC/QSV/AMF. Touchpad, mouse, keyboard, text input, voice-to-text. Deploy as Python source or compile to a single portable exe.

<p align="center">
  <img src="docs/screen.jpg" width="45%" alt="SCREEN mode">
  <img src="docs/remote.jpg" width="45%" alt="REMOTE mode">
</p>

---

## 1. Requirements

| Requirement | Full | Remote-only |
|-------------|:---:|:---:|
| Windows 10+ | Yes | Yes |
| Python 3.10+ | Yes | Yes |
| GPU (NVENC/AMF/QSV) | Recommended | No |
| Chromium browser 94+ | Yes | Recommended |
| WSL (voice recognition) | Optional | Optional |
| openssl (TLS cert) | Yes | Yes |

---

## 2. Setup — Full Mode

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Generate self-signed cert
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes -subj "/CN=localhost"

copy config.example.json config.json
.venv\Scripts\python server.py
```

> **Firewall**: Windows may block inbound connections. Allow LAN access:
> ```powershell
> New-NetFirewallRule -Name "DeskBeam" -DisplayName "DeskBeam" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 8769
> ```

Open `https://<lan-ip>:8769` in Chrome/Edge. Accept the certificate warning.

> **No-SSL mode**: If `cert.pem` and `key.pem` are missing, DeskBeam falls back to plain HTTP (browser microphone unavailable).

---

## 3. Setup — Remote-only

### Option A: Python Source

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-remote.txt
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes -subj "/CN=localhost"
copy config.example.json config.json
.venv\Scripts\python server_remote.py
```

### Option B: Standalone EXE

Compile into a single `DeskBeamRemote.exe` — no Python installation needed:

```powershell
build_remote.bat
```

Deploy these 4 files:

```
├── DeskBeamRemote.exe     # double-click to start
├── config.json             # edit before deploying
├── cert.pem                # generate with openssl
└── key.pem
```

Runs hidden (no terminal). How to stop:
- `stop.bat` (double-click)
- Task Manager → end `DeskBeamRemote.exe`
- Browser: click status text (`LIVE`) → choose `Shutdown`

---

## 4. Login / Logout

Click the status text (`LIVE` / `RETRY` / `CONNECTING`) in the top-left corner:

- **Logout** — clear session cookie, return to login
- **Shutdown** — kill DeskBeam process
- **Cancel** — dismiss dialog

---

## 5. Run as Background Service

```powershell
start.vbs      # hidden + UAC elevation
start.bat      # visible CMD for debugging
stop.bat       # kill server
```

---

## 6. Configuration

```json
{
    "port": 8769,
    "ssl_cert": "cert.pem",
    "ssl_key": "key.pem",
    "web_dir": "web",
    "token": "",
    "max_fps": 3,
    "streaming": true,
    "gop": 1,
    "wsl_asr_script": "~/scripts/asr.py",
    "asr_health_url": "http://127.0.0.1:8082/healthz",
    "asr_cooldown": 10
}
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `port` | int | 8769 | HTTPS/WSS port |
| `token` | str | `""` | Auth token; empty = no auth |
| `max_fps` | int | 3 | Maximum frame rate |
| `streaming` | bool | true | Enable screen streaming |
| `gop` | int | 1 | Keyframe interval |
| `wsl_asr_script` | str | `~/scripts/asr.py` | ASR script path in WSL |
| `asr_health_url` | str | `http://127.0.0.1:8082/healthz` | ASR health check URL |
| `asr_cooldown` | int | 10 | Seconds between ASR retries |

---

## 7. Browser Compatibility

| Browser | Remote | Screen |
|---------|:---:|:---:|
| Chrome / Edge 94+ | Yes | Yes (VideoDecoder) |
| Opera / Brave | Yes | Yes (VideoDecoder) |
| Firefox | Yes | Yes (WebRTC) |
| Safari | Yes | Yes (WebRTC) |
| iOS browsers | Yes | Yes (WebRTC) |

---

## 8. GOP Tuning

Bandwidth at 2560×1440, CQ=26, static desktop:

| `gop` | Keyframe interval | Bandwidth | Use case |
|-------|-------------------|-----------|----------|
| 1 | every frame | ~23 Mbps | LAN, zero latency |
| 15 | every 0.5s @30fps | ~6 Mbps | Cloud 15M server |
| 30 | every 1s @30fps | ~5 Mbps | Cloud 30M server |
| 60 | every 2s @30fps | ~3 Mbps | Cloud 3M server |

P-frames drop to ~200 bytes on static content.

---

## 9. WebRTC Fallback

Auto-switches to WebRTC when the browser doesn't support `VideoDecoder` (Firefox/Safari). Requires `aiortc`:

```powershell
pip install aiortc
```

WebRTC uses the browser's native H.264 decoder. Shares the same screen capture + encoding pipeline as VideoDecoder mode.

---

## 10. Voice Recognition (Optional)

Two modes: online API or local WSL ASR. **Online takes priority** when API key is configured.

### Option A: Online API (Recommended)

```json
// config.json
"asr_api_url": "https://api.xiaomimimo.com/v1/chat/completions",
"asr_api_key": "your-api-key"
```

Compatible with OpenAI-format speech recognition APIs (e.g., Xiaomi MiMo ASR). Audio is sent as base64, transcription is typed into the focused window.

### Option B: WSL Local Model

Requires WSL and a running ASR model server (default port 8082). The project includes `asr.py`:

```bash
# In WSL
cp asr.py ~/scripts/
# Start ASR model server on 127.0.0.1:8082
```

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `asr_api_url` | `""` | Online API URL (empty = local) |
| `asr_api_key` | `""` | Online API key |
| `wsl_asr_script` | `~/scripts/asr.py` | WSL ASR script path |
| `asr_health_url` | `http://127.0.0.1:8082/healthz` | WSL health check URL |
| `asr_cooldown` | `10` | WSL retry interval (seconds) |

---

## 11. Architecture

```
Browser                                     Python server
┌──────────────────────┐               ┌─────────────────────────┐
│ canvas + VideoDecoder │◄── WSS (H.264)│ mss → screen capture   │
│ touchpad / mouse      │──► WSS JSON   │ numpy → cursor overlay  │
│ keyboard shortcuts    │   (control)   │ PyAV → H.264 encode    │
│ text input / voice    │──► WSS (WAV)  │   NVENC/QSV/AMF/x264   │
└──────────────────────┘               │ keyboard / ctypes       │
                                       │ WSL → ASR (voice)      │
                                       └─────────────────────────┘
```

---

## 12. Security

| Layer | Mechanism |
|-------|-----------|
| Transport | TLS 1.2+ WSS encryption |
| Auth | Token cookie (HttpOnly + SameSite=Strict), 5 fails → 24h block |
| Session | 24h server-side expiry |
| Path traversal | `relative_to()` sandbox |
| Logging | `audit.log` records login/logout/connection events |
| Secrets | `config.json`, `cert.pem`, `key.pem`, `*.ps1`, `audit.log` git-ignored |

### Self-signed Cert

Encrypts traffic but the browser cannot verify identity. First visit shows a certificate warning — this is expected.

### ARP Spoofing Risk

On LAN, an attacker can redirect traffic + present a fake cert → full MITM (desktop contents, keystrokes, voice captured).

**Mitigations:**
1. Compare cert fingerprint after generation: `openssl x509 -in cert.pem -noout -sha256 -fingerprint`
2. Use Let's Encrypt with a real domain
3. Static ARP binding: `arp -s <gateway-ip> <gateway-mac>`
4. Do not expose on untrusted networks

---

## 13. File Structure

```
├── server.py              # Main server (full mode)
├── server_remote.py       # Remote-only (for exe builds)
├── asr.py                 # Voice-to-text script (WSL, needs ASR server)
├── webrtc_streamer.py     # WebRTC fallback for Firefox/Safari
├── encoder.py             # H.264 encoder (PyAV)
├── requirements.txt       # Full mode deps
├── requirements-remote.txt
├── build_remote.bat       # PyInstaller build script
├── config.example.json
├── start.vbs / start.bat  # Launchers
├── stop.bat               # Kill server
├── icon.ico / icon.png    # App icon
├── .gitignore
├── LICENSE
├── docs/                  # Screenshots
└── web/
    ├── index.html         # Browser UI
    └── login.html         # Auth page
```

---

## 14. Disclaimer

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND. Misconfiguration (weak token, untrusted network, leaked secrets) may lead to unauthorized access, data loss, or other damages. The authors assume no liability. Use at your own risk.

This software provides full access to the controlled computer. Only use it on machines you own or are legally authorized to access. Compliance with local laws and regulations is your sole responsibility. See [MIT License](LICENSE) for details.

---

## 15. License

MIT — see [LICENSE](LICENSE)
