# Mysa MQTT Stack & Terminal Dashboard

This repository runs a local MQTT bridge stack for **Mysa Smart Thermostats** using Podman Compose and provides a live `curses`-based terminal dashboard (`mysa_dashboard.py`) to monitor thermostat telemetry in real time.

## Components

- **[`mysa_compose.yaml`](./mysa_compose.yaml)**: Podman Compose stack running:
  - **`mosquitto`** (`docker.io/library/eclipse-mosquitto:2`): Local MQTT broker configured via [`mosquitto/config/mosquitto.conf`](./mosquitto/config/mosquitto.conf).
  - **`mysa2mqtt`** (`docker.io/bourquep/mysa2mqtt:latest`): Official [`bourquep/mysa2mqtt`](https://github.com/bourquep/mysa2mqtt) container that authenticates with Mysa Cloud and publishes Home Assistant MQTT Auto-Discovery (`homeassistant/#`) and state (`mysa2mqtt/#`) topics.
- **[`mysa_dashboard.py`](./mysa_dashboard.py)**: Live terminal dashboard built with Python's `curses` module that subscribes to `homeassistant/#` and `mysa2mqtt/#` and displays:
  - Pretty thermostat names (`device.name` and unescaped `-XX` topic paths)
  - Online/offline status, HVAC mode, and active state (`HEATING`, `COOLING`, `IDLE`, `OFF`)
  - Live ambient temperature and target setpoint in both `°F` and `°C`
  - Relative humidity (`%`), fan mode, hardware metadata (`ID`, `SN`, `FW`), and last-updated timestamp with a live staleness counter.
- **[`.env.example`](./.env.example)**: Template for Mysa credentials and `M2M_*` configuration options.

## 1. Configure Environment

Copy [`.env.example`](./.env.example) to `.env` and fill in your Mysa account credentials:

```bash
cp .env.example .env
```

## 2. Run the Podman Compose Stack (`mosquitto` + `mysa2mqtt`)

```bash
# Start both mosquitto and mysa2mqtt in the background
podman-compose -f mysa_compose.yaml up -d

# Follow container logs
podman-compose -f mysa_compose.yaml logs -f

# Inspect raw MQTT messages directly from the mosquitto container (optional)
podman exec -it mosquitto mosquitto_sub -v -t 'homeassistant/#' -t 'mysa2mqtt/#'

# Stop the stack
podman-compose -f mysa_compose.yaml down
```

## 3. Run the Terminal Dashboard (`mysa_dashboard.py`)

Set up the Python virtual environment (requires `paho-mqtt` and `python-dotenv`):

```bash
python3 -m venv .venv
.venv/bin/pip install paho-mqtt python-dotenv
```

Launch the live dashboard:

```bash
.venv/bin/python mysa_dashboard.py
```

### Dashboard Controls
- **`u`** (or **`f`** / **`c`**): Toggle primary temperature display unit between `°F` and `°C`.
- **`q`**: Quit the dashboard.
