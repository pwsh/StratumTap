"""Server-Sent Events fan-out.

The whole point of this module is rule 1 of the streaming contract: **a producer
never awaits a consumer**. :meth:`Broadcaster.publish` is a plain synchronous
method that does one ``put_nowait`` per subscriber into a bounded queue; a client
that cannot keep up loses its *oldest* queued events (and gets told how many via
its ``dropped`` counter), it never slows the gpsd reader or the chrony poller down.

The payload is serialized once per publish, not once per subscriber.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import time
from collections.abc import Iterable

log = logging.getLogger("stratumtap.stream")

#: Event names a client may subscribe to.
EVENTS = ("nmea", "gpsd", "ntp", "status")

#: Emit a ``stats`` event this often (module constants so tests can shrink them).
STATS_INTERVAL_S = 10.0
#: Emit a ``: keepalive`` comment after this much silence.
KEEPALIVE_INTERVAL_S = 15.0

_FOREVER = float("inf")


class TooManyClients(RuntimeError):
    """Raised by :meth:`Broadcaster.subscribe` when the client cap is reached."""


def dumps(payload) -> str:
    """Compact JSON for the wire (never raises on odd values)."""
    return json.dumps(payload, separators=(",", ":"), default=str)


def sse_frame(event_id: int, event: str, data: str) -> str:
    """Format one SSE event. Multi-line *data* becomes multiple ``data:`` lines."""
    body = "\n".join(f"data: {part}" for part in data.split("\n"))
    return f"id: {event_id}\nevent: {event}\n{body}\n\n"


class Subscriber:
    """One connected stream client: its interests, its queue and its counters."""

    __slots__ = ("id", "events", "queue", "sent", "dropped", "created_at")

    def __init__(self, sub_id: int, events: Iterable[str], queue_size: int) -> None:
        self.id = sub_id
        self.events: frozenset[str] = frozenset(events)
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, int(queue_size)))
        #: Events accepted for this subscriber (a dropped event counted here too).
        self.sent = 0
        #: Events discarded because the queue was full when a newer one arrived.
        self.dropped = 0
        self.created_at = time.time()

    @property
    def queue_len(self) -> int:
        """Events currently waiting to be written to this client."""
        return self.queue.qsize()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Subscriber {self.id} events={sorted(self.events)} "
            f"sent={self.sent} dropped={self.dropped} queued={self.queue_len}>"
        )


class Broadcaster:
    """Fan-out of producer events to a bounded set of SSE subscribers."""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._subs: dict[int, Subscriber] = {}
        self._next_id = 0

    # -- subscription ------------------------------------------------------

    @property
    def max_clients(self) -> int:
        """Concurrent subscriber cap (``STRATUMTAP_STREAM_MAX_CLIENTS``)."""
        return max(1, int(getattr(self._settings, "stream_max_clients", 16)))

    @property
    def queue_size(self) -> int:
        """Per-subscriber queue size (``STRATUMTAP_STREAM_QUEUE``)."""
        return max(1, int(getattr(self._settings, "stream_queue", 500)))

    @property
    def client_count(self) -> int:
        """Number of currently subscribed clients."""
        return len(self._subs)

    @property
    def subscribers(self) -> list[Subscriber]:
        """A snapshot list of the current subscribers."""
        return list(self._subs.values())

    def subscribe(self, events: set[str]) -> Subscriber:
        """Register a new subscriber; raises :class:`TooManyClients` past the cap."""
        if len(self._subs) >= self.max_clients:
            raise TooManyClients(f"too many stream clients ({self.max_clients})")
        self._next_id += 1
        sub = Subscriber(self._next_id, events, self.queue_size)
        self._subs[sub.id] = sub
        log.debug("stream client %d subscribed to %s", sub.id, sorted(sub.events))
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        """Remove *sub*; safe to call more than once."""
        if self._subs.pop(sub.id, None) is not None:
            log.debug("stream client %d left (sent=%d dropped=%d)", sub.id, sub.sent, sub.dropped)

    # -- publishing --------------------------------------------------------

    def publish(self, event: str, payload) -> int:
        """Queue *payload* for every subscriber of *event*. Never blocks, never awaits.

        Returns the number of subscribers the event was queued for. On a full
        queue the oldest event is dropped so the client keeps receiving *fresh*
        data (stale raw NMEA is worthless) and ``dropped`` is incremented.
        """
        if not self._subs:
            return 0
        item: tuple[str, str] | None = None
        count = 0
        for sub in self._subs.values():
            if event not in sub.events:
                continue
            if item is None:
                data = payload if isinstance(payload, str) else dumps(payload)
                item = (event, data)
            try:
                sub.queue.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    sub.queue.get_nowait()
                    sub.dropped += 1
                except asyncio.QueueEmpty:  # pragma: no cover - drained concurrently
                    pass
                try:
                    sub.queue.put_nowait(item)
                except asyncio.QueueFull:  # pragma: no cover - defensive
                    sub.dropped += 1
                    continue
            sub.sent += 1
            count += 1
        return count

    # -- SSE ---------------------------------------------------------------

    async def sse_generator(
        self,
        sub: Subscriber,
        request=None,
        status_provider=None,
        status_interval: float = 2.0,
        server_info_provider=None,
    ):
        """Yield the SSE byte stream for *sub* until the client goes away.

        Emits ``hello`` first, then queued events, a ``stats`` event every
        :data:`STATS_INTERVAL_S` and a ``: keepalive`` comment after
        :data:`KEEPALIVE_INTERVAL_S` of silence. The subscriber is always removed
        in the ``finally``; the generator never lets an exception escape into the
        ASGI server on a client disconnect.
        """
        loop = asyncio.get_running_loop()
        interval = max(0.1, float(status_interval))
        want_status = "status" in sub.events and status_provider is not None
        event_id = 0
        try:
            hello = {
                "client_id": sub.id,
                "events": [e for e in EVENTS if e in sub.events],
                "queue": sub.queue.maxsize,
                "server": await _call(server_info_provider),
            }
            event_id += 1
            yield sse_frame(event_id, "hello", dumps(hello))

            now = loop.time()
            last_out = now
            next_stats = now + STATS_INTERVAL_S
            next_status = now + interval if want_status else _FOREVER

            while True:
                now = loop.time()
                deadline = min(next_stats, next_status, last_out + KEEPALIVE_INTERVAL_S)
                item = None
                try:
                    item = await asyncio.wait_for(sub.queue.get(), timeout=max(0.0, deadline - now))
                except (TimeoutError, asyncio.QueueEmpty):
                    item = None

                if item is not None:
                    event, data = item
                    event_id += 1
                    yield sse_frame(event_id, event, data)
                    last_out = loop.time()
                    continue

                now = loop.time()
                if now >= next_status:
                    payload = await _call(status_provider)
                    if payload is not None:
                        event_id += 1
                        yield sse_frame(event_id, "status", dumps(payload))
                        last_out = loop.time()
                    next_status = loop.time() + interval
                if now >= next_stats:
                    event_id += 1
                    yield sse_frame(event_id, "stats", dumps(self.stats(sub)))
                    last_out = loop.time()
                    next_stats = last_out + STATS_INTERVAL_S
                if loop.time() >= last_out + KEEPALIVE_INTERVAL_S:
                    if request is not None and await _disconnected(request):
                        return
                    yield ": keepalive\n\n"
                    last_out = loop.time()
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError) as exc:  # pragma: no cover - transport teardown
            log.debug("stream client %d dropped: %s", sub.id, exc)
        finally:
            self.unsubscribe(sub)

    async def raw_generator(self, sub: Subscriber, request=None, sep: str = "\n"):
        """Yield only the raw ``line`` of each queued ``nmea`` event (for nmea.txt)."""
        try:
            while True:
                try:
                    item = await asyncio.wait_for(sub.queue.get(), timeout=KEEPALIVE_INTERVAL_S)
                except TimeoutError:
                    if request is not None and await _disconnected(request):
                        return
                    continue
                event, data = item
                if event != "nmea":
                    continue
                line = _raw_line(data)
                if line:
                    yield line + sep
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError) as exc:  # pragma: no cover - transport teardown
            log.debug("raw stream client %d dropped: %s", sub.id, exc)
        finally:
            self.unsubscribe(sub)

    def stats(self, sub: Subscriber) -> dict:
        """The payload of the periodic ``stats`` event for *sub*."""
        return {
            "t": time.time(),
            "sent": sub.sent,
            "dropped": sub.dropped,
            "queue_len": sub.queue_len,
            "clients": self.client_count,
        }


def _raw_line(data: str) -> str | None:
    if data.startswith("{"):
        try:
            decoded = json.loads(data)
        except ValueError:  # pragma: no cover - defensive
            return None
        line = decoded.get("line") if isinstance(decoded, dict) else None
        return line if isinstance(line, str) else None
    return data


async def _call(provider):
    """Call *provider* (sync or async); ``None`` provider yields ``None``."""
    if provider is None:
        return None
    value = provider()
    if inspect.isawaitable(value):
        value = await value
    return value


async def _disconnected(request) -> bool:
    """``request.is_disconnected()`` that never raises."""
    checker = getattr(request, "is_disconnected", None)
    if checker is None:
        return False
    with contextlib.suppress(Exception):
        return bool(await checker())
    return False  # pragma: no cover - defensive
