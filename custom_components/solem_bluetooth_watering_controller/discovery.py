"""Experimental discovery of controller station details."""

from homeassistant.exceptions import HomeAssistantError

from .api import SolemAPI
from .const import CONTROLLER_MAC_ADDRESS, NUM_STATIONS


def merge_station_details(data: dict, metadata: dict) -> dict:
    """Use a recognized count, otherwise retain the manually configured count."""
    detected = metadata.get("station_count")
    count = detected if type(detected) is int and 1 <= detected <= 12 else data.get(NUM_STATIONS, 0)
    if type(count) is not int or not 1 <= count <= 12:
        raise HomeAssistantError("Station count could not be detected; enter it manually")
    names = metadata.get("station_names", {})
    if not isinstance(names, dict) or any(not isinstance(names.get(str(i)), str) for i in range(1, count + 1)):
        raise HomeAssistantError("Incomplete station names; existing configuration retained")
    areas = data.get("station_areas", [])
    if not isinstance(areas, list):
        areas = []
    return {
        **data,
        NUM_STATIONS: count,
        "station_names": {str(i): names[str(i)].strip() or f"Station {i}" for i in range(1, count + 1)},
        "station_count_source": metadata.get("station_count_source", "unknown") if detected == count else "manual",
        "controller_firmware": metadata.get("firmware"),
        "station_areas": (areas + [0] * count)[:count],
    }


async def async_discover_station_details(
    hass, data: dict, timeout: int = 15, *, allow_manual_fallback: bool = False
) -> dict:
    """Read metadata; only setup may fall back to a verified manual count."""
    mac = data[CONTROLLER_MAC_ADDRESS].rsplit(" - ", 1)[-1]
    try:
        response = await hass.services.async_call(
            "solem_toolkit", "read_metadata",
            {"device_mac": mac, "bluetooth_timeout": timeout},
            blocking=True, return_response=True,
        )
        if not isinstance(response, dict):
            raise HomeAssistantError("Toolkit did not return station metadata")
        return merge_station_details(data, response)
    except HomeAssistantError:
        count = data.get(NUM_STATIONS, 0)
        if not allow_manual_fallback or type(count) is not int or not 1 <= count <= 12:
            raise
        # Unsupported names must not block manual setup, but a lost device must.
        await SolemAPI(hass, mac, timeout).connect()
        cached = data.get("station_names", {})
        return merge_station_details(data, {
            "station_names": {str(i): cached.get(str(i), f"Station {i}") for i in range(1, count + 1)},
            "firmware": data.get("controller_firmware"),
        })
