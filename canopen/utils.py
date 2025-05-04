"""Additional utility functions for canopen."""

import asyncio
from typing import Optional, Union, Iterable, Callable


def pretty_index(index: Optional[Union[int, str]],
                 sub: Optional[Union[int, str]] = None):
    """Format an index and subindex as a string."""

    index_str = ""
    if isinstance(index, int):
        index_str = f"0x{index:04X}"
    elif index:
        index_str = f"{index!r}"

    sub_str = ""
    if isinstance(sub, int):
        # Need 0x prefix if index is not present
        sub_str = f"{'0x' if not index_str else ''}{sub:02X}"
    elif sub:
        sub_str = f"{sub!r}"

    return ":".join(s for s in (index_str, sub_str) if s)


def call_callbacks(callbacks: Iterable[Callable], loop: asyncio.AbstractEventLoop | None = None, *args, **kwargs) -> bool:
    """Call a list of callbacks with the given arguments.

    """

    def dispatch():
        for callback in callbacks:
            result = callback(*args, **kwargs)
            if result is not None and asyncio.iscoroutine(result):
                asyncio.create_task(result)

    # If the loop is running, call the callbacks from the loop to minimize
    # blocking and multithreading issues.
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(dispatch)
        return False
    else:
        dispatch()
        return True
