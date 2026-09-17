"""Validate dashboard schedules before replacing any saved data or timers."""
from homeassistant.exceptions import HomeAssistantError

from .util import parse_time_string


def validate_schedule(schedule, num_stations):
    if not isinstance(schedule, list) or len(schedule) != 12:
        raise HomeAssistantError("Schedule must contain exactly 12 months")
    keys = {f"station_{i}_minutes" for i in range(1, num_stations + 1)}
    result = []
    for month in schedule:
        if not isinstance(month, dict):
            raise HomeAssistantError("Each month must contain an interval, times and stations")
        interval, hours, stations = (month.get(k) for k in ("interval_days", "hours", "stations"))
        if type(interval) is not int or not 0 <= interval <= 365:
            raise HomeAssistantError("Watering interval must be 0–365 whole days")
        if not isinstance(hours, list) or len(hours) > 24:
            raise HomeAssistantError("Each month supports up to 24 start times")
        try:
            hours = sorted({parse_time_string(h).strftime("%H:%M:%S") for h in hours})
        except ValueError as exc:
            raise HomeAssistantError(str(exc)) from exc
        if not isinstance(stations, dict) or stations.keys() != keys:
            raise HomeAssistantError("Schedule stations do not match this controller; reload the card")
        if any(type(v) is not int or not 0 <= v <= 720 for v in stations.values()):
            raise HomeAssistantError("Station durations must be 0–720 whole minutes")
        result.append({"interval_days": interval, "hours": hours, "stations": dict(stations)})
    return result
