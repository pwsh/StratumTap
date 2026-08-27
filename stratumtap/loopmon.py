"""Event-loop lag monitor.

A 1 s ticker that records how late each wakeup actually was. If the loop is being
starved — by a blocking call, a runaway fan-out or simply too much work for the Pi
— the overshoot grows, and ``/api/v1/health`` reports it as ``loop_lag_ms``.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque

log = logging.getLogger("stratumtap.loopmon")

TICK_S = 1.0
SAMPLES = 60


class LoopMonitor:
    """Records the scheduling overshoot of a fixed-interval sleep."""

    def __init__(self, interval_s: float = TICK_S, samples: int = SAMPLES) -> None:
        self.interval_s = max(0.01, float(interval_s))
        self.overshoots: deque[float] = deque(maxlen=max(1, int(samples)))
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the ticker task."""
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        """Cancel the ticker task."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover - defensive
            log.debug("loop monitor raised on shutdown", exc_info=True)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            await asyncio.sleep(self.interval_s)
            self.record(loop.time() - started - self.interval_s)

    def record(self, overshoot: float) -> None:
        """Append one measured overshoot in seconds (negative values clamp to 0)."""
        self.overshoots.append(max(0.0, float(overshoot)))

    def max_lag_ms(self) -> float:
        """Largest overshoot over the retained window, in milliseconds."""
        if not self.overshoots:
            return 0.0
        return round(max(self.overshoots) * 1000.0, 3)
