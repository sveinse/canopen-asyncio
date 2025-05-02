""" Utils for async """
import functools
import threading

# NOTE: Global, but needed to be able to use ensure_not_async() in
#       decorator context.
_ASYNC_SENTINELS: dict[int, bool] = {}


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
            raise RuntimeError(f"Calling a blocking function in async. {fn.__qualname__}() in {fn.__code__.co_filename}:{fn.__code__.co_firstlineno}, while running async")
        return fn(*args, **kwargs)
    return async_guard_wrap
