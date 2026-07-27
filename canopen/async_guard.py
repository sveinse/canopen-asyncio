""" Utils for async """
import functools
import logging
import threading
import traceback


_ASYNC_SENTINELS: dict[int, bool] = {}
"""Per-thread boolean indicating allowance of running blocking functions.

Any function that is guarded with the :code:`@ensure_not_async` decorator will
fail with a :code:`RuntimeError` if called if True for this thread. The
application sets this value to True using :code:`set_async_sentinel(True)`
when it is running async code.
"""

logger = logging.getLogger(__name__)


def set_async_sentinel(enable: bool):
    """ Register a function to validate if async is running """
    _ASYNC_SENTINELS[threading.get_ident()] = enable


def ensure_not_async(fn):
    """ Decorator that will ensure that the function is not called if async
        is running.
    """
    @functools.wraps(fn)
    def async_guard_wrap(*args, **kwargs):
        if _ASYNC_SENTINELS.get(threading.get_ident(), False):
            st = "".join(traceback.format_stack())
            logger.debug("Traceback:\n%s", st.rstrip())
            raise RuntimeError(f"Calling a blocking function, {fn.__qualname__}() in {fn.__code__.co_filename}:{fn.__code__.co_firstlineno}, while running async")
        return fn(*args, **kwargs)
    return async_guard_wrap


class AllowBlocking:
    """ Context manager to pause async guard """
    def __init__(self):
        self._enabled = _ASYNC_SENTINELS.get(threading.get_ident(), False)

    def __enter__(self):
        set_async_sentinel(False)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        set_async_sentinel(self._enabled)
