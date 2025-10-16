import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import socketio  # python-socketio (ASGI)

# ------------------
# Config from ENV (+ fallbacks for your Docker)
# ------------------
TRANSLATOR_KEY = os.getenv("TRANSLATOR_KEY") or os.getenv("apikey", "")
TRANSLATOR_REGION = os.getenv("TRANSLATOR_REGION", "eastus")
TRANSLATOR_ENDPOINT = os.getenv("TRANSLATOR_ENDPOINT") or os.getenv("billing", "")
SPEECH_KEY = os.getenv("SPEECH_KEY", "")
SPEECH_REGION = os.getenv("SPEECH_REGION", "eastus")
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "*")

if not TRANSLATOR_ENDPOINT:
    raise RuntimeError("TRANSLATOR_ENDPOINT is required (e.g., https://<name>.cognitiveservices.azure.com)")

API_BASE = f"{TRANSLATOR_ENDPOINT.rstrip('/')}/translator/text/v3.0"

# ------------------
# Flask app (WSGI)
# ------------------
flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": CORS_ALLOW_ORIGIN}})

@flask_app.get("/health")
def health():
    return {"ok": True}

@flask_app.post("/translate")
def translate():
    data = (request.get_json(silent=True) or {})
    text = (data.get("text") or "").strip()
    from_lang = (data.get("from") or "").strip() or None

    # Support single or CSV: /translate?to=en or /translate?to=en,fr,es
    to_param = (request.args.get("to") or "").strip()
    if not text or not to_param:
        return jsonify({"error": "missing_text_or_to"}), 400

    to_list = [t.strip() for t in to_param.split(",") if t.strip()]
    if not to_list:
        return jsonify({"error": "invalid_to"}), 400

    params = {"api-version": "3.0"}
    for t in to_list:
        # 'to' is repeated per target language
        params.setdefault("to", [])
        params["to"].append(t)
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
        # Convenience: first translation text if present
        translated = None
        if payload and isinstance(payload, list):
            translations = (payload[0] or {}).get("translations") or []
            if translations:
                translated = translations[0].get("text")
        return jsonify({"translated": translated, "raw": payload})
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", 500)
        detail = None
        try:
            detail = e.response.json()
        except Exception:
            detail = str(e)
        return jsonify({"error": "translate_failed", "detail": detail}), status

@flask_app.get("/speech/token")
def speech_token():
    if not SPEECH_KEY:
        return jsonify({"error": "missing_speech_key"}), 500
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
# Socket.IO (ASGI server) — baseline
# ------------------
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=CORS_ALLOW_ORIGIN,
)

@sio.event
async def connect(sid, environ):
    # No-op; hook for auth if needed
    return

@sio.event
async def disconnect(sid):
    # No-op; add presence cleanup if you enable presence
    return

@sio.on("join")
async def on_join(sid, data):
    room = (data or {}).get("room") or "default"
    sio.enter_room(sid, room)  # ok to call without await in ASGI server
    await sio.emit("system", {"message": "joined"}, room=room, skip_sid=sid)

@sio.on("leave")
async def on_leave(sid, data):
    room = (data or {}).get("room") or "default"
    sio.leave_room(sid, room)
    await sio.emit("system", {"message": "left"}, room=room, skip_sid=sid)

@sio.on("signal")
async def on_signal(sid, data):
    room = (data or {}).get("room") or "default"
    await sio.emit("signal", data, room=room, skip_sid=sid)

# Wrap Flask WSGI app into ASGI and mount under Socket.IO ASGI app
from asgiref.wsgi import WsgiToAsgi
asgi_flask = WsgiToAsgi(flask_app)
asgi_app = socketio.ASGIApp(sio, other_asgi_app=asgi_flask)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    # If this file is named app.py, "app:asgi_app" is correct.
    uvicorn.run("app:asgi_app", host="0.0.0.0", port=port, log_level="info")
