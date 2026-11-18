"""Loop-scoped async resources: opened once, reused, and always closed with the loop."""
import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class LoopResource(Generic[T]):
    """Hold one async context manager open per event loop and share its value.

    A guardian task enters and exits the context manager itself. The MCP SDK requires
    that (its anyio task groups must be closed by the task that opened them), and it
    means the resource is released on ``aclose()``, on ``discard()`` or when the event
    loop cancels its remaining tasks at shutdown, even if nobody closes it explicitly.
    """

    def __init__(self, factory: Callable[[], AbstractAsyncContextManager[T]], *, open_timeout: float):
        self._factory = factory
        self._open_timeout = open_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._guardian: asyncio.Task | None = None
        self._release: asyncio.Event | None = None
        self._value: T | None = None

    def _bind(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # First use, or the loop that owned the previous guardian is gone.
            self._loop, self._lock = loop, asyncio.Lock()
            self._guardian = self._release = self._value = None
        return self._lock

    async def get(self) -> tuple[T, bool]:
        """Return ``(value, opened_now)``; concurrent callers share a single open."""
        async with self._bind():
            if self._guardian is not None and not self._guardian.done():
                return self._value, False
            ready = self._loop.create_future()
            self._release = asyncio.Event()
            self._guardian = self._loop.create_task(self._hold(ready, self._release))
            try:
                self._value = await asyncio.wait_for(ready, self._open_timeout)
            except BaseException:
                await self._stop()
                raise
            return self._value, True

    async def discard(self, value: T | None = None) -> None:
        """Close the held value so the next ``get()`` reopens it.

        Passing the value that failed makes this idempotent: when several callers hit
        the same broken resource only the first one closes it, and nobody closes the
        replacement that another caller has already opened.
        """
        async with self._bind():
            if value is None or value is self._value:
                await self._stop()

    async def aclose(self) -> None:
        await self.discard()

    async def _hold(self, ready: asyncio.Future, release: asyncio.Event) -> None:
        try:
            async with self._factory() as value:
                if not ready.done():
                    ready.set_result(value)
                await release.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except Exception as exc:
            # Open failed (reported to the waiting caller) or close failed (nothing
            # useful to do: the value is already unusable and has been dropped).
            if not ready.done():
                ready.set_exception(exc)

    async def _stop(self) -> None:
        guardian, release, opened = self._guardian, self._release, self._value is not None
        self._guardian = self._release = self._value = None
        if guardian is None or guardian.done():
            return
        if opened:
            release.set()  # leave the context manager normally
        else:
            guardian.cancel()  # still opening: abandon it
        await asyncio.wait({guardian})
