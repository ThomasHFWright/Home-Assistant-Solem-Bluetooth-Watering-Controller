"""Button setup for our Integration.

Here we use a different method to define some of our entity classes.
As, in our example, so much is common, we use our base entity class to define
many properties, then our base sensor class to define the property to get the
value of the sensor.

As such, for all our other sensor types, we can just set the _attr_ value to
keep our code small and easily readable.  You can do this for all entity properties(attributes)
if you so wish, or mix and match to suit.
"""

from dataclasses import dataclass
import logging
import asyncio
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from . import MyConfigEntry
from .base import SolemBaseEntity
from .coordinator import SolemCoordinator
from .discovery import async_discover_station_details

_LOGGER = logging.getLogger(__name__)


@dataclass
class ButtonTypeClass:
    """Class for holding sensor type to sensor class."""

    device_type: str
    button_class: object


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Buttons."""
    # This gets the data update coordinator from the config entry runtime data as specified in your __init__.py
    coordinator: SolemCoordinator = config_entry.runtime_data.coordinator

    # ----------------------------------------------------------------------------
    # Here we enumerate the sensors in your data value from your
    # DataUpdateCoordinator and add an instance of your sensor class to a list
    # for each one.
    # This maybe different in your specific case, depending on how your data is
    # structured
    # ----------------------------------------------------------------------------

    button_types = [
        ButtonTypeClass("SPRINKLE_BUTTON", IrrigationStartButton),
        ButtonTypeClass("STOP_BUTTON", IrrigationStopButton),
        ButtonTypeClass("ON_BUTTON", ControllerOnButton),
        ButtonTypeClass("OFF_BUTTON", ControllerOffButton),
    ]

    buttons = []

    for button_type in button_types:
        buttons.extend(
            [
                button_type.button_class(coordinator, device)
                for device in coordinator.data
                if device.get("device_type") == button_type.device_type
            ]
        )

    # Now create the buttons.
    async_add_entities([*buttons, RefreshStationDetailsButton(coordinator)])


class SolemButtonEntity(SolemBaseEntity, ButtonEntity):
    def __init__(
        self, coordinator: SolemCoordinator, device: dict[str, Any]
    ) -> None:
        """Initialise entity."""
        super().__init__(coordinator, device, None)

    @property
    def entity_category(self):
        return EntityCategory.CONFIG

class IrrigationStartButton(SolemButtonEntity):
    """Button entity to manually start irrigation."""

    def __init__(self, coordinator: SolemCoordinator, device: dict[str, Any]):
        super().__init__(coordinator, device)
        self._attr_name = "Start Irrigation"

    async def async_press(self) -> None:
        """Handle the button press."""
        asyncio.create_task(self.coordinator.start_irrigation(int(self.device_id.rsplit("_", 1)[-1])))

class IrrigationStopButton(SolemButtonEntity):
    """Button entity to manually stop irrigation."""

    def __init__(self, coordinator: SolemCoordinator, device: dict[str, Any]):
        super().__init__(coordinator, device)
        self._attr_name = "Stop Irrigation"

    async def async_press(self) -> None:
        """Handle the button press."""
        # Keep the service call open so HA can display a failed stop to the user.
        await self.coordinator.stop_irrigation()

class ControllerOnButton(SolemButtonEntity):
    """Button entity to manually stop irrigation."""

    def __init__(self, coordinator: SolemCoordinator, device: dict[str, Any]):
        super().__init__(coordinator, device)
        self._attr_name = "Controller ON"

    async def async_press(self) -> None:
        """Handle the button press."""
        asyncio.create_task(self.coordinator.turn_controller_on())

class ControllerOffButton(SolemButtonEntity):
    """Button entity to manually stop irrigation."""

    def __init__(self, coordinator: SolemCoordinator, device: dict[str, Any]):
        super().__init__(coordinator, device)
        self._attr_name = "Controller OFF"

    async def async_press(self) -> None:
        """Handle the button press."""
        asyncio.create_task(self.coordinator.turn_controller_off())


class RefreshStationDetailsButton(ButtonEntity):
    """Reload controller names and count without changing watering state."""

    _attr_has_entity_name = True
    _attr_name = "Refresh station details"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SolemCoordinator):
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.controller_mac_address}_refresh_station_details"
        self._attr_device_info = {
            "identifiers": {("solem_bluetooth_watering_controller", coordinator.controller_mac_address)}
        }

    async def async_press(self) -> None:
        entry = self.coordinator.config_entry
        details = await async_discover_station_details(
            self.hass, dict(entry.data), self.coordinator.bluetooth_timeout
        )
        # The config-entry listener reloads entities after an actual change.
        self.hass.config_entries.async_update_entry(entry, data=details)
