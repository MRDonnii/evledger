"""Constants for smart charging (formerly the EV Smart Charge integration)."""

DOMAIN = "evledger"
CONF_SMART_CHARGE = "smart_charge"
CONF_SMART_ENABLED = "enabled"

CONF_BATTERY_ENTITY = "battery_entity"
CONF_PRICE_ENTITIES = "price_entities"
CONF_CAPACITY = "battery_capacity_kwh"
CONF_CHARGER_TYPE = "charger_type"
CONF_ZAPTEC_MODE_ENTITY = "zaptec_mode_entity"
CONF_CHARGE_SWITCH = "charge_switch"
CONF_CAR_PLUGGED_ENTITY = "car_plugged_entity"
CONF_CAR_DEVICE = "car_device"
CONF_NOTIFY_SERVICES = "notify_services"
CONF_NOTIFY_ONLY_HOME = "notify_only_home"
# Dashboard path opened when the notification itself is tapped, e.g. /dashboard-ev/car.
CONF_NOTIFY_URL = "notify_url"
# Icon on the phone messages (Material Design Icon), and its background colour.
CONF_NOTIFY_ICON = "notify_icon"
DEFAULT_NOTIFY_ICON = "mdi:ev-station"
NOTIFY_COLOR = "#16a34a"
CONF_VEHICLE_MODEL = "vehicle_model"
VEHICLE_AUTO = "auto"

CHARGER_NONE = "none"
CHARGER_ZAPTEC = "zaptec"
CHARGER_SWITCH = "switch"
CHARGER_TYPES = [CHARGER_NONE, CHARGER_ZAPTEC, CHARGER_SWITCH]

DEFAULT_CAPACITY = 57.5
DEFAULT_TARGET_SOC = 80.0
DEFAULT_POWER_KW = 11.0
DEFAULT_EFFICIENCY = 0.9
DEFAULT_PRICE_FACTOR = 1.0
DEFAULT_READY_BY = "07:00"
DEFAULT_FIXED_START = "22:00"
DEFAULT_FIXED_END = "06:00"
DEFAULT_PRICE_CAP = 1.5
DEFAULT_MIN_SOC = 20.0
DEFAULT_CONSUMPTION = 180.0
DEFAULT_TRIP_MARGIN = 15.0
DEFAULT_TRIP_RESERVE = 10.0

# Wait this long after start-up before sending commands, so charger and car states have settled.
STARTUP_GRACE_SECONDS = 60
# The plan must want charging this long before a start is sent (see control.Controller).
START_DELAY_SECONDS = 15

STATUS_PLAN_ONLY = "plan_only"
STATUS_MANUAL = "manual"
STATUS_DISCONNECTED = "disconnected"
STATUS_OTHER_CAR = "other_car"
STATUS_UNKNOWN = "unknown"
STATUS_CHARGING = "charging"
STATUS_STOPPED_EXTERNALLY = "stopped_externally"
STATUS_NOT_RESPONDING = "not_responding"
STATUS_PAUSED = "paused"
STATUS_STARTING = "starting"
STATUS_DONE = "done"
STATUS_WAITING = "waiting"
STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
# Without an answer on the phone, the cheapest plan runs anyway after this long.
CONFIRM_TIMEOUT_MINUTES = 60
STATUSES = [STATUS_PLAN_ONLY, STATUS_MANUAL, STATUS_DISCONNECTED, STATUS_OTHER_CAR, STATUS_UNKNOWN,
            STATUS_CHARGING, STATUS_STOPPED_EXTERNALLY, STATUS_NOT_RESPONDING, STATUS_PAUSED,
            STATUS_STARTING, STATUS_DONE, STATUS_WAITING, STATUS_AWAITING_CONFIRMATION]
