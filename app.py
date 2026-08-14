import os
import re
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import psutil
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from db import EventLog
from govee import GoveeClient, GoveeError

load_dotenv()

APP_VERSION = "1.5"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PREVIOUS_HEAD_FILE = os.path.join(DATA_DIR, "previous-update-head")
os.makedirs(DATA_DIR, exist_ok=True)

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
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "AetherControl/1.5 (local Raspberry Pi dashboard)")
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
        response = requests.get(
            "https://api.weather.gov/alerts/active",
            params={"point": f"{NWS_LAT},{NWS_LON}"},
            headers=NWS_HEADERS,
            timeout=8,
        )
        response.raise_for_status()
        features = response.json().get("features", [])
        alerts = []
        rank = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1, "Unknown": 0}
        for feature in features:
            prop = feature.get("properties", {})
            alerts.append(
                {
                    "id": feature.get("id") or prop.get("id"),
                    "event": prop.get("event") or "Weather Alert",
                    "severity": prop.get("severity") or "Unknown",
                    "urgency": prop.get("urgency") or "Unknown",
                    "headline": prop.get("headline") or prop.get("event") or "Weather alert in effect",
                    "description": prop.get("description") or "",
                    "instruction": prop.get("instruction") or "",
                    "expires": prop.get("expires"),
                }
            )
        alerts.sort(key=lambda alert: rank.get(alert["severity"], 0), reverse=True)
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
        humidity = (period.get("relativeHumidity") or {}).get("value")
        precipitation = (period.get("probabilityOfPrecipitation") or {}).get("value")
        data = {
            "configured": True,
            "online": True,
            "temperature": period.get("temperature"),
            "unit": period.get("temperatureUnit") or "F",
            "humidity": humidity,
            "precipitation_probability": precipitation,
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


def _power_capable_devices():
    if not govee:
        return []
    devices = []
    for device in govee.get_devices():
        name = (device.get("deviceName") or "").strip()
        sku = (device.get("sku") or "").strip()
        device_id = device.get("device")
        capabilities = device.get("capabilities") or []
        has_power = any(
            cap.get("instance") == "powerSwitch"
            and cap.get("type") == "devices.capabilities.on_off"
            for cap in capabilities
        )
        if name and sku and device_id and has_power:
            devices.append({"name": name, "sku": sku})
    return devices


def _device_category(sku):
    sku = (sku or "").upper()
    if sku.startswith("H508"):
        return "plug"
    if sku.startswith(("H600", "H61")):
        return "light"
    if sku.startswith("H71"):
        return "air"
    return "device"


def _known_key_for_name(name):
    target = name.strip().casefold()
    for key, configured_name in DEVICE_NAMES.items():
        if configured_name.strip().casefold() == target:
            return key
    return None


def _home_device_payload():
    discovered = _power_capable_devices()
    if not discovered:
        return []

    results = {}
    workers = min(8, len(discovered))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(govee.get_state, item["name"]): item for item in discovered}
        for future in as_completed(futures):
            item = futures[future]
            try:
                state = future.result()
            except GoveeError as exc:
                state = {"online": False, "power": None, "error": str(exc)}
            results[item["name"]] = state

    payload = []
    for item in discovered:
        state = results.get(item["name"], {})
        known_key = _known_key_for_name(item["name"])
        payload.append(
            {
                "name": item["name"],
                "sku": item["sku"],
                "category": _device_category(item["sku"]),
                "online": bool(state.get("online")),
                "power": state.get("power"),
                "error": state.get("error"),
                "known_key": known_key,
                "critical": known_key in ("server-rack", "aether"),
            }
        )
    payload.sort(key=lambda item: (item["category"], item["name"].casefold()))
    return payload


def _git_binary():
    return shutil.which("git")


def _git_run(args, timeout=20):
    git_bin = _git_binary()
    if not git_bin:
        raise RuntimeError("git is not installed")
    return subprocess.run(
        [git_bin, *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_text(args, timeout=20):
    result = _git_run(args, timeout=timeout)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "git command failed").strip()
        raise RuntimeError(message)
    return result.stdout.strip()


def _git_head(ref="HEAD"):
    try:
        return _git_text(["rev-parse", ref], timeout=8)
    except Exception:
        return None


def _git_dirty():
    try:
        return bool(_git_text(["status", "--porcelain", "--untracked-files=no"], timeout=8))
    except Exception:
        return True


def _remote_version():
    try:
        source = _git_text(["show", "origin/main:app.py"], timeout=8)
        match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
        return match.group(1) if match else None
    except Exception:
        return None


def _read_previous_head():
    try:
        value = open(PREVIOUS_HEAD_FILE, "r", encoding="utf-8").read().strip()
        if not value:
            return None
        result = _git_run(["cat-file", "-e", f"{value}^{{commit}}"], timeout=8)
        return value if result.returncode == 0 else None
    except Exception:
        return None


def _write_previous_head(value):
    if value:
        with open(PREVIOUS_HEAD_FILE, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")


def _fetch_remote():
    result = _git_run(["fetch", "--quiet", "origin", "+main:refs/remotes/origin/main"], timeout=30)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "git fetch failed").strip()
        raise RuntimeError(message)


def _update_status(fetch=True):
    if fetch:
        _fetch_remote()
    local_head = _git_head("HEAD")
    remote_head = _git_head("origin/main")
    if not local_head or not remote_head:
        raise RuntimeError("Could not resolve local or remote Git commit")

    count_text = _git_text(["rev-list", "--count", f"{local_head}..{remote_head}"], timeout=8)
    commits_ahead = int(count_text or "0")
    commits = []
    if commits_ahead:
        raw = _git_text(
            ["log", "-n", "8", "--format=%h%x1f%s", f"{local_head}..{remote_head}"],
            timeout=8,
        )
        for line in raw.splitlines():
            if "\x1f" in line:
                sha, subject = line.split("\x1f", 1)
                commits.append({"sha": sha, "subject": subject})

    previous = _read_previous_head()
    return {
        "ok": True,
        "available": commits_ahead > 0,
        "commits_ahead": commits_ahead,
        "commits": commits,
        "local_head": local_head,
        "remote_head": remote_head,
        "local_version": APP_VERSION,
        "remote_version": _remote_version(),
        "rollback_available": bool(previous and previous != local_head),
        "rollback_head": previous,
        "dirty": _git_dirty(),
    }


def _reboot_pi_after_update():
    time.sleep(1.75)
    systemctl_bin = shutil.which("systemctl") or "/usr/bin/systemctl"
    sudo_bin = shutil.which("sudo")
    command = [systemctl_bin, "reboot"]
    if sudo_bin:
        command = [sudo_bin, "-n", systemctl_bin, "reboot"]

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "reboot command failed").strip()
            log.add("error", "Updater", f"Automatic reboot failed: {message[:400]}")
    except Exception as exc:
        log.add("error", "Updater", f"Automatic reboot failed: {exc}")


def _schedule_reboot():
    log.add("info", "Updater", "Automatic Pi reboot scheduled")
    threading.Thread(target=_reboot_pi_after_update, daemon=True).start()


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


@app.get("/api/home")
def home_devices():
    if not govee:
        return jsonify({"ok": False, "error": "Govee API is not configured", "devices": []}), 503
    try:
        devices = _home_device_payload()
        return jsonify({"ok": True, "devices": devices, "count": len(devices)})
    except GoveeError as exc:
        return jsonify({"ok": False, "error": str(exc), "devices": []}), 502


@app.post("/api/home/power")
def home_device_power():
    if not govee:
        return jsonify({"ok": False, "error": "Govee API is not configured"}), 503

    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    enabled = body.get("on")
    if not name or not isinstance(enabled, bool):
        return jsonify({"ok": False, "error": "Body must contain device name and boolean field 'on'"}), 400

    try:
        allowed = {item["name"].casefold(): item["name"] for item in _power_capable_devices()}
        canonical_name = allowed.get(name.casefold())
        if not canonical_name:
            return jsonify({"ok": False, "error": "Device is not power-controllable"}), 404

        known_key = _known_key_for_name(canonical_name)
        if not enabled and known_key in ("server-rack", "aether"):
            return jsonify({"ok": False, "error": "Use protected power-off confirmation for this device"}), 409

        action = "ON" if enabled else "OFF"
        log.add("info", canonical_name, f"Power {action} requested from My Home")
        govee.set_power(canonical_name, enabled)
        time.sleep(0.35)
        state = govee.get_state(canonical_name)
        log.add("success", canonical_name, f"Power {action} sent from My Home")
        return jsonify(
            {
                "ok": True,
                "device": {
                    "name": canonical_name,
                    "online": state.get("online"),
                    "power": state.get("power"),
                },
            }
        )
    except GoveeError as exc:
        log.add("error", name or "My Home", f"Power control failed: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.get("/api/weather/alerts")
def weather_alerts():
    return jsonify(_weather_alerts())


@app.get("/api/weather")
def weather_status():
    weather = _weather_snapshot()
    alerts = _weather_alerts()
    return jsonify(
        {
            **weather,
            "alerts": alerts.get("alerts") or [],
            "alert_count": len(alerts.get("alerts") or []),
        }
    )


@app.get("/api/update/status")
def update_status():
    try:
        return jsonify(_update_status(fetch=True))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "local_version": APP_VERSION}), 502


@app.post("/api/update/install")
def update_install():
    try:
        status = _update_status(fetch=True)
    except Exception as exc:
        log.add("error", "Updater", f"Update check failed: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 502

    if status.get("dirty"):
        message = "Tracked files have local changes; refusing to overwrite them"
        log.add("error", "Updater", message)
        return jsonify({"ok": False, "error": message}), 409

    if not status.get("available"):
        return jsonify({"ok": True, "changed": False, "message": "Already up to date", "reboot_scheduled": False})

    before = status["local_head"]
    _write_previous_head(before)
    log.add("info", "Updater", f"Installing {status['commits_ahead']} pending commit(s)")

    result = _git_run(["merge", "--ff-only", "origin/main"], timeout=45)
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip()).strip()
    if result.returncode != 0:
        message = output or f"git merge exited with code {result.returncode}"
        log.add("error", "Updater", message[:500])
        return jsonify({"ok": False, "error": message}), 500

    after = _git_head("HEAD")
    log.add("success", "Updater", f"Installed update {before[:7]} -> {after[:7] if after else 'unknown'}")
    _schedule_reboot()
    return jsonify(
        {
            "ok": True,
            "changed": True,
            "before": before,
            "after": after,
            "output": output,
            "message": "Update installed; rebooting",
            "reboot_scheduled": True,
        }
    )


@app.post("/api/update/pull")
def update_pull_legacy():
    return update_install()


@app.post("/api/update/rollback")
def update_rollback():
    previous = _read_previous_head()
    current = _git_head("HEAD")
    if not previous or not current or previous == current:
        return jsonify({"ok": False, "error": "No previous update is available to roll back"}), 409
    if _git_dirty():
        return jsonify({"ok": False, "error": "Tracked files have local changes; refusing rollback"}), 409

    result = _git_run(["reset", "--hard", previous], timeout=30)
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip()).strip()
    if result.returncode != 0:
        message = output or "git reset failed"
        log.add("error", "Updater", f"Rollback failed: {message[:500]}")
        return jsonify({"ok": False, "error": message}), 500

    _write_previous_head(current)
    log.add("success", "Updater", f"Rolled back {current[:7]} -> {previous[:7]}")
    _schedule_reboot()
    return jsonify(
        {
            "ok": True,
            "from": current,
            "to": previous,
            "message": "Rollback complete; rebooting",
            "reboot_scheduled": True,
        }
    )


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
