"""Support for Modbus Register sensors."""
import logging
import struct
from typing import Any, Optional, Union

from pymodbus.exceptions import ConnectionException, ModbusException
from pymodbus.pdu import ExceptionResponse
import voluptuous as vol

from homeassistant.components.sensor import DEVICE_CLASSES_SCHEMA, PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_ADDRESS,
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_OFFSET,
    CONF_SLAVE,
    CONF_STRUCTURE,
    CONF_UNIT_OF_MEASUREMENT,
    STATE_ON,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import (
    ConfigType,
    DiscoveryInfoType,
    HomeAssistantType,
)

from .const import (
    CALL_TYPE_REGISTER_HOLDING,
    CALL_TYPE_REGISTER_INPUT,
    CONF_BIT_NUMBER,
    CONF_BIT_SENSORS,
    CONF_COUNT,
    CONF_DATA_TYPE,
    CONF_HUB,
    CONF_INPUT_TYPE,
    CONF_PRECISION,
    CONF_REGISTER,
    CONF_REGISTER_TYPE,
    CONF_REGISTERS,
    CONF_REVERSE_ORDER,
    CONF_SCALE,
    CONF_SENSORS,
    DATA_TYPE_CUSTOM,
    DATA_TYPE_FLOAT,
    DATA_TYPE_INT,
    DATA_TYPE_STRING,
    DATA_TYPE_UINT,
    DEFAULT_HUB,
    DEFAULT_STRUCT_FORMAT,
    MODBUS_DOMAIN,
)
from .modbus import ModbusHub

_LOGGER = logging.getLogger(__name__)


def number(value: Any) -> Union[int, float]:
    """Coerce a value to number without losing precision."""
    if isinstance(value, int):
        return value

    if isinstance(value, str):
        try:
            value = int(value)
            return value
        except (TypeError, ValueError):
            pass

    try:
        value = float(value)
        return value
    except (TypeError, ValueError) as err:
        raise vol.Invalid(f"invalid number {value}") from err


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_REGISTERS): [
            {
                vol.Required(CONF_NAME): cv.string,
                vol.Required(CONF_REGISTER): cv.positive_int,
                vol.Optional(CONF_COUNT, default=1): cv.positive_int,
                vol.Optional(CONF_DATA_TYPE, default=DATA_TYPE_INT): vol.In(
                    [
                        DATA_TYPE_INT,
                        DATA_TYPE_UINT,
                        DATA_TYPE_FLOAT,
                        DATA_TYPE_STRING,
                        DATA_TYPE_CUSTOM,
                    ]
                ),
                vol.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
                vol.Optional(CONF_HUB, default=DEFAULT_HUB): cv.string,
                vol.Optional(CONF_OFFSET, default=0): number,
                vol.Optional(CONF_PRECISION, default=0): cv.positive_int,
                vol.Optional(
                    CONF_REGISTER_TYPE, default=CALL_TYPE_REGISTER_HOLDING
                ): vol.In([CALL_TYPE_REGISTER_HOLDING, CALL_TYPE_REGISTER_INPUT]),
                vol.Optional(CONF_REVERSE_ORDER, default=False): cv.boolean,
                vol.Optional(CONF_SCALE, default=1): number,
                vol.Optional(CONF_SLAVE): cv.positive_int,
                vol.Optional(CONF_STRUCTURE): cv.string,
                vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
            }
        ]
    }
)


async def async_setup_platform(
    hass: HomeAssistantType,
    config: ConfigType,
    async_add_entities,
    discovery_info: Optional[DiscoveryInfoType] = None,
):
    """Set up the Modbus sensors."""
    sensors = []

    #  check for old config:
    if discovery_info is None:
        _LOGGER.warning(
            "Sensor configuration depreciated, will be removed in a future release"
        )
        discovery_info = {
            CONF_NAME: "noName",
            CONF_SENSORS: config[CONF_REGISTERS],
        }
        for entry in discovery_info[CONF_SENSORS]:
            entry[CONF_ADDRESS] = entry[CONF_REGISTER]
            entry[CONF_INPUT_TYPE] = entry[CONF_REGISTER_TYPE]
            del entry[CONF_REGISTER]
            del entry[CONF_REGISTER_TYPE]
        config = None

    for entry in discovery_info.get(CONF_BIT_SENSORS, []):
        words_count = int(entry[CONF_COUNT])
        bit_number = int(entry[CONF_BIT_NUMBER])

        if bit_number >= words_count * 16:
            _LOGGER.error(
                "Bit number for the %s sensor is out of range",
                entry[CONF_NAME],
            )
            continue

        hub: ModbusHub = hass.data[MODBUS_DOMAIN][discovery_info[CONF_NAME]]
        sensors.append(
            ModbusBitSensor(
                hub,
                entry[CONF_NAME],
                entry.get(CONF_SLAVE),
                entry[CONF_ADDRESS],
                bit_number,
                entry.get(CONF_UNIT_OF_MEASUREMENT),
                words_count,
                entry.get(CONF_DEVICE_CLASS),
                entry[CONF_INPUT_TYPE],
            )
        )

    for entry in discovery_info.get(CONF_SENSORS, []):
        if entry[CONF_DATA_TYPE] == DATA_TYPE_STRING:
            structure = str(entry[CONF_COUNT] * 2) + "s"
        elif entry[CONF_DATA_TYPE] != DATA_TYPE_CUSTOM:
            try:
                structure = f">{DEFAULT_STRUCT_FORMAT[entry[CONF_DATA_TYPE]][entry[CONF_COUNT]]}"
            except KeyError:
                _LOGGER.error(
                    "Unable to detect data type for %s sensor, try a custom type",
                    entry[CONF_NAME],
                )
                continue
        else:
            structure = entry.get(CONF_STRUCTURE)

        try:
            size = struct.calcsize(structure)
        except struct.error as err:
            _LOGGER.error("Error in sensor %s structure: %s", entry[CONF_NAME], err)
            continue

        if entry[CONF_COUNT] * 2 != size:
            _LOGGER.error(
                "Structure size (%d bytes) mismatch registers count (%d words)",
                size,
                entry[CONF_COUNT],
            )
            continue

        if CONF_HUB in entry:
            # from old config!
            discovery_info[CONF_NAME] = entry[CONF_HUB]
        hub: ModbusHub = hass.data[MODBUS_DOMAIN][discovery_info[CONF_NAME]]
        sensors.append(
            ModbusRegisterSensor(
                hub,
                entry[CONF_NAME],
                entry.get(CONF_SLAVE),
                entry[CONF_ADDRESS],
                entry[CONF_INPUT_TYPE],
                entry.get(CONF_UNIT_OF_MEASUREMENT),
                entry[CONF_COUNT],
                entry[CONF_REVERSE_ORDER],
                entry[CONF_SCALE],
                entry[CONF_OFFSET],
                structure,
                entry[CONF_PRECISION],
                entry[CONF_DATA_TYPE],
                entry.get(CONF_DEVICE_CLASS),
            )
        )

    if not sensors:
        return False
    async_add_entities(sensors)


class ModbusSensorBase(RestoreEntity):
    """Base class for the Modbus sensor."""

    def __init__(
        self,
        hub,
        name,
        slave,
        register,
        unit_of_measurement,
        count,
        device_class,
        register_type,
    ):
        """Initialize the modbus sensor."""
        self._hub = hub
        self._name = name
        self._slave = int(slave) if slave else None
        self._register = int(register)
        self._unit_of_measurement = unit_of_measurement
        self._count = count
        self._device_class = device_class
        self._register_type = register_type
        self._value = None
        self._available = True

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._value

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def device_class(self) -> Optional[str]:
        """Return the device class of the sensor."""
        return self._device_class

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available


class ModbusRegisterSensor(ModbusSensorBase):
    """Modbus register sensor."""

    def __init__(
        self,
        hub,
        name,
        slave,
        register,
        register_type,
        unit_of_measurement,
        count,
        reverse_order,
        scale,
        offset,
        structure,
        precision,
        data_type,
        device_class,
    ):
        """Initialize the modbus register sensor."""
        super().__init__(
            hub,
            name,
            slave,
            register,
            unit_of_measurement,
            count,
            device_class,
            register_type,
        )
        self._reverse_order = reverse_order
        self._scale = scale
        self._offset = offset
        self._precision = precision
        self._structure = structure
        self._data_type = data_type
        self._value = None
        self._available = True

    async def async_added_to_hass(self):
        """Handle entity which will be added."""
        state = await self.async_get_last_state()
        if not state:
            return
        self._value = state.state

    def update(self):
        """Update the state of the sensor."""
        try:
            if self._register_type == CALL_TYPE_REGISTER_INPUT:
                result = self._hub.read_input_registers(
                    self._slave, self._register, self._count
                )
            else:
                result = self._hub.read_holding_registers(
                    self._slave, self._register, self._count
                )
        except ConnectionException:
            self._available = False
            return

        if isinstance(result, (ModbusException, ExceptionResponse)):
            self._available = False
            return

        registers = result.registers
        if self._reverse_order:
            registers.reverse()

        byte_string = b"".join([x.to_bytes(2, byteorder="big") for x in registers])
        if self._data_type == DATA_TYPE_STRING:
            self._value = byte_string.decode()
        else:
            val = struct.unpack(self._structure, byte_string)

            # Issue: https://github.com/home-assistant/core/issues/41944
            # If unpack() returns a tuple greater than 1, don't try to process the value.
            # Instead, return the values of unpack(...) separated by commas.
            if len(val) > 1:
                self._value = ",".join(map(str, val))
            else:
                val = val[0]

                # Apply scale and precision to floats and ints
                if isinstance(val, (float, int)):
                    val = self._scale * val + self._offset

                    # We could convert int to float, and the code would still work; however
                    # we lose some precision, and unit tests will fail. Therefore, we do
                    # the conversion only when it's absolutely necessary.
                    if isinstance(val, int) and self._precision == 0:
                        self._value = str(val)
                    else:
                        self._value = f"{float(val):.{self._precision}f}"
                else:
                    # Don't process remaining datatypes (bytes and booleans)
                    self._value = str(val)

        self._available = True


class ModbusBitSensor(ModbusSensorBase):
    """Modbus bit sensor."""

    def __init__(
        self,
        hub,
        name,
        slave,
        register,
        bit_number,
        unit_of_measurement,
        count,
        device_class,
        register_type,
    ):
        """Initialize the modbus bit sensor."""
        super().__init__(
            hub,
            name,
            slave,
            register,
            unit_of_measurement,
            count,
            device_class,
            register_type,
        )
        self._bit_number = int(bit_number)

    async def async_added_to_hass(self):
        """Handle entity which will be added."""
        state = await self.async_get_last_state()
        if not state:
            return
        self._value = state.state == STATE_ON

    def update(self):
        """Update the state of the sensor."""
        try:
            if self._register_type == CALL_TYPE_REGISTER_INPUT:
                result = self._hub.read_input_registers(
                    self._slave, self._register, self._count
                )
            else:
                result = self._hub.read_holding_registers(
                    self._slave, self._register, self._count
                )

        except ConnectionException:
            self._available = False
            return

        if isinstance(result, (ModbusException, ExceptionResponse)):
            self._available = False
            return

        register_index = self._bit_number // 16
        register_bit_mask = 1 << (self._bit_number % 16)
        self._value = bool(result.registers[register_index] & register_bit_mask)
        self._available = True
