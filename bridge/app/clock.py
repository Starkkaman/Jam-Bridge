"""Time source. Production uses the wall clock; tests use a fast clock (scale < 1) so a 130 s chat timeout takes about
a second, and `advance()` jumps logical time (retention, the 10 min queue drop)."""

import asyncio
import time


class Clock:
    def __init__(self, scale: float = 1.0):
        self.scale = float(scale) if scale and scale > 0 else 1.0
        self._t0_wall = time.time()
        self._t0_mono = time.monotonic()
        self._offset = 0.0  # logical seconds added by advance()

    def time(self) -> float:
        """Logical epoch seconds."""
        if self.scale == 1.0:
            return time.time() + self._offset
        return self._t0_wall + (time.monotonic() - self._t0_mono) / self.scale + self._offset

    def now_ms(self) -> int:
        return int(self.time() * 1000)

    def secs(self, logical_seconds: float) -> float:
        """Real seconds for a logical duration (timeouts, sleeps)."""
        return logical_seconds * self.scale

    async def sleep(self, logical_seconds: float) -> None:
        await asyncio.sleep(max(0.0, logical_seconds * self.scale))

    def advance(self, logical_seconds: float) -> None:
        self._offset += logical_seconds
