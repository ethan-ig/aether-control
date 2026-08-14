import os
import shutil
import socket
import subprocess
import threading
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

import psutil
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from db import EventLog
from govee import GoveeClient, GoveeError

load_dotenv()

APP_VERSION = "1.4"
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
POLL_TARGETS = list(DEVICE_NAMES.items())

_last_states = {}
_started_at = time.time()

NWS_LAT = os.getenv("NWS_LAT")
NWS_LON = os.getenv("NWS_LON")
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "AetherControl/1.4 (local Raspberry Pi dashboard)")
NWS_HEADERS = {"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}
_weather_cache = {"at": 0.0, "data": None}
_alert_cache = {"at": 0.0, "data": None}


def _weather_alerts():
    now = time.time()
    if _alert_cache["data"] is not None and now - _alert_cache["at"] < 60:
        return _alert_cache["data"]

    if not NWS_LAT or not NWS_LON:
        data = {"configured": False, "alerts": []}
        _alert_cache.update(at=now, data=data)
        return data

    try:
        r = requests.get(
            "https://api.weather.gov/alerts/active",
            params={"point": f"{NWS_LAT},{NWS_LON}"},
            headers=NWS_HEADERS,
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
        data = {"configured": True, "alerts": alerts}
    except Exception as exc:
        data = {"configured": True, "alerts": [], "error": str(exc)}

    _alert_cache.update(at=now, data=data)
    return data


def _weather_snapshot():
    now = time.time()
    if _weather_cache["data"] is not None and now - _weather_cache["at"] < 300:
        return _weather_cache["data"]

    if not NWS_LAT or not NWS_LON:
        data = {"configured": False, "online": False, "error": "NWS location is not configured"}
        _weather_cache.update(at=now, data=data)
        return data

    try:
        point = requests.get(
            f"https://api.weather.gov/points/{NWS_LAT},{NWS_LON}",
            headers=NWS_HEADERS,
            timeout=8,
        )
        point.raise_for_status()
        props = point.json().get("properties", {})
        hourly_url = props.get("forecastHourly")
        if not hourly_url:
            raise RuntimeError("NWS points response did not include forecastHourly")

        forecast = requests.get(hourly_url, headers=NWS_HEADERS, timeout=8)
        forecast.raise_for_status()
        periods = forecast.json().get("properties", {}).get("periods", [])
        if not periods:
            raise RuntimeError("NWS hourly forecast returned no periods")

        period = periods[0]
        rh = (period.get("relativeHumidity") or {}).get("value")
        pop = (period.get("probabilityOfPrecipitation") or {}).get("value")
        data = {
            "configured": True,
            "online": True,
            "temperature": period.get("temperature"),
            "unit": period.get("temperatureUnit") or "F",
            "humidity": rh,
            "precipitation_probability": pop,
            "condition": period.get("shortForecast") or "Weather",
            "wind": period.get("windSpeed") or "",
            "icon": period.get("icon") or "",
            "updated": period.get("startTime"),
        }
    except Exception as exc:
        stale = _weather_cache.get("data")
        if stale and stale.get("online"):
            data = dict(stale)
            data["stale"] = True
            data["error"] = str(exc)
        else:
            data = {"configured": True, "online": False, "error": str(exc)}

    _weather_cache.update(at=now, data=data)
    return data


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
        if "error" in state:
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


def _git_head(git_bin):
    result = subprocess.run(
        [git_bin, "rev-parse", "HEAD"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _reboot_pi_after_update():
    time.sleep(1.75)
    systemctl_bin = shutil.which("systemctl") or "/usr/bin/systemctl"
    sudo_bin = shutil.which("sudo")
    command = [systemctl_bin, "reboot"]
    if sudo_bin:
        command = [sudo_bin, "-n", systemctl_bin, "reboot"]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "reboot command failed").strip()
            log.add("error", "Updater", f"Automatic reboot failed: {message[:400]}")
    except Exception as exc:
        log.add("error", "Updater", f"Automatic reboot failed: {exc}")


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

    devices = {}
    for key, name in DEVICE_NAMES.items():
        state = states.get(key, {"name": name, "online": False})
        devices[key] = {
            "name": name,
            "online": bool(state.get("online")),
            "power": state.get("power"),
            "error": state.get("error"),
        }

    weather = _weather_snapshot()
    alerts = _weather_alerts()
    top_alert = (alerts.get("alerts") or [None])[0]

    legacy_weather_tile = {
        "online": bool(weather.get("online")),
        "temperature": weather.get("temperature"),
        "humidity": weather.get("humidity"),
        "unit": weather.get("unit", "F"),
        "error": weather.get("error"),
    }

    return jsonify(
        {
            "devices": devices,
            "rack": legacy_weather_tile,
            "weather": {
                **weather,
                "alert_count": len(alerts.get("alerts") or []),
                "top_alert": top_alert,
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


@app.get("/api/weather")
def weather_status():
    weather = _weather_snapshot()
    alerts = _weather_alerts()
    return jsonify({
        **weather,
        "alerts": alerts.get("alerts") or [],
        "alert_count": len(alerts.get("alerts") or []),
    })


@app.post("/api/update/pull")
def update_pull():
    git_bin = shutil.which("git")
    if not git_bin:
        log.add("error", "Updater", "git executable was not found")
        return jsonify({"ok": False, "error": "git is not installed"}), 500

    if not os.path.isdir(os.path.join(BASE_DIR, ".git")):
        log.add("error", "Updater", "Project directory is not a Git repository")
        return jsonify({"ok": False, "error": "Aether Control is not a Git repository"}), 409

    before = _git_head(git_bin)
    log.add("info", "Updater", "git pull --ff-only requested from touchscreen")

    try:
        result = subprocess.run(
            [git_bin, "pull", "--ff-only"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.add("error", "Updater", "git pull timed out")
        return jsonify({"ok": False, "error": "git pull timed out"}), 504
    except Exception as exc:
        log.add("error", "Updater", f"git pull failed: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500

    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip()).strip()
    if result.returncode != 0:
        message = output or f"git pull exited with code {result.returncode}"
        log.add("error", "Updater", message[:500])
        return jsonify({"ok": False, "error": message}), 500

    after = _git_head(git_bin)
    changed = bool(before and after and before != after)
    if changed:
        log.add("success", "Updater", f"Pulled update {before[:7]} -> {after[:7]}")
    else:
        log.add("success", "Updater", "Repository already up to date")

    log.add("info", "Updater", "Automatic Pi reboot scheduled")
    threading.Thread(target=_reboot_pi_after_update, daemon=True).start()

    return jsonify({
        "ok": True,
        "changed": changed,
        "before": before,
        "after": after,
        "output": output,
        "message": "Update downloaded; rebooting" if changed else "Already up to date; rebooting",
        "restart_required": False,
        "reboot_scheduled": True,
    })


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
