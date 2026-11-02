from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import cards, quotas
from .db import parse_iso, utcnow
from .presenton import PresentonError
from .security import decrypt_secret


LOGGER = logging.getLogger("ppt_gate.settlement")


class SettlementWorker:
    """Settles metered async generation tasks after Presenton finishes.

    The browser receives a task id immediately. This worker polls the official
    async-task status route with the shadow user's session and either settles
    (completed) or releases (failed) the reservation. A timeout is marked
    ``unknown`` and left for manual reconciliation instead of being released
    automatically.
    """

    def __init__(self, app) -> None:
        self.app = app
        self.settings = app.state.settings
        self.presenton = app.state.presenton
        self._stop = asyncio.Event()
        self._cookie_cache: dict[str, tuple[str, float]] = {}

    async def run(self) -> None:
        interval = max(2.0, float(self.settings.settlement_interval_seconds))
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:  # pragma: no cover - defensive isolation
                LOGGER.exception("settlement tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    async def stop(self) -> None:
        self._stop.set()

    async def _cookie_for(self, card: dict[str, Any]) -> str:
        cached = self._cookie_cache.get(card["id"])
        if cached is not None and cached[1] > time.time():
            return cached[0]
        credential = decrypt_secret(
            card["credential_enc"], self.settings.gate_secret
        )
        cookie = await self.presenton.login_user(
            card["presenton_username"], credential
        )
        self._cookie_cache[card["id"]] = (cookie, time.time() + 600)
        return cookie

    async def tick(self, *, force: bool = False) -> None:
        rows = quotas.pending_reservations(self.settings.database_path, limit=20)
        now = utcnow()
        for row in rows:
            last_checked = parse_iso(row.get("last_checked_at"))
            if (
                not force
                and last_checked is not None
                and (now - last_checked).total_seconds() < 5
            ):
                continue
            card = cards.get_card(self.settings.database_path, str(row["card_id"]))
            if card is None:
                quotas.release(self.settings.database_path, row["id"], "card missing")
                continue
            try:
                cookie = await self._cookie_for(card)
            except (PresentonError, ValueError) as exc:
                LOGGER.warning("settlement login failed card=%s error=%s", card["id"], exc)
                quotas.touch(self.settings.database_path, row["id"])
                continue

            code, task = await self.presenton.async_task_status(
                cookie, str(row["reference"])
            )
            if code == 401:
                self._cookie_cache.pop(card["id"], None)
                quotas.touch(self.settings.database_path, row["id"])
                continue
            if code != 200 or task is None:
                quotas.touch(self.settings.database_path, row["id"])
                continue

            status = str(task.get("status") or "").lower()
            if status in {"completed", "success", "succeeded"}:
                quotas.settle(
                    self.settings.database_path,
                    row["id"],
                    cost_cents=int(row.get("estimated_cost_cents") or 0),
                    reference=str(row["reference"]),
                )
            elif status in {"error", "failed", "cancelled", "canceled"}:
                quotas.release(
                    self.settings.database_path,
                    row["id"],
                    f"presenton task {status}",
                )
            else:
                expires_at = parse_iso(row.get("expires_at"))
                if expires_at is not None and now > expires_at:
                    quotas.mark_unknown(
                        self.settings.database_path,
                        row["id"],
                        "task status timeout",
                    )
                else:
                    quotas.touch(self.settings.database_path, row["id"])
