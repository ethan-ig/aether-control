import os
import time
import uuid
from threading import Lock

import requests
import urllib3


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

        self.idrac_device_name = os.getenv(
            "IDRAC_DEVICE_NAME",
            os.getenv("SERVER_RACK_DEVICE_NAME", "Server Rack"),
        ).strip()
        self.idrac_host = (os.getenv("IDRAC_HOST") or "").strip()
        self.idrac_user = (os.getenv("IDRAC_USER") or "").strip()
        self.idrac_password = os.getenv("IDRAC_PASSWORD") or ""
        self.idrac_verify_ssl = (os.getenv("IDRAC_VERIFY_SSL", "false").strip().lower() in {"1", "true", "yes", "on"})
        self._idrac_system_uri = None

        if not self.idrac_verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    @property
    def headers(self):
        return {
            "Govee-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _is_idrac_device(self, name):
        return bool(
            name
            and self.idrac_device_name
            and name.strip().casefold() == self.idrac_device_name.casefold()
        )

    def _idrac_base_url(self):
        host = self.idrac_host.rstrip("/")
        if not host:
            raise GoveeError("iDRAC is not configured: set IDRAC_HOST, IDRAC_USER and IDRAC_PASSWORD in .env")
        if not self.idrac_user or not self.idrac_password:
            raise GoveeError("iDRAC is not configured: set IDRAC_HOST, IDRAC_USER and IDRAC_PASSWORD in .env")
        if not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        return host

    def _idrac_request(self, method, path, **kwargs):
        url = path if path.startswith("http") else f"{self._idrac_base_url()}{path}"
        try:
            response = requests.request(
                method,
                url,
                auth=(self.idrac_user, self.idrac_password),
                verify=self.idrac_verify_ssl,
                timeout=self.timeout,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                **kwargs,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            raise GoveeError(f"iDRAC request failed: {exc}") from exc

    def _idrac_system_path(self):
        if self._idrac_system_uri:
            return self._idrac_system_uri

        collection = self._idrac_request("GET", "/redfish/v1/Systems").json()
        members = collection.get("Members") or []
        if not members:
            raise GoveeError("iDRAC Redfish returned no computer systems")

        path = members[0].get("@odata.id")
        if not path:
            raise GoveeError("iDRAC Redfish system did not include an @odata.id")
        self._idrac_system_uri = path
        return path

    def _idrac_state(self, name):
        system_path = self._idrac_system_path()
        payload = self._idrac_request("GET", system_path).json()
        power_state = str(payload.get("PowerState") or "Unknown")
        normalized = power_state.casefold()
        if normalized == "on":
            power = 1
        elif normalized == "off":
            power = 0
        else:
            power = None

        return {
            "name": name,
            "online": True,
            "power": power,
            "temperature": None,
            "humidity": None,
            "capabilities": {"idracPowerState": power_state},
            "capability_types": {"idracPowerState": "redfish"},
            "source": "idrac",
            "power_state": power_state,
        }

    def _idrac_set_power(self, name, enabled):
        state = self._idrac_state(name)
        if enabled and state.get("power") == 1:
            return {"code": 200, "message": "Server is already on", "source": "idrac"}
        if not enabled and state.get("power") == 0:
            return {"code": 200, "message": "Server is already off", "source": "idrac"}

        system_path = self._idrac_system_path()
        action_path = f"{system_path}/Actions/ComputerSystem.Reset"
        reset_type = "On" if enabled else "GracefulShutdown"
        self._idrac_request("POST", action_path, json={"ResetType": reset_type})
        return {
            "code": 200,
            "message": f"iDRAC {reset_type} command accepted",
            "source": "idrac",
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
        if self._is_idrac_device(name):
            return self._idrac_state(name)

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
        if self._is_idrac_device(name):
            return self._idrac_set_power(name, enabled)

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
