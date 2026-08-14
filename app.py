import os
import socket
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

import psutil
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from db import EventLog
from govee import GoveeClient, GoveeError

load_dotenv()

APP_VERSION = "1.1"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

log = EventLog(os.path.join(DATA_DIR, "events.db"))

try:
    govee = GoveeClient()
    GOVEE_READY = True
except GoveeError:
    govee = None
    GOVEE_READY = False

DEVICE_NAMES = {
    "server-rack": os.getenv("SERVER_RACK_DEVICE_NAME", "Server Rack"),
    "aether": os.getenv("AETHER_DEVICE_NAME", "Aether"),
    "tv": os.getenv("TV_DEVICE_NAME", "TV"),
    "setup": os.getenv("SETUP_DEVICE_NAME", "Setup"),
}
RACK_TEMP_DEVICE = os.getenv("RACK_TEMP_DEVICE_NAME", "3 rack inside")
RACK_TEMP_UNIT = os.getenv("RACK_TEMP_UNIT", "F").upper()
POLL_TARGETS = list(DEVICE_NAMES.items()) + [("rack-temp", RACK_TEMP_DEVICE)]

_last_states = {}
_started_at = time.time()

NWS_LAT = os.getenv("NWS_LAT")
NWS_LON = os.getenv("NWS_LON")
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "AetherControl/1.1 (local Raspberry Pi dashboard)")


def _weather_alerts():
    if not NWS_LAT or not NWS_LON:
        return {"configured": False, "alerts": []}
    try:
        r = requests.get(
            "https://api.weather.gov/alerts/active",
            params={"point": f"{NWS_LAT},{NWS_LON}"},
            headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"},
            timeout=8,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        alerts = []
        rank = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1, "Unknown": 0}
        for feature in features:
            prop = feature.get("properties", {})
            alerts.append({
                "id": feature.get("id") or prop.get("id"),
                "event": prop.get("event") or "Weather Alert",
                "severity": prop.get("severity") or "Unknown",
                "urgency": prop.get("urgency") or "Unknown",
                "headline": prop.get("headline") or prop.get("event") or "Weather alert in effect",
                "description": prop.get("description") or "",
                "instruction": prop.get("instruction") or "",
                "expires": prop.get("expires"),
            })
        alerts.sort(key=lambda a: rank.get(a["severity"], 0), reverse=True)
        return {"configured": True, "alerts": alerts}
    except Exception as exc:
        return {"configured": True, "alerts": [], "error": str(exc)}


def _internet_online():
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=0.75):
            return True
    except OSError:
        return False


def _read_one(key, name):
    if not govee:
        return key, {"name": name, "online": False, "error": "Govee API key is not configured"}
    try:
        return key, govee.get_state(name)
    except GoveeError as exc:
        return key, {"name": name, "online": False, "error": str(exc)}


def _record_state_changes(states):
    for key, state in states.items():
        if key == "rack-temp" or "error" in state:
            continue
        current = (state.get("online"), state.get("power"))
        previous = _last_states.get(key)
        if previous is not None and current != previous:
            if not state.get("online"):
                log.add("warning", state["name"], "Device became unreachable")
            elif state.get("power") == 1:
                log.add("success", state["name"], "Power state changed to ON")
            elif state.get("power") == 0:
                log.add("info", state["name"], "Power state changed to OFF")
        _last_states[key] = current


def _system_payload():
    try:
        root_disk = psutil.disk_usage("/")
        boot_time = psutil.boot_time()
        return {
            "hostname": socket.gethostname(),
            "cpu": round(psutil.cpu_percent(interval=0.15), 1),
            "memory": round(psutil.virtual_memory().percent, 1),
            "disk": round(root_disk.percent, 1),
            "uptime_seconds": int(time.time() - boot_time),
            "controller_uptime_seconds": int(time.time() - _started_at),
            "internet": _internet_online(),
            "govee_configured": GOVEE_READY,
            "version": APP_VERSION,
        }
    except Exception as exc:
        return {"error": str(exc), "version": APP_VERSION}


@app.get("/")
def index():
    return render_template("index.html", version=APP_VERSION)


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "version": APP_VERSION, "govee_configured": GOVEE_READY})


@app.get("/api/dashboard")
def dashboard():
    states = {}
    with ThreadPoolExecutor(max_workers=len(POLL_TARGETS)) as pool:
        futures = [pool.submit(_read_one, key, name) for key, name in POLL_TARGETS]
        for future in as_completed(futures):
            key, state = future.result()
            states[key] = state

    _record_state_changes(states)

    sensor = states.get("rack-temp", {})
    temp = sensor.get("temperature")
    humidity = sensor.get("humidity")

    devices = {}
    for key, name in DEVICE_NAMES.items():
        state = states.get(key, {"name": name, "online": False})
        devices[key] = {
            "name": name,
            "online": bool(state.get("online")),
            "power": state.get("power"),
            "error": state.get("error"),
        }

    return jsonify(
        {
            "devices": devices,
            "rack": {
                "online": bool(sensor.get("online")),
                "temperature": temp,
                "humidity": humidity,
                "unit": RACK_TEMP_UNIT,
                "error": sensor.get("error"),
            },
            "network": {"internet": _internet_online()},
            "version": APP_VERSION,
        }
    )


@app.post("/api/device/<key>/power")
def set_device_power(key):
    if key not in DEVICE_NAMES:
        return jsonify({"ok": False, "error": "Unknown device"}), 404
    if not govee:
        return jsonify({"ok": False, "error": "Govee API is not configured"}), 503

    body = request.get_json(silent=True) or {}
    enabled = body.get("on")
    if not isinstance(enabled, bool):
        return jsonify({"ok": False, "error": "Body must contain boolean field 'on'"}), 400

    name = DEVICE_NAMES[key]
    action = "ON" if enabled else "OFF"
    log.add("info", name, f"Power {action} requested from touchscreen")

    try:
        govee.set_power(name, enabled)
        time.sleep(0.45)
        state = govee.get_state(name)
        if not state.get("online"):
            raise GoveeError("Device is currently unreachable")

        confirmed = state.get("power") == (1 if enabled else 0)
        if confirmed:
            log.add("success", name, f"Power {action} confirmed")
        else:
            log.add("warning", name, f"Govee accepted {action}, but state has not caught up yet")

        _last_states[key] = (state.get("online"), state.get("power"))
        return jsonify(
            {
                "ok": True,
                "confirmed": confirmed,
                "device": {
                    "name": name,
                    "online": state.get("online"),
                    "power": state.get("power"),
                },
            }
        )
    except GoveeError as exc:
        log.add("error", name, f"Power {action} failed: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.get("/api/weather/alerts")
def weather_alerts():
    return jsonify(_weather_alerts())


@app.get("/api/logs")
def get_logs():
    return jsonify({"events": log.recent(request.args.get("limit", 100))})


@app.post("/api/logs/clear")
def clear_logs():
    log.clear()
    log.add("info", "Aether Control", "Event log cleared")
    return jsonify({"ok": True})


@app.get("/api/system")
def system_status():
    return jsonify(_system_payload())


if __name__ == "__main__":
    bind = os.getenv("AETHER_BIND", "127.0.0.1")
    port = int(os.getenv("AETHER_PORT", "5000"))
    app.run(host=bind, port=port, threaded=True)
