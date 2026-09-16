"""Experimental discovery of controller station details."""

from homeassistant.exceptions import HomeAssistantError

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


async def async_discover_station_details(hass, data: dict, timeout: int = 15) -> dict:
    """Read metadata through the Toolkit's serialized BLE service."""
    response = await hass.services.async_call(
        "solem_toolkit", "read_metadata",
        {"device_mac": data[CONTROLLER_MAC_ADDRESS].rsplit(" - ", 1)[-1], "bluetooth_timeout": timeout},
        blocking=True, return_response=True,
    )
    if not isinstance(response, dict):
        raise HomeAssistantError("Toolkit did not return station metadata")
    return merge_station_details(data, response)
