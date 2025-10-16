import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import socketio  # python-socketio (ASGI)

# ------------------
# Config from ENV (+ Docker fallbacks)
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

    to_param = (request.args.get("to") or "").strip()
    if not text or not to_param:
        return jsonify({"error": "missing_text_or_to"}), 400

    to_list = [t.strip() for t in to_param.split(",") if t.strip()]
    if not to_list:
        return jsonify({"error": "invalid_to"}), 400

    params = {"api-version": "3.0"}
    for t in to_list:
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
# Socket.IO (ASGI) + Presence for dual rooms
# ------------------
import asyncio
from collections import defaultdict

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=CORS_ALLOW_ORIGIN,
)

presence_lock = asyncio.Lock()
# room -> userId -> {"name": str, "devices": set(deviceId, ...)}
room_users = defaultdict(dict)

async def emit_roster(room: str):
    async with presence_lock:
        users = room_users.get(room, {})
        roster = [
            {"userId": uid, "name": info.get("name") or uid, "devices": sorted(list(info.get("devices", set())))}
            for uid, info in users.items()
        ]
    await sio.emit("roster", {"room": room, "users": roster}, room=room)

@sio.event
async def connect(sid, environ):
    return

@sio.event
async def disconnect(sid):
    await _presence_remove(sid, "disconnect")

@sio.on("join")
async def on_join(sid, data):
    data = data or {}
    room = (data.get("room") or "default").strip() or "default"
    user_id = (data.get("userId") or "").strip()
    device_id = (data.get("deviceId") or "").strip()
    name = (data.get("name") or user_id or "Guest").strip()

    # Back-compat: allow room-only join (older clients)
    if not user_id or not device_id:
        sio.enter_room(sid, room)
        await sio.emit("system", {"message": "joined"}, room=room, skip_sid=sid)
        return

    # Optional: capacity guard (2 distinct users max)
    async with presence_lock:
        distinct = len(room_users.get(room, {}))
        if distinct >= 2 and user_id not in room_users[room]:
            await sio.emit("system", {"level": "error", "message": "room_full"}, to=sid)
            return

    sio.enter_room(sid, room)
    await sio.save_session(sid, {"room": room, "userId": user_id, "deviceId": device_id, "name": name})

    async with presence_lock:
        already = user_id in room_users[room]
        if not already:
            room_users[room][user_id] = {"name": name, "devices": set()}
        pre = set(room_users[room][user_id]["devices"])
        room_users[room][user_id]["devices"].add(device_id)

    another_device = already and (device_id not in pre)

    await sio.emit(
        "user_joined",
        {"room": room, "userId": user_id, "name": name, "deviceId": device_id, "anotherDevice": another_device},
        room=room,
        skip_sid=sid,
    )
    await emit_roster(room)

@sio.on("leave")
async def on_leave(sid, data):
    await _presence_remove(sid, "leave")

@sio.on("signal")
async def on_signal(sid, data):
    # Relay signaling/captions to everyone else in the room
    room = (data or {}).get("room") or "default"
    await sio.emit("signal", data, room=room, skip_sid=sid)

async def _presence_remove(sid, reason: str):
    try:
        session = await sio.get_session(sid)
    except KeyError:
        session = None
    if not session:
        return

    room = session.get("room")
    user_id = session.get("userId")
    device_id = session.get("deviceId")
    name = session.get("name") or user_id
    last_device = False

    async with presence_lock:
        ue = room_users.get(room, {}).get(user_id)
        if ue:
            ds = ue.get("devices", set())
            ds.discard(device_id)
            if not ds:
                last_device = True
                room_users[room].pop(user_id, None)
                if not room_users[room]:
                    room_users.pop(room, None)

    if room:
        await sio.emit(
            "user_left",
            {"room": room, "userId": user_id, "name": name, "deviceId": device_id, "lastDevice": last_device, "reason": reason},
            room=room,
            skip_sid=sid,
        )
        await emit_roster(room)

# Wrap Flask WSGI app into ASGI and mount under Socket.IO ASGI app
from asgiref.wsgi import WsgiToAsgi
asgi_flask = WsgiToAsgi(flask_app)
asgi_app = socketio.ASGIApp(sio, other_asgi_app=asgi_flask)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:asgi_app", host="0.0.0.0", port=port, log_level="info")
