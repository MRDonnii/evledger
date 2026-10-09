"""Decides when to send start/stop to the charger. No Home Assistant imports, so it can be unit tested."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class ChargerState(StrEnum):
    UNKNOWN = "unknown"
    DISCONNECTED = "disconnected"
    READY = "ready"  # car connected, waiting to be started or authorised
    CHARGING = "charging"
    PAUSED = "paused"  # car connected, charging stopped or finished


class Action(StrEnum):
    NONE = "none"
    START = "start"
    STOP = "stop"


class Event(StrEnum):
    PLUGGED = "plugged"
    UNPLUGGED = "unplugged"
    MANUAL_START = "manual_start"
    EXTERNAL_STOP = "external_stop"


CONNECTED = (ChargerState.READY, ChargerState.CHARGING, ChargerState.PAUSED)


@dataclass
class Controller:
    """Sends a command only when the charger is not already where the plan wants it and waits for the
    charger to react before trying again. It never gives up for good: after a few quick attempts a
    wanted start is retried every 15 minutes, and a charge stopped from outside is restarted once
    after 10 minutes (a second outside stop is respected). A start or stop that did not come from
    here is reported, so the plan can follow the user instead of fighting them."""

    retry_after: timedelta = timedelta(minutes=3)
    # A stop that did not take (e.g. sent seconds after a start) is repeated sooner than a start.
    stop_retry_after: timedelta = timedelta(seconds=45)
    # The plan must want charging this long before a start is sent, so a setting that is only
    # passed through for a moment (a time being typed) never starts the charger.
    start_delay: timedelta = timedelta(seconds=15)
    # A car that takes long to wake up can start drawing power minutes after the start command.
    own_command_window: timedelta = timedelta(minutes=15)
    max_start_attempts: int = 3
    slow_retry_after: timedelta = timedelta(minutes=15)
    external_retry_after: timedelta = timedelta(minutes=10)

    state: ChargerState | None = None
    last_command: Action = Action.NONE
    last_command_at: datetime | None = None
    start_attempts: int = 0
    blocked: bool = False  # the car, the app or a fault stopped the charging
    blocked_at: datetime | None = None
    external_stops: int = 0
    last_desired: bool | None = None
    desired_since: datetime | None = None

    def _own(self, action: Action, now: datetime) -> bool:
        return (self.last_command == action and self.last_command_at is not None
                and now - self.last_command_at <= self.own_command_window)

    def reset(self) -> None:
        self.start_attempts = 0
        self.blocked = False
        self.blocked_at = None
        self.external_stops = 0

    def observe(self, state: ChargerState, now: datetime) -> Event | None:
        previous, self.state = self.state, state
        if previous is None or previous == state or state == ChargerState.UNKNOWN:
            return None
        if state == ChargerState.DISCONNECTED:
            self.reset()
            return Event.UNPLUGGED if previous != ChargerState.UNKNOWN else None
        if previous in (ChargerState.DISCONNECTED, ChargerState.UNKNOWN):
            self.reset()
            if previous == ChargerState.DISCONNECTED:
                return Event.PLUGGED
            return None
        if state == ChargerState.CHARGING:
            self.start_attempts = 0
            self.blocked = False
            return None if self._own(Action.START, now) else Event.MANUAL_START
        if previous == ChargerState.CHARGING and not self._own(Action.STOP, now):
            self.blocked = True
            self.blocked_at = now
            self.external_stops += 1
            return Event.EXTERNAL_STOP
        return None

    @property
    def gave_up(self) -> bool:
        """The quick attempts are used up; starts are now retried slowly."""
        return self.start_attempts >= self.max_start_attempts

    @property
    def respects_stop(self) -> bool:
        """Stopped from outside twice: leave it stopped until the plan changes or the car is replugged."""
        return self.blocked and self.external_stops >= 2

    def decide(self, desired: bool, now: datetime) -> Action:
        if desired != self.last_desired:
            self.last_desired = desired
            self.desired_since = now if desired else None
            self.reset()
        state = self.state
        if state not in CONNECTED:
            return Action.NONE
        since = None if self.last_command_at is None else now - self.last_command_at
        if desired and state != ChargerState.CHARGING:
            if self.blocked and (self.respects_stop or self.blocked_at is None
                                 or now - self.blocked_at < self.external_retry_after):
                return Action.NONE
            wait = self.slow_retry_after if self.gave_up else self.retry_after
            if since is not None and since < wait and self.last_command == Action.START:
                return Action.NONE
            if self.start_wait(now):
                return Action.NONE
            self.start_attempts += 1
            return self._command(Action.START, now)
        if not desired and state == ChargerState.CHARGING:
            if since is not None and since < self.stop_retry_after and self.last_command == Action.STOP:
                return Action.NONE
            return self._command(Action.STOP, now)
        return Action.NONE

    def start_wait(self, now: datetime) -> timedelta | None:
        """How long a wanted start still has to wait, or None when it may go now."""
        if self.desired_since is None:
            return None
        left = self.desired_since + self.start_delay - now
        return left if left > timedelta(0) else None

    def _command(self, action: Action, now: datetime) -> Action:
        self.last_command, self.last_command_at = action, now
        return action
