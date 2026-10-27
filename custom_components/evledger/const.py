"""Constants for the EV Ledger integration."""
from __future__ import annotations

DOMAIN = "evledger"
# Button, number and text hold the public charge form, which every car has.
PLATFORMS = ["button", "number", "select", "sensor", "text"]
# Added when smart charging is switched on in the options.
SMART_PLATFORMS = ["binary_sensor", "datetime", "switch", "time"]

STORAGE_VERSION = 1
STORAGE_KEY_TEMPLATE = "evledger_{entry_id}"

DEFAULT_POLL_INTERVAL_SECONDS = 30
# A charger session counter this far below where it stood when a charge began was reset (a new plug-in).
METER_RESET_KWH = 0.05

# Trip detection thresholds
TRIP_MIN_DISTANCE_KM = 0.3
TRIP_END_IDLE_MINUTES = 5

# Charge detection thresholds
CHARGE_START_POWER_W = 500
CHARGE_END_POWER_W = 100
CHARGE_END_IDLE_MINUTES = 3

DEFAULT_CURRENCY = "DKK"

# --- Config keys ---
CONF_VEHICLE_NAME = "vehicle_name"
CONF_VEHICLE_PROVIDER = "vehicle_provider"
CONF_CURRENCY = "currency"

CONF_BATTERY_ENTITY = "battery_entity"
CONF_ODOMETER_ENTITY = "odometer_entity"
CONF_DEVICE_TRACKER_ENTITY = "device_tracker_entity"
CONF_CHARGING_BINARY_ENTITY = "charging_binary_entity"
CONF_LOCKED_ENTITY = "locked_entity"
CONF_OUTSIDE_TEMP_ENTITY = "outside_temp_entity"

CONF_TESLA_MODEL_KEY = "tesla_model_key"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
CONF_RATED_WH_PER_KM = "rated_wh_per_km"
CONF_MODEL_LABEL = "model_label"

# Temperature buckets for efficiency-vs-rating comparison (degrees C, trip start reading)
TEMP_BUCKET_COLD_MAX_C = 5
TEMP_BUCKET_MILD_MAX_C = 15

CONF_CHARGER_PROVIDERS = "charger_providers"

CONF_ZAPTEC_POWER_ENTITY = "zaptec_power_entity"
CONF_ZAPTEC_SESSION_ENERGY_ENTITY = "zaptec_session_energy_entity"
CONF_ZAPTEC_CHARGING_ENTITY = "zaptec_charging_entity"
CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY = "zaptec_completed_energy_entity"

CONF_MONTA_LAST_CHARGE_ENTITY = "monta_last_charge_entity"
CONF_MONTA_WALLET_ENTITY = "monta_wallet_entity"

CONF_SPOT_PRICE_ENTITY = "spot_price_entity"
# Open Charge Map API key (optional): names and operators of public charging stations
CONF_OCM_API_KEY = "ocm_api_key"

# --- Provider registry ids ---
VEHICLE_PROVIDER_TESLA_CUSTOM = "tesla_custom"

CHARGER_PROVIDER_ZAPTEC = "zaptec"
CHARGER_PROVIDER_MONTA = "monta"
CHARGER_PROVIDER_MANUAL = "manual"
CHARGER_PROVIDER_SPOT_PRICE = "spot_price"

CHARGER_PROVIDERS_WITH_ENTITIES = (CHARGER_PROVIDER_ZAPTEC, CHARGER_PROVIDER_MONTA)

# --- Session location kind ---
LOCATION_HOME = "home"
LOCATION_PUBLIC = "public"

# --- Services ---
SERVICE_LOG_PUBLIC_CHARGE = "log_public_charge"
SERVICE_UPDATE_CHARGE = "update_charge"
SERVICE_DELETE_CHARGE = "delete_charge"
SERVICE_DELETE_TRIP = "delete_trip"
SERVICE_ADD_CHARGE = "add_charge"
SERVICE_SET_WEEK_PLAN = "set_week_plan"
SERVICE_SET_READY_BY_ONCE = "set_ready_by_once"
SERVICE_CLEAR_READY_BY_ONCE = "clear_ready_by_once"
SERVICE_SET_ZONE_PRICE = "set_zone_price"
SERVICE_SET_OPERATOR_PRICE = "set_operator_price"
SERVICE_FORGET_PLACE = "forget_place"
SERVICE_SAVE_TYRE_SET = "save_tyre_set"
SERVICE_DELETE_TYRE_SET = "delete_tyre_set"
SERVICE_CHANGE_TYRES = "change_tyres"
SERVICE_UPDATE_TYRE_CHANGE = "update_tyre_change"
SERVICE_DELETE_TYRE_CHANGE = "delete_tyre_change"
SERVICE_LOG_TREAD_DEPTH = "log_tread_depth"
SERVICE_DELETE_TREAD_DEPTH = "delete_tread_depth"
TYRE_SERVICES = (SERVICE_SAVE_TYRE_SET, SERVICE_DELETE_TYRE_SET, SERVICE_CHANGE_TYRES, SERVICE_UPDATE_TYRE_CHANGE,
                 SERVICE_DELETE_TYRE_CHANGE, SERVICE_LOG_TREAD_DEPTH, SERVICE_DELETE_TREAD_DEPTH)
ATTR_ENDED_AT = "ended_at"
ATTR_LOCATION = "location"
ATTR_KWH = "kwh"
ATTR_PRICE = "price"
ATTR_PRICE_PER_KWH = "price_per_kwh"
ATTR_REMEMBER = "remember"
ATTR_LOCATION_NAME = "location_name"
ATTR_STARTED_AT = "started_at"
ATTR_NOTE = "note"
ATTR_CHARGE_ID = "charge_id"
ATTR_ZONE = "zone"
ATTR_OPERATOR = "operator"
ATTR_NAME = "name"
ATTR_TRIP_ID = "trip_id"

SIGNAL_UPDATE = f"{DOMAIN}_update"
