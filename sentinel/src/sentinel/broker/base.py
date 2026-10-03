"""Broker port. The broker (or exchange) is canonical for account state."""

from __future__ import annotations

from typing import Protocol

from sentinel.contracts import AccountSnapshot, Direction, InstrumentSpec, Quote


class BrokerError(RuntimeError):
    """Definite failure: the broker says the request did not happen."""


class BrokerUncertain(RuntimeError):
    """Outcome unknown (timeout, disconnect mid-request). Never retry blindly."""


class Broker(Protocol):
    def server_time(self) -> int: ...
    def spec(self, instrument: str) -> InstrumentSpec: ...
    def quote(self, instrument: str) -> Quote: ...
    def account(self) -> AccountSnapshot: ...

    def send_order(
        self,
        client_id: str,
        instrument: str,
        direction: Direction,
        volume: float,
        stop_loss: float,
        take_profit: float | None,
        max_slippage_points: int,
        fencing_token: int,
    ) -> dict: ...

    def close_position(self, ticket: str, client_id: str, fencing_token: int) -> dict: ...
    def find_by_client_id(self, client_id: str, since_ts: int | None = None) -> list[dict]: ...
    def deals_since(self, ts: int) -> list[dict]: ...
