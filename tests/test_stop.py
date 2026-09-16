"""Regression tests for stop delivery and button error/state handling.

Use the real HA exception/entity/coordinator classes; mock the toolkit service
boundary so no test can operate physical irrigation hardware.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.solem_bluetooth_watering_controller import api as api_module
from custom_components.solem_bluetooth_watering_controller.api import APIConnectionError, SolemAPI
from custom_components.solem_bluetooth_watering_controller.button import IrrigationStopButton
from custom_components.solem_bluetooth_watering_controller.coordinator import SolemCoordinator

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def hass():
    return SimpleNamespace(data={}, services=SimpleNamespace(async_call=AsyncMock()))


@pytest.fixture
def api(hass, monkeypatch):
    monkeypatch.setattr(api_module, "_STOP_RETRY_DELAY", 0, raising=False)
    return SolemAPI(hass, MAC, 15)


@pytest.fixture
def coordinator():
    # Avoid startup's BLE, weather, storage and scheduled irrigation side effects.
    result = object.__new__(SolemCoordinator)
    result.controller_mac_address = MAC
    result.api = SimpleNamespace(stop_manual_sprinkle=AsyncMock())
    result.irrigation_stop_event = asyncio.Event()
    result.num_stations = 6
    result.stations = [
        SimpleNamespace(device_id=f"station_{i}", state="Sprinkling" if i == 1 else "Stopped")
        for i in range(1, 7)
    ]
    result.data = [
        {"device_id": station.device_id, "state": station.state}
        for station in result.stations
    ] + [{"device_id": "rain", "state": False}]
    result.async_set_updated_data = Mock()
    result.async_update_all_sensors = AsyncMock(side_effect=RuntimeError("weather offline"))
    return result


async def test_stop_retries_transient_failures_with_same_payload(api, hass):
    hass.services.async_call.side_effect = [
        HomeAssistantError("proxy busy"), HomeAssistantError("timeout"), None
    ]
    await api.stop_manual_sprinkle()
    assert hass.services.async_call.await_count == 3
    for call in hass.services.async_call.await_args_list:
        assert call.args == (
            "solem_toolkit", "stop_manual_sprinkle",
            {"device_mac": MAC, "bluetooth_timeout": 15},
        )
        assert call.kwargs == {"blocking": True}


@pytest.mark.parametrize("error, attempts", [
    pytest.param(HomeAssistantError("proxy out of slots"), 3, id="exhausted"),
    pytest.param(ServiceValidationError("invalid service data"), 1, id="invalid-input"),
])
async def test_stop_errors_preserve_cause_and_retry_count(api, hass, error, attempts):
    hass.services.async_call.side_effect = error
    with pytest.raises(APIConnectionError, match=str(error)):
        await api.stop_manual_sprinkle()
    assert hass.services.async_call.await_count == attempts


async def test_start_is_not_replayed_after_error(api, hass):
    hass.services.async_call.side_effect = HomeAssistantError("timeout")
    with pytest.raises(APIConnectionError):
        await api.sprinkle_station_x_for_y_minutes(1, 1)
    assert hass.services.async_call.await_count == 1


async def test_same_controller_calls_do_not_overlap_even_across_adapters(api, hass):
    entered, release = asyncio.Event(), asyncio.Event()
    services = []

    async def call(domain, service, payload, **kwargs):
        services.append(service)
        if service == "sprinkle_station_x_for_y_minutes":
            entered.set()
            await release.wait()

    hass.services.async_call.side_effect = call
    start = asyncio.create_task(api.sprinkle_station_x_for_y_minutes(1, 1))
    await entered.wait()
    other = SolemAPI(hass, MAC.lower(), 15)
    stop = asyncio.create_task(other.stop_manual_sprinkle())
    await asyncio.sleep(0)
    assert services == ["sprinkle_station_x_for_y_minutes"]
    release.set()
    await asyncio.gather(start, stop)
    assert services == ["sprinkle_station_x_for_y_minutes", "stop_manual_sprinkle"]


async def test_other_controller_is_not_blocked(api, hass):
    entered, release = asyncio.Event(), asyncio.Event()

    async def call(domain, service, payload, **kwargs):
        if payload["device_mac"] == MAC:
            entered.set()
            await release.wait()

    hass.services.async_call.side_effect = call
    first = asyncio.create_task(api.stop_manual_sprinkle())
    await entered.wait()
    await asyncio.wait_for(SolemAPI(hass, "11:22:33:44:55:66", 15).stop_manual_sprinkle(), 1)
    release.set()
    await first


async def test_cancellation_does_not_retry_and_releases_lock(api, hass):
    entered, release = asyncio.Event(), asyncio.Event()

    async def call(*args, **kwargs):
        entered.set()
        await release.wait()

    hass.services.async_call.side_effect = call
    task = asyncio.create_task(api.stop_manual_sprinkle())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert hass.services.async_call.await_count == 1
    hass.services.async_call.side_effect = None
    await asyncio.wait_for(api.stop_manual_sprinkle(), 1)


async def test_mock_never_sends_command(api, hass):
    api.mock = True
    await api.stop_manual_sprinkle()
    hass.services.async_call.assert_not_awaited()


async def test_button_waits_for_stop_completion(coordinator):
    entered, release = asyncio.Event(), asyncio.Event()

    async def stop():
        entered.set()
        await release.wait()

    coordinator.api.stop_manual_sprinkle.side_effect = stop
    # The press method only needs the coordinator, not an attached HA platform.
    button = SimpleNamespace(coordinator=coordinator)
    press = asyncio.create_task(IrrigationStopButton.async_press(button))
    await entered.wait()
    assert not press.done()
    release.set()
    await press


async def test_button_reports_failure_and_keeps_running_state(coordinator):
    coordinator.api.stop_manual_sprinkle.side_effect = APIConnectionError("proxy unreachable")
    button = SimpleNamespace(coordinator=coordinator)
    with pytest.raises(HomeAssistantError, match="proxy unreachable"):
        await IrrigationStopButton.async_press(button)
    assert not coordinator.irrigation_stop_event.is_set()
    assert coordinator.stations[0].state == "Sprinkling"
    coordinator.async_set_updated_data.assert_not_called()


@pytest.mark.parametrize("failures", [[], [HomeAssistantError("BLE connection timed out")]],
                         ids=["first-attempt", "recovered"])
async def test_stop_updates_all_six_zones_without_weather(coordinator, api, hass, failures):
    coordinator.api = api
    hass.services.async_call.side_effect = [*failures, None]
    await IrrigationStopButton.async_press(SimpleNamespace(coordinator=coordinator))
    assert hass.services.async_call.await_count == len(failures) + 1
    assert coordinator.irrigation_stop_event.is_set()
    assert all(station.state == "Stopped" for station in coordinator.stations)
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert [device["state"] for device in published[:6]] == ["Stopped"] * 6
    assert published[6] == {"device_id": "rain", "state": False}
    # No in-place mutation: coordinator listeners must see changed data.
    assert coordinator.data[0]["state"] == "Sprinkling"
    coordinator.async_update_all_sensors.assert_not_awaited()


async def test_no_extra_water_accounted_after_stop_during_timer_sleep(coordinator, monkeypatch):
    coordinator.irrigation_manual_duration = 1
    coordinator.api.sprinkle_station_x_for_y_minutes = AsyncMock()
    coordinator.async_update_all_sensors = AsyncMock(return_value=coordinator.data)
    coordinator.total_water_consumption = 0
    coordinator.water_flow_rate = [12] * 6
    coordinator.station_areas = [10] * 6
    coordinator.sprinkle_total_amount_today = [0.0] * 6

    async def sleep_then_stop(seconds):
        await coordinator.stop_irrigation()

    monkeypatch.setattr(
        "custom_components.solem_bluetooth_watering_controller.coordinator.sleep",
        sleep_then_stop,
    )
    await coordinator.start_irrigation(1)
    assert coordinator.total_water_consumption == 0
    assert coordinator.sprinkle_total_amount_today == [0.0] * 6
    assert coordinator.stations[0].state == "Stopped"
