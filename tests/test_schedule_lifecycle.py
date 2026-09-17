"""Exercise cancellation with Home Assistant's actual config-entry lifecycle."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from custom_components.solem_bluetooth_watering_controller import coordinator as module
from custom_components.solem_bluetooth_watering_controller.coordinator import SolemCoordinator


def make_coordinator(hass):
    entry = ConfigEntry(version=1, minor_version=1, domain="solem_bluetooth_watering_controller",
                        title="Test", data={}, options={}, source="user", unique_id="test",
                        discovery_keys={}, subentries_data=[])
    coordinator = object.__new__(SolemCoordinator)
    coordinator.hass = hass
    coordinator.config_entry = entry
    coordinator.controller_mac_address = "AA:BB:CC:DD:EE:FF"
    coordinator._watering_timers = []
    entry.async_on_unload(coordinator._cancel_watering_timers)
    coordinator.num_stations = 1
    coordinator.water_flow_rate = [12]
    coordinator.station_areas = [1]
    coordinator.rain_total_amount_forecasted_today = 0
    coordinator.schedule = [{"hours": ["12:00"], "interval_days": 0, "stations": {"station_1_minutes": 1}}] * 12
    coordinator.last_rain = coordinator.last_sprinkle = dt_util.now() - timedelta(days=1)
    coordinator.needs_watering_today = lambda: True
    return coordinator


async def test_reload_cancels_daily_listeners_and_pending_watering(monkeypatch):
    active_daily, active_watering = [], []

    def tracker(active):
        def register(*args, **kwargs):
            token = object()
            active.append(token)
            return lambda: active.remove(token)
        return register

    monkeypatch.setattr(module, "async_track_time_change", tracker(active_daily))
    monkeypatch.setattr(module, "async_call_later", tracker(active_watering))
    now = dt_util.now().replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(module.dt_util, "now", lambda: now)
    hass = SimpleNamespace(data={})
    old = make_coordinator(hass)
    await old.setup_scheduled_tasks()
    await old.check_and_schedule_watering()
    assert len(active_daily) == 2 and len(active_watering) == 1

    await old.config_entry._async_process_on_unload(hass)
    assert active_daily == active_watering == []

    new = make_coordinator(hass)
    await new.setup_scheduled_tasks()
    await new.check_and_schedule_watering()
    assert len(active_daily) == 2 and len(active_watering) == 1
    await new.config_entry._async_process_on_unload(hass)
    assert active_daily == active_watering == []


async def test_unload_cancels_initialization_before_it_registers_callbacks(monkeypatch):
    import asyncio

    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def initialize(self):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    hass = SimpleNamespace(
        data={}, states=SimpleNamespace(get=lambda entity: None),
        async_create_background_task=lambda target, name, eager_start: asyncio.create_task(target),
    )
    entry = ConfigEntry(version=1, minor_version=1, domain="solem_bluetooth_watering_controller",
                        title="Test", data={"controller_mac_address": "Test - AA:BB:CC:DD:EE:FF",
                                            "sprinkle_with_rain": "false", "sensors": "zone.home"},
                        options={}, source="user", unique_id="test", discovery_keys={}, subentries_data=[])
    monkeypatch.setattr(module.DataUpdateCoordinator, "__init__",
                        lambda self, hass, *args, **kwargs: setattr(self, "hass", hass))
    monkeypatch.setattr(module, "Store", Mock())
    monkeypatch.setattr(SolemCoordinator, "async_init", initialize)
    coordinator = SolemCoordinator(hass, entry)
    await entered.wait()
    await entry._async_process_on_unload(hass)
    assert coordinator.init_task.cancelled()
    assert cancelled.is_set()
