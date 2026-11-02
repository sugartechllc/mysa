# Mysa MQTT Stack & Terminal Dashboard

This repository runs a local MQTT bridge stack for **Mysa Smart Thermostats** using Docker Compose (Podman Compose also works) and provides a live `curses`-based terminal dashboard (`mysa_dashboard.py`) to monitor thermostat telemetry in real time.

## Components

- **[`mysa_compose.yaml`](./mysa_compose.yaml)**: Compose stack running:
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

## 2. Run the Compose Stack (`mosquitto` + `mysa2mqtt`)

```bash
# Start both mosquitto and mysa2mqtt in the background
docker compose -f mysa_compose.yaml up -d

# Follow container logs
docker compose -f mysa_compose.yaml logs -f

# Inspect raw MQTT messages directly from the mosquitto container (optional)
docker exec -it mosquitto mosquitto_sub -v -t 'homeassistant/#' -t 'mysa2mqtt/#'

# Stop the stack
docker compose -f mysa_compose.yaml down
```

With Podman, substitute `podman-compose` for `docker compose` and `podman` for `docker`.

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

## 4. Running unattended on a Raspberry Pi

### Mysa stack (`mosquitto` + `mysa2mqtt`)

No systemd unit of its own is needed. Both services have `restart: unless-stopped`, so Docker restarts them after a crash or reboot once the Docker daemon itself starts at boot:

```bash
sudo systemctl enable docker
docker compose -f mysa_compose.yaml up -d   # once; containers then persist across reboots
```

Containers you stopped with `docker compose down` or `docker stop` stay stopped after a reboot.

### MqttToChords bridge

The bridge is a separate program (see the `MqttToChords` repository) and does need a systemd service. Its repository provides `mqtttochords.service` and `mqtttochords.sh`, which expect the paths below; edit the unit if yours differ.

```bash
# As the service user (pi): checkout, virtualenv and private config
git clone --recurse-submodules <MqttToChords repo> ~/MqttToChords
python3 -m venv ~/venv.mqtttochords
~/venv.mqtttochords/bin/pip install -r ~/MqttToChords/requirements.txt
cp ~/MqttToChords/config.json.example ~/MqttToChords.json   # then edit: CHORDS host, credentials, devices
chmod 600 ~/MqttToChords.json                                # it holds the CHORDS API key

# Check it first, without sending anything to CHORDS
cd ~/MqttToChords && ~/venv.mqtttochords/bin/python MqttToChords.py -t -v -c ~/MqttToChords.json

# Install and start the service
sudo cp ~/MqttToChords/mqtttochords.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mqtttochords
journalctl -u mqtttochords -f
```

Start the Mysa stack first, because the bridge subscribes to the broker on `localhost:1883`.
