"""Saving schedules replaces future timers without issuing irrigation commands."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.solem_bluetooth_watering_controller import coordinator as module
from custom_components.solem_bluetooth_watering_controller.coordinator import SolemCoordinator
from custom_components.solem_bluetooth_watering_controller.schedule import validate_schedule


def schedule(hours=None, minutes=5):
    return [{"interval_days": 0, "hours": list(hours or []), "stations": {"station_1_minutes": minutes}} for _ in range(12)]


@pytest.fixture
async def coordinator(monkeypatch):
    now = datetime(2026, 7, 10, 10, tzinfo=ZoneInfo("Europe/Lisbon"))
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", now.tzinfo)
    monkeypatch.setattr(dt_util, "now", lambda: now)
    c = object.__new__(SolemCoordinator)
    c.hass = SimpleNamespace()
    c.controller_mac_address = "AA:BB:CC:DD:EE:FF"
    c.num_stations = 1
    c.schedule = schedule()
    c._watering_timers = []
    c._schedule_lock = asyncio.Lock()
    c.init_task = asyncio.get_running_loop().create_future()
    c.init_task.set_result(None)
    c.last_rain = c.last_sprinkle = now - timedelta(days=10)
    c.has_rained_today = c.will_it_rain_today = c.is_raining_now = False
    c.sprinkle_target_amount_today = [0]
    c.sprinkle_total_amount_today = [0]
    c.rain_total_amount_forecasted_today = 0
    c.water_flow_rate, c.station_areas = [12], [12]
    c.save_persistent_data = AsyncMock()
    c.async_update_all_sensors = AsyncMock(return_value=[])
    c.async_set_updated_data = Mock()
    c.run_watering_cycle = AsyncMock()
    c.active_timers = {}

    def register(hass, delay, action):
        token = object()
        c.active_timers[token] = (delay, action)
        return lambda: c.active_timers.pop(token, None)

    monkeypatch.setattr(module, "async_call_later", register)
    return c


async def test_save_replaces_timers_and_recomputes_targets_in_local_time(coordinator):
    c = coordinator
    await c.async_set_schedule(schedule(["12:00"]))
    assert [delay for delay, _ in c.active_timers.values()] == [7200]
    assert c.sprinkle_target_amount_today == [5]
    old_tokens = set(c.active_timers)
    await c.async_set_schedule(schedule(["13:00", "14:00"], minutes=10))
    assert not old_tokens.intersection(c.active_timers)
    assert sorted(delay for delay, _ in c.active_timers.values()) == [10800, 14400]
    assert c.sprinkle_target_amount_today == [20]
    assert c.forecasted_sprinkle_today == [20]
    c.run_watering_cycle.assert_not_awaited()
    assert (await c.get_next_watering_date()).hour == 13
    assert (await c.get_next_watering_date()).utcoffset() == timedelta(hours=1)


async def test_clearing_schedule_cancels_pending_runs(coordinator):
    c = coordinator
    await c.async_set_schedule(schedule(["12:00"]))
    await c.async_set_schedule(schedule())
    assert not c.active_timers
    assert c.sprinkle_target_amount_today == [0]
    assert await c.get_next_watering_date() is None


async def test_repeat_checks_do_not_duplicate_and_ignore_elapsed_times(coordinator):
    c = coordinator
    await c.async_set_schedule(schedule(["09:00", "12:00", "12:00:00"]))
    await c.check_and_schedule_watering()
    assert len(c.active_timers) == 1
    assert c.sprinkle_target_amount_today == [10]


async def test_empty_month_does_not_schedule_next_months_times_today(coordinator):
    c = coordinator
    data = schedule()
    data[7]["hours"] = ["12:00"]  # August; now is July.
    await c.async_set_schedule(data)
    assert not c.active_timers
    assert await c.get_next_watering_date() == datetime(2026, 8, 1, 12, tzinfo=ZoneInfo("Europe/Lisbon"))


async def test_failed_storage_keeps_old_schedule_and_timers(coordinator):
    c = coordinator
    await c.async_set_schedule(schedule(["12:00"]))
    previous, timers = deepcopy(c.schedule), dict(c.active_timers)
    c.save_persistent_data.side_effect = OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        await c.async_set_schedule(schedule(["13:00"]))
    assert c.schedule == previous
    assert c.active_timers == timers


@pytest.mark.parametrize("invalid", [None, [], schedule()[:-1], [None] * 12,
    [{"interval_days": -1, "hours": [], "stations": {"station_1_minutes": 0}}] * 12,
    schedule(["25:00"]), schedule([None]), schedule(minutes=-1), schedule(minutes=721),
    schedule(minutes=1.5), schedule(minutes=True),
    [{"interval_days": 0, "hours": [], "stations": {"station_2_minutes": 0}}] * 12,
])
async def test_invalid_schedule_does_not_change_state_or_timers(coordinator, invalid):
    c = coordinator
    await c.async_set_schedule(schedule(["12:00"]))
    previous, timers = deepcopy(c.schedule), dict(c.active_timers)
    c.save_persistent_data.reset_mock()
    with pytest.raises(HomeAssistantError):
        await c.async_set_schedule(invalid)
    assert c.schedule == previous and c.active_timers == timers
    c.save_persistent_data.assert_not_awaited()


async def test_daily_interval_does_not_rearm_after_recent_watering(coordinator):
    c = coordinator
    c.last_sprinkle = dt_util.now()
    data = schedule(["12:00"])
    data[6]["interval_days"] = 2
    await c.async_set_schedule(data)
    assert not c.active_timers
    assert c.sprinkle_target_amount_today == [0]
    assert (await c.get_next_watering_date()).date() == dt_util.now().date() + timedelta(days=2)
