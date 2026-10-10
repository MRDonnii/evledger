"""Constants for smart charging (formerly the EV Smart Charge integration)."""

from datetime import time

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
# A charger that reboots or loses its connection reports "disconnected" for a short while; only a
# disconnect longer than this counts as the car being unplugged.
UNPLUG_GRACE_SECONDS = 120
# How long to wait for the car's battery level and the prices after a start before planning without
# them (with the last known battery level, or an assumed low one, and estimated prices).
INPUT_WAIT_SECONDS = 600
ASSUMED_SOC = 20.0
# The plan must want charging this long before a start is sent (see control.Controller).
START_DELAY_SECONDS = 15

# Ready by on Saturdays and Sundays, when "another time at the weekend" is on.
DEFAULT_READY_BY_WEEKEND = "09:00"
# The evening check: a reminder when the car is home without the cable and its battery is below the level, and
# a warning when the charger is offline while the car is plugged in.
DEFAULT_REMINDER_TIME = "21:00"
DEFAULT_REMINDER_SOC = 50.0
# The evening check is only made this long after its time (not when Home Assistant starts at night).
REMINDER_WINDOW_HOURS = 3
# A charger offline this long while the plan wants to charge is reported at once.
CHARGER_OFFLINE_ALERT_MINUTES = 10
# A charge saved before a restart counts in the done message only if its last period ended this recently.
CHARGE_RUN_KEEP_HOURS = 24
# Preconditioning: the car's climate is turned on this long before the ready-by time (or a trip's departure),
# and turned off again this long after it if the car is still plugged in at home.
DEFAULT_PRECONDITION_MINUTES = 20.0
PRECONDITION_OFF_AFTER_MINUTES = 30
# Trips from a calendar: events in the next hours whose title has the keyword (or, without one, that have a
# location) become the temporary plan, leaving early enough to arrive at the event's start.
CONF_TRIP_CALENDAR = "trip_calendar"
CONF_TRIP_KEYWORD = "trip_calendar_keyword"
CALENDAR_LOOKAHEAD_HOURS = 36
CALENDAR_REFRESH_MINUTES = 15
# Leave this long before the event when the drive time is not known yet, and this much earlier than the drive time.
CALENDAR_LEAD_MINUTES = 45
CALENDAR_MARGIN_MINUTES = 10
# The car's location (from EV Ledger's setup), for the reminder and preconditioning.
CONF_CAR_TRACKER = "car_tracker"
# The car's climate entity, when it is not on the battery sensor's device (another car integration sends the
# commands, e.g. Teslemetry or Tesla Fleet next to Tesla Custom).
CONF_CAR_CLIMATE = "car_climate_entity"

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

# A second "charging started" message only after this long (the car pausing or a charger reboot is no new start).
STARTED_NOTE_QUIET_MINUTES = 30
# The saving against "Charge now" is shown from this amount (also when the plan cost more).
SAVING_SHOWN = 0.5
SAVED_MONTHS_KEPT = 3
# Learning the charging power and efficiency from the ledger's charges.
LEARN_MIN_HOURS = 0.5
LEARN_TOP_SOC = 90.0  # above this cars charge slower: those charges do not count for the power
LEARN_MIN_SOC_GAIN = 10.0
LEARN_MIN_SAMPLES = 2
LEARN_WEIGHT = 0.3
LEARN_POWER_RANGE = (1.0, 50.0)
LEARN_EFFICIENCY_RANGE = (0.7, 1.0)
# The monthly summary: on the first days of the month from this time (once).
MONTHLY_AT = time(9, 0)
MONTHLY_DAYS = 3
MONTH_NAMES = ("januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober",
               "november", "december")
