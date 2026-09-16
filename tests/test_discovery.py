"""Station discovery at the Toolkit boundary, without BLE or watering."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.solem_bluetooth_watering_controller.discovery import (
    async_discover_station_details, merge_station_details,
)
from custom_components.solem_bluetooth_watering_controller.config_flow import validate_input
from custom_components.solem_bluetooth_watering_controller.coordinator import SolemCoordinator

DATA = {"controller_mac_address": "Controller - AA:BB:CC:DD:EE:FF", "num_stations": 1}
METADATA = {
    "station_count": 6, "station_count_source": "v5_identification_experimental",
    "station_names": {str(i): f"Garden {i}" for i in range(1, 13)}, "firmware": "5.1.7",
    "identification_frames": ["private"], "name_frames": ["private"],
}


def test_detected_count_replaces_old_default_and_ignores_unused_slots():
    data = {**DATA, "station_areas": [10], "sensors": "zone.home"}
    result = merge_station_details(data, METADATA)
    assert result["num_stations"] == 6
    assert result["station_names"] == {str(i): f"Garden {i}" for i in range(1, 7)}
    assert result["station_areas"] == [10, 0, 0, 0, 0, 0]
    assert result["sensors"] == "zone.home"
    assert "name_frames" not in result and "identification_frames" not in result
    assert data["num_stations"] == 1 and data["station_areas"] == [10]


def test_unknown_profile_keeps_manual_count_and_names_unnamed_stations():
    result = merge_station_details(DATA, {**METADATA, "station_count": None, "station_names": {"1": ""}})
    assert result["num_stations"] == 1
    assert result["station_names"] == {"1": "Station 1"}
    assert result["station_count_source"] == "manual"


@pytest.mark.parametrize("metadata", [
    {**METADATA, "station_names": {"1": "Garden"}},
    {**METADATA, "station_names": None},
])
def test_partial_names_do_not_replace_config(metadata):
    with pytest.raises(HomeAssistantError, match="Incomplete"):
        merge_station_details(DATA, metadata)


def test_auto_count_requires_recognized_metadata():
    with pytest.raises(HomeAssistantError, match="enter it manually"):
        merge_station_details({**DATA, "num_stations": 0}, {**METADATA, "station_count": None})


async def test_setup_validation_uses_read_only_metadata_and_fills_count():
    hass = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock(return_value=METADATA)))
    data = {**DATA, "num_stations": 0}
    await validate_input(hass, data)
    assert data["num_stations"] == 6
    assert data["station_names"]["2"] == "Garden 2"
    hass.services.async_call.assert_awaited_once_with(
        "solem_toolkit", "read_metadata",
        {"device_mac": "AA:BB:CC:DD:EE:FF", "bluetooth_timeout": 15},
        blocking=True, return_response=True,
    )


async def test_discovery_propagates_failure_without_mutating_data():
    hass = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock(side_effect=HomeAssistantError("offline"))))
    data = dict(DATA)
    with pytest.raises(HomeAssistantError, match="offline"):
        await async_discover_station_details(hass, data)
    assert data == DATA


def test_station_display_name_has_manual_fallback():
    coordinator = object.__new__(SolemCoordinator)
    coordinator.config_entry = SimpleNamespace(data={"station_names": {"1": "Front lawn"}})
    assert coordinator.station_name(1) == "Front lawn"
    assert coordinator.station_name(2) == "Station 2"


@pytest.mark.parametrize("reply", [
    HomeAssistantError("Metadata not supported"),
    {"station_count": None, "station_names": {}},
    None,
])
async def test_manual_setup_works_without_name_support_after_connectivity_check(reply):
    hass = SimpleNamespace(data={}, services=SimpleNamespace(async_call=AsyncMock(side_effect=[reply, None])))
    data = {**DATA, "num_stations": 2}
    await validate_input(hass, data)
    assert data["num_stations"] == 2
    assert data["station_names"] == {"1": "Station 1", "2": "Station 2"}
    assert data["station_count_source"] == "manual"
    assert [c.args[1] for c in hass.services.async_call.await_args_list] == ["read_metadata", "list_characteristics"]


async def test_manual_setup_still_rejects_unreachable_device():
    from custom_components.solem_bluetooth_watering_controller.config_flow import CannotConnect

    hass = SimpleNamespace(data={}, services=SimpleNamespace(async_call=AsyncMock(side_effect=HomeAssistantError("offline"))))
    data = dict(DATA)
    with pytest.raises(CannotConnect):
        await validate_input(hass, data)
    assert data == DATA
    assert hass.services.async_call.await_count == 2


async def test_automatic_setup_cannot_silently_fall_back_to_one_station():
    from custom_components.solem_bluetooth_watering_controller.config_flow import CannotConnect

    hass = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock(side_effect=HomeAssistantError("unsupported"))))
    with pytest.raises(CannotConnect):
        await validate_input(hass, {**DATA, "num_stations": 0})
    assert hass.services.async_call.await_count == 1


async def test_refresh_remains_strict_and_keeps_cached_names():
    from custom_components.solem_bluetooth_watering_controller.button import RefreshStationDetailsButton
    from unittest.mock import Mock

    data = {**DATA, "station_names": {"1": "Existing garden"}}
    hass = SimpleNamespace(
        services=SimpleNamespace(async_call=AsyncMock(return_value={"station_names": {}})),
        config_entries=SimpleNamespace(async_update_entry=Mock()),
    )
    coordinator = SimpleNamespace(controller_mac_address="AA:BB:CC:DD:EE:FF", bluetooth_timeout=15,
                                  config_entry=SimpleNamespace(data=data))
    button = RefreshStationDetailsButton(coordinator)
    button.hass = hass
    with pytest.raises(HomeAssistantError, match="Incomplete"):
        await button.async_press()
    hass.config_entries.async_update_entry.assert_not_called()
    assert data["station_names"] == {"1": "Existing garden"}
