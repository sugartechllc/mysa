# Mysa MQTT Schema

Derived from `mqtt.txt` (84 messages captured from a `mosquitto_sub -v` style subscription to `mysa2mqtt/#`). Only state topics were observed; no command or discovery topics appear in the capture.

## Topic grammar

```
mysa2mqtt/climate/<device_name>/mysa_<device_id>_climate/<attribute>
mysa2mqtt/sensor/<device_name>/mysa_<device_id>_<measurement>/state
```

| Segment | Description |
|---|---|
| `mysa2mqtt` | Root prefix (bridge name) |
| `<entity_type>` | `climate` or `sensor` (Home Assistant MQTT entity types) |
| `<device_name>` | User-assigned device name, URL-escaped with `-` instead of `%` (`Living-20Room` = `Living Room`). Decode with `re.sub(r'-([0-9A-F]{2})', ...)` |
| `<device_id>` | 12 hex chars, lowercase, MAC-style (e.g. `24dcc38096c8`) |
| `<attribute>` / `<measurement>` | See below |

Payloads are plain text (not JSON), no units.

## Climate topics

Base: `mysa2mqtt/climate/<device_name>/mysa_<device_id>_climate/`

| Attribute | Type | Observed values | Notes |
|---|---|---|---|
| `current_temperature` | float, 1 decimal | `13.1` – `16.2` | °C (inferred) |
| `current_humidity` | float, 1 decimal | `53.3` – `62.0` | % RH |
| `mode_state` | enum string | `off` | Only `off` observed; HA modes likely `off`, `heat`, (`cool`, `auto`, `fan_only`, `dry` on AC models) |
| `fan_mode_state` | enum string | `auto`, `low` | Absent on `Drain thy tank` (`e831cd985b88`) — likely no fan (a different Mysa model) |
| `action` | enum string | `off` | HA climate actions: `off`, `idle`, `heating`, `cooling`, ... |
| `temperature_state` | float or `None` | `None` | Setpoint. Literal string `None` (Python `str(None)`) when unset/off — must be handled by parsers |

## Sensor topics

Base: `mysa2mqtt/sensor/<device_name>/mysa_<device_id>_<measurement>/state`

| Measurement | Type | Notes |
|---|---|---|
| `temperature` | float, 2 decimals | Duplicates `current_temperature` (`16.20` vs `16.2`) |
| `humidity` | float, 2 decimals | Duplicates `current_humidity` (`54.00` vs `54.0`) |

## Devices observed

| Name (decoded) | Topic name | Device ID | Fan mode |
|---|---|---|---|
| Ochin | `Ochin` | `24dcc38096c8` | yes |
| Living Room | `Living-20Room` | `24dcc380aab4` | yes |
| Master Bedroom | `Master-20Bedroom` | `24dcc3818a28` | yes |
| Basement | `Basement` | `24dcc381b27c` | yes |
| Drain thy tank | `Drain-20thy-20tank` | `e831cd985b88` | no |

## Behavior observations

- On subscribe, each device publishes its full state (≈7 climate/sensor topics, 2 more humidity sensors grouped at the end) — this looks like a burst of retained messages or a full re-publish cycle (the capture shows two full cycles).
- Updates are re-published even when the value is unchanged, so don't treat a message as a change event.
- Message order within a cycle isn't guaranteed (e.g. `action` before `current_temperature` for one device in cycle 2).
- Not seen in the capture (likely exist given `*_state` naming, per the HA MQTT climate convention): `mode_command`, `fan_mode_command`, `temperature_command`, availability topics, and `homeassistant/...` discovery configs. Capture them separately with `mosquitto_sub -t 'mysa2mqtt/#' -t 'homeassistant/#' -v` before relying on them.

## Subscription patterns

```
mysa2mqtt/#                                   # everything
mysa2mqtt/climate/+/+/current_temperature     # all temps
mysa2mqtt/climate/<name>/+/#                  # one device
mysa2mqtt/sensor/+/+/state                    # all sensor values
```

## Parsing regex

```python
TOPIC_RE = re.compile(
    r"^mysa2mqtt/(?P<type>climate|sensor)/(?P<name>[^/]+)/mysa_(?P<id>[0-9a-f]{12})_(?P<kind>climate|temperature|humidity)/(?P<attr>[^/]+)$"
)
name = re.sub(r"-([0-9A-Fa-f]{2})", lambda m: chr(int(m[1], 16)), m["name"])
```
