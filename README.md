# Dual Video with Azure Speech (STT) + Translator (Proxy)

This package gives you:
- **Flask + Socket.IO** backend for WebRTC signaling and a secure proxy to **Azure Translator**.
- **Azure Speech STS** token endpoint so the browser uses the **Speech SDK** without exposing keys.
- **Static frontend** with two side-by-side videos and translated captions for each stream.

## 1) Prereqs

- Python 3.11+
- Azure resources:
  - **Translator** (key, region, endpoint like `https://<name>.cognitiveservices.azure.com`)
  - **Speech** (key, region)
- (Optional) A public host (Render/Fly/Azure App Service).

## 2) Configure env

Create a `.env` (or set env vars) based on `.env.example`:

```
TRANSLATOR_KEY=...
TRANSLATOR_REGION=eastus
TRANSLATOR_ENDPOINT=https://<your-translator>.cognitiveservices.azure.com

SPEECH_KEY=...
SPEECH_REGION=eastus

CORS_ALLOW_ORIGIN=http://localhost:5500
```

> **Security**: Rotate any keys that were shared in chats. Never expose keys to the browser.

## 3) Install & run backend locally

```bash
cd backend
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# load your .env into the environment (or export variables by hand)
# On Linux/macOS:
export $(grep -v '^#' ../.env | xargs)

# Run
python app.py
# server listens on 0.0.0.0:8000
```

> On Windows PowerShell, set env vars with `$env:NAME="value"` before running.

## 4) Open the frontend

You can open `frontend/index.html` directly with a simple static server. For example:

```bash
# in project root (one level above frontend)
python -m http.server 5500
```

Then open: `http://localhost:5500/frontend/index.html`

- Paste your backend URL in the top-left field (e.g., `http://localhost:8000` or your Render URL).
- Pick a room name (e.g., `room-1234`).
- Click **Join** in **two browsers/devices** and speak:
  - Left pane shows **your** raw captions (from Azure Speech).
  - Right pane shows **peer** translated captions they send you.
  - Each peer sees the other's translated lines under their *remote* video.

## 5) Deploy to Render

- Create a **Web Service** from the `backend` folder.
- **Build command**: `pip install -r requirements.txt`
- **Start command**: `python app.py`
- Add environment variables from `.env` in Render dashboard.
- Set `CORS_ALLOW_ORIGIN` to your site origin (e.g., `https://<user>.github.io`).

## 6) TURN (optional)

For strict NATs, add a TURN server to `RTCPeerConnection`'s `iceServers`.

## Notes

- The `/speech/token` endpoint issues an **STS token** via Azure Speech; token TTL is short (~10 min). The client will reuse until it expires; for very long sessions you may refresh by re-calling `getSpeechToken()` and updating the recognizer.
- The `/translate` endpoint uses the **headers** you validated by cURL:
  - `Ocp-Apim-Subscription-Key`
  - `Ocp-Apim-Subscription-Region`
- Keep `CORS_ALLOW_ORIGIN` tight for production.
# dual-video-translate-azure
