#!/usr/bin/env python3
"""
Mysa MQTT Terminal Dashboard (`mysa_dashboard.py`)

Connects to the local MQTT broker (`homeassistant/#` and `mysa2mqtt/#`) and renders
an in-place updating terminal dashboard for all discovered Mysa thermostats using Python's
built-in `curses` character-positioning module.
"""

from __future__ import annotations

import argparse
import curses
import datetime
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt
from dotenv import load_dotenv


def unescape_topic_name(escaped: str) -> str:
    """
    Reverses `mqtt2ha`'s `cleanString` hex-escaping (e.g. `Drain-20thy-20tank` -> `Drain thy tank`).
    """
    try:
        raw_bytes = bytearray()
        i = 0
        while i < len(escaped):
            if escaped[i] == "-" and i + 2 < len(escaped):
                hex_part = escaped[i + 1 : i + 3]
                if all(c in "0123456789ABCDEFabcdef" for c in hex_part):
                    raw_bytes.append(int(hex_part, 16))
                    i += 3
                    continue
            raw_bytes.extend(escaped[i].encode("utf-8"))
            i += 1
        return raw_bytes.decode("utf-8", errors="replace")
    except Exception:
        return escaped


def c_to_f(celsius: float) -> float:
    return (celsius * 9.0 / 5.0) + 32.0


@dataclass
class ThermostatView:
    device_id: str
    pretty_name: str
    model: str = "-"
    sw_version: str = "-"
    serial_number: str = "-"
    mode: str = "-"
    action: str = "-"
    current_temp_c: Optional[float] = None
    target_temp_c: Optional[float] = None
    humidity: Optional[float] = None
    fan_mode: Optional[str] = None
    power_w: Optional[float] = None
    floor_temp_c: Optional[float] = None
    availability: str = "unknown"
    last_updated: str = "-"
    last_updated_ts: Optional[float] = None
    update_count: int = 0


class DashboardState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.devices: Dict[str, ThermostatView] = {}
        self.broker_connected: bool = False
        self.broker_host: str = "localhost"
        self.broker_port: int = 1883
        self.total_messages: int = 0
        self.last_topic: str = "-"
        self.display_fahrenheit: bool = True

    def get_or_create(self, device_id: str, fallback_name: str) -> ThermostatView:
        if device_id not in self.devices:
            self.devices[device_id] = ThermostatView(
                device_id=device_id,
                pretty_name=fallback_name,
            )
        return self.devices[device_id]


# Topic regexes:
# homeassistant/<component>/<escaped_device_name>/mysa_<device_id>_<suffix>/config
RE_DISCOVERY = re.compile(
    r"^[^/]+/(?P<component>[^/]+)/(?P<dev_name>[^/]+)/mysa_(?P<dev_id>[a-fA-F0-9]+)_(?P<entity>[^/]+)/config$"
)
# mysa2mqtt/<component>/<escaped_device_name>/mysa_<device_id>_<suffix>/<prop>
RE_STATE = re.compile(
    r"^[^/]+/(?P<component>[^/]+)/(?P<dev_name>[^/]+)/mysa_(?P<dev_id>[a-fA-F0-9]+)_(?P<entity>[^/]+)/(?P<prop>.+)$"
)


def parse_float(val: str) -> Optional[float]:
    val = val.strip()
    if not val or val == "None":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def on_mqtt_message(state: DashboardState, topic: str, payload_bytes: bytes) -> None:
    payload = payload_bytes.decode("utf-8", errors="replace").strip()
    now_ts = time.time()
    now_str = datetime.datetime.now().strftime("%H:%M:%S")

    with state.lock:
        state.total_messages += 1
        state.last_topic = topic

        m_disc = RE_DISCOVERY.match(topic)
        if m_disc:
            if not payload:
                return
            try:
                cfg: Dict[str, Any] = json.loads(payload)
            except Exception:
                return
            dev_info = cfg.get("device") or {}
            dev_id = str(dev_info.get("identifiers") or m_disc.group("dev_id"))
            pretty_name = str(dev_info.get("name") or unescape_topic_name(m_disc.group("dev_name")))
            view = state.get_or_create(dev_id, pretty_name)
            view.pretty_name = pretty_name
            if dev_info.get("model"):
                view.model = str(dev_info["model"])
            if dev_info.get("sw_version"):
                view.sw_version = str(dev_info["sw_version"])
            if dev_info.get("serial_number"):
                view.serial_number = str(dev_info["serial_number"])
            view.last_updated = now_str
            view.last_updated_ts = now_ts
            return

        m_state = RE_STATE.match(topic)
        if m_state:
            dev_id = m_state.group("dev_id")
            fallback_name = unescape_topic_name(m_state.group("dev_name"))
            entity = m_state.group("entity")
            prop = m_state.group("prop")

            view = state.get_or_create(dev_id, fallback_name)
            view.last_updated = now_str
            view.last_updated_ts = now_ts
            view.update_count += 1

            if entity == "climate":
                if prop == "availability":
                    view.availability = payload
                elif prop == "mode_state":
                    view.mode = payload if payload != "None" else "off"
                elif prop == "action":
                    view.action = payload
                elif prop == "current_temperature":
                    view.current_temp_c = parse_float(payload)
                elif prop == "temperature_state":
                    view.target_temp_c = parse_float(payload)
                elif prop == "current_humidity":
                    view.humidity = parse_float(payload)
                elif prop == "fan_mode_state":
                    view.fan_mode = payload if payload != "None" else "-"
            elif entity == "temperature" and prop == "state":
                val = parse_float(payload)
                if val is not None:
                    view.current_temp_c = val
            elif entity == "humidity" and prop == "state":
                val = parse_float(payload)
                if val is not None:
                    view.humidity = val
            elif entity == "power" and prop == "state":
                view.power_w = parse_float(payload)
            elif entity == "floor_temperature" and prop == "state":
                view.floor_temp_c = parse_float(payload)


def format_temp(temp_c: Optional[float], use_fahrenheit: bool) -> str:
    if temp_c is None:
        return "  --  "
    if use_fahrenheit:
        return f"{c_to_f(temp_c):5.1f}°F ({temp_c:4.1f}°C)"
    return f"{temp_c:5.1f}°C ({c_to_f(temp_c):4.1f}°F)"


def format_elapsed(last_updated_ts: Optional[float], now_ts: float) -> str:
    if last_updated_ts is None:
        return ""
    elapsed = max(0, int(now_ts - last_updated_ts))
    if elapsed < 60:
        return f"({elapsed}s)"
    if elapsed < 3600:
        mins, secs = divmod(elapsed, 60)
        return f"({mins}m {secs:02d}s)"
    hours, rem = divmod(elapsed, 3600)
    mins = rem // 60
    return f"({hours}h {mins:02d}m)"


def safe_addstr(stdscr: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    """Writes text at (y, x) clipped safely to terminal bounds."""
    max_y, max_x = stdscr.getmaxyx()
    if y < 0 or y >= max_y or x >= max_x:
        return
    if x < 0:
        text = text[-x:]
        x = 0
    clipped = text[: max_x - x - 1]
    if not clipped:
        return
    try:
        stdscr.addstr(y, x, clipped, attr)
    except curses.error:
        pass


def run_curses_ui(stdscr: curses.window, state: DashboardState) -> None:
    curses.curs_set(0)  # Hide cursor
    stdscr.nodelay(True)
    stdscr.timeout(200)  # Refresh UI every 200ms

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)     # Headers / Borders
    curses.init_pair(2, curses.COLOR_GREEN, -1)    # Online / Idle / OK
    curses.init_pair(3, curses.COLOR_RED, -1)      # Heating / Offline
    curses.init_pair(4, curses.COLOR_YELLOW, -1)   # Warning / Fan / Dry
    curses.init_pair(5, curses.COLOR_BLUE, -1)     # Cooling
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)  # Accent / Device Name

    COLOR_HEADER = curses.color_pair(1) | curses.A_BOLD
    COLOR_ONLINE = curses.color_pair(2) | curses.A_BOLD
    COLOR_HEATING = curses.color_pair(3) | curses.A_BOLD
    COLOR_WARN = curses.color_pair(4) | curses.A_BOLD
    COLOR_COOLING = curses.color_pair(5) | curses.A_BOLD
    COLOR_NAME = curses.color_pair(6) | curses.A_BOLD

    while True:
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q")):
            break
        elif ch in (ord("u"), ord("U"), ord("f"), ord("F"), ord("c"), ord("C")):
            with state.lock:
                state.display_fahrenheit = not state.display_fahrenheit

        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()

        with state.lock:
            connected = state.broker_connected
            host = state.broker_host
            port = state.broker_port
            total_msgs = state.total_messages
            last_topic = state.last_topic
            use_f = state.display_fahrenheit
            devices = sorted(state.devices.values(), key=lambda d: d.pretty_name.lower())

        # Top Banner
        now_clock = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = " MYSA SMART THERMOSTAT MQTT DASHBOARD "
        safe_addstr(stdscr, 0, 2, title, COLOR_HEADER | curses.A_REVERSE)
        safe_addstr(stdscr, 0, len(title) + 4, f"Time: {now_clock}", curses.A_DIM)

        broker_status = "CONNECTED" if connected else "DISCONNECTED"
        broker_attr = COLOR_ONLINE if connected else COLOR_HEATING
        safe_addstr(stdscr, 1, 2, f"MQTT Broker: {host}:{port} [")
        safe_addstr(stdscr, 1, 17 + len(f"{host}:{port}"), broker_status, broker_attr)
        safe_addstr(
            stdscr,
            1,
            17 + len(f"{host}:{port}") + len(broker_status),
            f"]   Messages: {total_msgs}   Primary Unit: {'°F' if use_f else '°C'}",
        )

        # Table Header
        sep_line = "─" * max(10, min(max_x - 4, 128))
        safe_addstr(stdscr, 3, 2, sep_line, COLOR_HEADER)
        header_row = (
            f"{'THERMOSTAT NAME':<18} "
            f"{'STATUS':<8} "
            f"{'MODE':<8} "
            f"{'ACTION':<9} "
            f"{'CURRENT TEMP':<17} "
            f"{'SETPOINT':<17} "
            f"{'HUMID':<7} "
            f"{'FAN':<7} "
            f"{'MODEL':<9} "
            f"{'UPDATED':<18}"
        )
        safe_addstr(stdscr, 4, 2, header_row, COLOR_HEADER)
        safe_addstr(stdscr, 5, 2, sep_line, COLOR_HEADER)

        if not devices:
            safe_addstr(
                stdscr,
                7,
                4,
                "Waiting for Mysa MQTT messages on 'homeassistant/#' and 'mysa2mqtt/#'...",
                COLOR_WARN,
            )
        else:
            now_ts = time.time()
            row_y = 6
            for dev in devices:
                if row_y >= max_y - 4:
                    break

                # Status color
                avail_str = dev.availability.upper()[:7]
                avail_attr = COLOR_ONLINE if dev.availability == "online" else COLOR_HEATING

                # Mode & Action color
                action_str = dev.action.upper()[:8]
                if dev.action == "heating":
                    action_attr = COLOR_HEATING
                elif dev.action == "cooling":
                    action_attr = COLOR_COOLING
                elif dev.action in ("fan", "drying"):
                    action_attr = COLOR_WARN
                elif dev.action == "idle":
                    action_attr = COLOR_ONLINE
                else:
                    action_attr = curses.A_DIM

                mode_str = dev.mode.upper()[:7]
                mode_attr = curses.A_BOLD if dev.mode not in ("off", "-") else curses.A_DIM

                curr_temp_str = format_temp(dev.current_temp_c, use_f)
                target_temp_str = format_temp(dev.target_temp_c, use_f) if dev.target_temp_c is not None else "  Off           "
                hum_str = f"{dev.humidity:4.0f}%" if dev.humidity is not None else "  -- "
                fan_str = (dev.fan_mode or "-")[:6]
                elapsed_str = format_elapsed(dev.last_updated_ts, now_ts)
                updated_str = f"{dev.last_updated} {elapsed_str}".strip()

                # Position each column explicitly with curses
                x = 2
                safe_addstr(stdscr, row_y, x, f"{dev.pretty_name[:18]:<18}", COLOR_NAME)
                x += 19
                safe_addstr(stdscr, row_y, x, f"{avail_str:<8}", avail_attr)
                x += 9
                safe_addstr(stdscr, row_y, x, f"{mode_str:<8}", mode_attr)
                x += 9
                safe_addstr(stdscr, row_y, x, f"{action_str:<9}", action_attr)
                x += 10
                safe_addstr(stdscr, row_y, x, f"{curr_temp_str:<17}", curses.A_BOLD)
                x += 18
                safe_addstr(
                    stdscr,
                    row_y,
                    x,
                    f"{target_temp_str:<17}",
                    COLOR_HEATING if dev.action == "heating" else ( curses.A_NORMAL if dev.target_temp_c else curses.A_DIM ),
                )
                x += 18
                safe_addstr(stdscr, row_y, x, f"{hum_str:<7}")
                x += 8
                safe_addstr(stdscr, row_y, x, f"{fan_str:<7}")
                x += 8
                safe_addstr(stdscr, row_y, x, f"{dev.model[:9]:<9}", curses.A_DIM)
                x += 10
                safe_addstr(stdscr, row_y, x, f"{updated_str:<18}", curses.A_DIM)

                # Sub-row with hardware metadata (ID, Serial, Firmware, Power if present)
                meta_parts = [f"ID: {dev.device_id}", f"SN: {dev.serial_number}", f"FW: {dev.sw_version}"]
                if dev.power_w is not None:
                    meta_parts.append(f"Power: {dev.power_w:.0f}W")
                if dev.floor_temp_c is not None:
                    meta_parts.append(f"Floor: {format_temp(dev.floor_temp_c, use_f).strip()}")
                safe_addstr(stdscr, row_y + 1, 4, "└─ " + "  |  ".join(meta_parts), curses.A_DIM)

                row_y += 3

        # Footer
        footer_y = max_y - 2
        if footer_y > 6:
            safe_addstr(stdscr, footer_y - 1, 2, sep_line, COLOR_HEADER)
            safe_addstr(
                stdscr,
                footer_y,
                2,
                f"Controls: [q] Quit   [u] Toggle °F/°C   |   Last topic: {last_topic}",
                curses.A_DIM,
            )

        stdscr.refresh()


def main() -> int:
    load_dotenv(".env")
    load_dotenv(".env.local", override=True)

    parser = argparse.ArgumentParser(description="Live curses terminal dashboard for mysa2mqtt.")
    parser.add_argument(
        "-H",
        "--mqtt-host",
        default=os.environ.get("M2M_MQTT_HOST", "localhost"),
        help="MQTT broker hostname (default: localhost or M2M_MQTT_HOST)",
    )
    parser.add_argument(
        "-P",
        "--mqtt-port",
        type=int,
        default=int(os.environ.get("M2M_MQTT_PORT", "1883")),
        help="MQTT broker port (default: 1883 or M2M_MQTT_PORT)",
    )
    parser.add_argument(
        "-U",
        "--mqtt-username",
        default=os.environ.get("M2M_MQTT_USERNAME"),
        help="MQTT broker username",
    )
    parser.add_argument(
        "-B",
        "--mqtt-password",
        default=os.environ.get("M2M_MQTT_PASSWORD"),
        help="MQTT broker password",
    )
    parser.add_argument(
        "--discovery-prefix",
        default="homeassistant",
        help="Home Assistant discovery topic prefix (default: homeassistant)",
    )
    parser.add_argument(
        "-T",
        "--topic-prefix",
        default=os.environ.get("M2M_MQTT_TOPIC_PREFIX", "mysa2mqtt"),
        help="mysa2mqtt state topic prefix (default: mysa2mqtt)",
    )
    args = parser.parse_args()

    state = DashboardState()
    state.broker_host = args.mqtt_host
    state.broker_port = args.mqtt_port
    state.display_fahrenheit = os.environ.get("M2M_TEMPERATURE_UNIT", "F").upper() == "F"

    client_id = f"mysa-dashboard-{os.getpid()}-{secrets.token_hex(4)}"
    mqtt_client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )
    if args.mqtt_username:
        mqtt_client.username_pw_set(args.mqtt_username, args.mqtt_password)

    def on_connect(c: mqtt.Client, _userdata: Any, _flags: Any, rc: Any, _props: Any = None) -> None:
        with state.lock:
            state.broker_connected = rc == 0
        if rc == 0:
            c.subscribe(f"{args.discovery_prefix}/#")
            c.subscribe(f"{args.topic_prefix}/#")

    def on_disconnect(_c: mqtt.Client, _userdata: Any, _flags: Any, _rc: Any, _props: Any = None) -> None:
        with state.lock:
            state.broker_connected = False

    def on_message(_c: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        on_mqtt_message(state, msg.topic, msg.payload)

    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    mqtt_client.connect_async(args.mqtt_host, args.mqtt_port, keepalive=60)
    mqtt_client.loop_start()

    try:
        curses.wrapper(run_curses_ui, state)
    except KeyboardInterrupt:
        pass
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

