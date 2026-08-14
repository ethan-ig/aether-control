# Aether Control v1.1

A 7-inch Raspberry Pi touchscreen control plane with an original Xbox-360-era-inspired dashboard style.

## v1 features

- Touch-first 3-page UI: **Home → Logs → System**
- Swipe left/right between pages
- Live Govee state polling every 6 seconds
- Power tiles for:
  - Server Rack
  - Aether
  - TV
  - Setup
- OFF confirmation, with extra warning text for Server Rack and Aether
- Rack temperature + humidity tile from `3 rack inside`
- SQLite event history for controller actions and state changes
- Raspberry Pi CPU, RAM, disk, uptime, internet, and Govee status
- Fullscreen Chromium kiosk launcher
- Optional systemd service installer

## Install over your existing project

This package does **not** contain a `.env`, so it will not overwrite your API key.

From the Raspberry Pi:

```bash
cd ~
# Copy/extract this package so its contents are in ~/aether-control
cd ~/aether-control
./install.sh
```

If you already created `.venv`, `install.sh` is safe to run again.

Make sure your `.env` contains:

```env
GOVEE_API_KEY=your-key
SERVER_RACK_DEVICE_NAME=Server Rack
AETHER_DEVICE_NAME=Aether
TV_DEVICE_NAME=TV
SETUP_DEVICE_NAME=Setup
RACK_TEMP_DEVICE_NAME=3 rack inside
RACK_TEMP_UNIT=F
AETHER_BIND=127.0.0.1
AETHER_PORT=5000
```

## First test

Terminal 1:

```bash
cd ~/aether-control
./start.sh
```

Terminal 2:

```bash
cd ~/aether-control
./kiosk.sh
```

For development, open `http://127.0.0.1:5000` in Chromium instead of kiosk mode.

## Start backend automatically

After v1 works normally:

```bash
cd ~/aether-control
./install-service.sh
```

Check it with:

```bash
sudo systemctl status aether-control
journalctl -u aether-control -f
```

`install-service.sh` starts only the backend. Kiosk auto-launch can be added after the UI has been verified on the actual touchscreen/Desktop session.

## Safety behavior

Aether Control v1 uses the Govee smart plug as a direct power switch. Turning **off** Server Rack or Aether cuts outlet power immediately after confirmation. v1 does not yet perform an OS/iDRAC graceful shutdown first.

That smarter shutdown chain belongs in the next server-control integration.


## v1.1
- Automatic dark mode from 8:00 PM through 6:59 AM (controller local time).
- National Weather Service active alert overlay. Severe weather can take over the touchscreen for across-the-room visibility.
- Alert details and per-session dismissal. New alert IDs will display even after a previous alert was dismissed.

### Weather warning setup
Add the Raspberry Pi's location to `.env`:

```env
NWS_LAT=YOUR_LATITUDE
NWS_LON=YOUR_LONGITUDE
```

Restart `aether-control.service` after changing `.env`.
