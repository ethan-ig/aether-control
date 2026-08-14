import os
import time
import uuid
from threading import Lock

import requests


class GoveeError(RuntimeError):
    pass


class GoveeClient:
    BASE_URL = "https://openapi.api.govee.com/router/api/v1"

    def __init__(self, api_key=None, timeout=8, device_cache_seconds=300):
        self.api_key = api_key or os.getenv("GOVEE_API_KEY")
        if not self.api_key:
            raise GoveeError("GOVEE_API_KEY is not set")
        self.timeout = timeout
        self.device_cache_seconds = device_cache_seconds
        self._device_cache = None
        self._device_cache_at = 0.0
        self._cache_lock = Lock()

    @property
    def headers(self):
        return {
            "Govee-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _check(self, response):
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise GoveeError(f"Govee HTTP error: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise GoveeError("Govee returned non-JSON data") from exc

        if payload.get("code") != 200:
            message = payload.get("message") or payload.get("msg") or "Unknown Govee API error"
            raise GoveeError(f"Govee API error {payload.get('code')}: {message}")
        return payload

    def get_devices(self, force=False):
        now = time.monotonic()
        with self._cache_lock:
            if (
                not force
                and self._device_cache is not None
                and now - self._device_cache_at < self.device_cache_seconds
            ):
                return list(self._device_cache)

        try:
            response = requests.get(
                f"{self.BASE_URL}/user/devices",
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GoveeError(f"Unable to reach Govee: {exc}") from exc

        data = self._check(response).get("data", [])
        with self._cache_lock:
            self._device_cache = list(data)
            self._device_cache_at = now
        return list(data)

    def find_device(self, name):
        target = name.strip().casefold()
        for device in self.get_devices():
            if device.get("deviceName", "").strip().casefold() == target:
                return device
        raise GoveeError(f"Govee device not found: {name}")

    def get_state_raw(self, name):
        device = self.find_device(name)
        body = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": device["sku"],
                "device": device["device"],
            },
        }
        try:
            response = requests.post(
                f"{self.BASE_URL}/device/state",
                headers=self.headers,
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GoveeError(f"Unable to read {name}: {exc}") from exc
        return self._check(response)

    def get_state(self, name):
        raw = self.get_state_raw(name)
        caps = raw.get("payload", {}).get("capabilities", [])
        values = {}
        types = {}
        for cap in caps:
            instance = cap.get("instance")
            if not instance:
                continue
            values[instance] = cap.get("state", {}).get("value")
            types[instance] = cap.get("type")

        online = values.get("online")
        if online is None:
            online = True

        return {
            "name": name,
            "online": bool(online),
            "power": values.get("powerSwitch"),
            "temperature": values.get("sensorTemperature"),
            "humidity": values.get("sensorHumidity"),
            "capabilities": values,
            "capability_types": types,
        }

    def set_power(self, name, enabled):
        device = self.find_device(name)
        body = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": device["sku"],
                "device": device["device"],
                "capability": {
                    "type": "devices.capabilities.on_off",
                    "instance": "powerSwitch",
                    "value": 1 if enabled else 0,
                },
            },
        }
        try:
            response = requests.post(
                f"{self.BASE_URL}/device/control",
                headers=self.headers,
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GoveeError(f"Unable to control {name}: {exc}") from exc
        return self._check(response)
