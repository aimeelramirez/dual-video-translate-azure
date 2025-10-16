import os
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room

# ------------------
# Config from ENV
# ------------------
TRANSLATOR_KEY = os.getenv("TRANSLATOR_KEY", "")
TRANSLATOR_REGION = os.getenv("TRANSLATOR_REGION", "eastus")
TRANSLATOR_ENDPOINT = os.getenv("TRANSLATOR_ENDPOINT", "")
SPEECH_KEY = os.getenv("SPEECH_KEY", "")
SPEECH_REGION = os.getenv("SPEECH_REGION", "eastus")
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "*")

if not TRANSLATOR_ENDPOINT:
    # e.g., "https://<resource-name>.cognitiveservices.azure.com"
    raise RuntimeError("TRANSLATOR_ENDPOINT is required (e.g., https://<name>.cognitiveservices.azure.com)")

API_BASE = f"{TRANSLATOR_ENDPOINT}/translator/text/v3.0"

# ------------------
# App setup
# ------------------
app = Flask(__name__, static_folder=None)
CORS(app, resources={r"/*": {"origins": CORS_ALLOW_ORIGIN}})

# Use eventlet-based server for WebSocket support on many hosts
socketio = SocketIO(app, cors_allowed_origins=CORS_ALLOW_ORIGIN)

# ------------------
# Routes
# ------------------

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/translate")
def translate():
    """
    Body: { "text": "...", "to": "fr", "from": "en" (optional) }
    Proxies to Azure Translator with key + region headers.
    """
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    to = (data.get("to") or "").strip()
    from_lang = (data.get("from") or "").strip() or None

    if not text or not to:
        return jsonify({"error": "missing_text_or_to"}), 400

    params = {"api-version": "3.0", "to": to}
    if from_lang:
        params["from"] = from_lang

    headers = {
        "Ocp-Apim-Subscription-Key": TRANSLATOR_KEY,
        "Ocp-Apim-Subscription-Region": TRANSLATOR_REGION,
        "Content-Type": "application/json",
    }
    body = [{"Text": text}]

    try:
        r = requests.post(f"{API_BASE}/translate", params=params, headers=headers, json=body, timeout=10)
        r.raise_for_status()
        payload = r.json()
        translated = payload[0]["translations"][0]["text"]
        return jsonify({"translated": translated})
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", 500)
        detail = None
        try:
            detail = e.response.json()
        except Exception:
            detail = str(e)
        return jsonify({"error": "translate_failed", "detail": detail}), status

@app.get("/speech/token")
def speech_token():
    """
    Issues a short-lived Azure Speech token using the standard STS endpoint.
    Do not expose SPEECH_KEY to the browser.
    """
    if not SPEECH_KEY:
        return jsonify({"error": "missing_speech_key"}), 500

    # Official token issuance endpoint
    url = f"https://{SPEECH_REGION}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    headers = {"Ocp-Apim-Subscription-Key": SPEECH_KEY}
    try:
        r = requests.post(url, headers=headers, timeout=10)
        r.raise_for_status()
        token = r.text
        return jsonify({"region": SPEECH_REGION, "token": token})
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", 500)
        return jsonify({"error": "speech_token_failed"}), status

# ------------------
# Socket.IO signaling for WebRTC + captions
# ------------------

@socketio.on("join")
def on_join(data):
    room = (data or {}).get("room") or "default"
    join_room(room)
    emit("system", {"message": "joined"}, to=room)

@socketio.on("signal")
def on_signal(data):
    # Pass-through signaling messages to all peers except sender
    room = (data or {}).get("room") or "default"
    emit("signal", data, to=room, include_self=False)

@socketio.on("leave")
def on_leave(data):
    room = (data or {}).get("room") or "default"
    leave_room(room)
    emit("system", {"message": "left"}, to=room)

# ------------------
# Main
# ------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    # eventlet is auto-used by SocketIO when installed
    socketio.run(app, host="0.0.0.0", port=port)
