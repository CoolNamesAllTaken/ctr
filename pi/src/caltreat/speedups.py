"""Makes NiceGUI's page building cheaper on the Pi (the asyncio loop builds pages, and every page freezes meanwhile).

NiceGUI asks `inspect.signature` whether a callback expects arguments for every element and handler it makes: ~40%
of the settings page's build time (3,400 signature calls per page; on a Pi 4 the page took ~1 s, a freeze for every
phone). The answer depends only on the function's code and which of its parameters have defaults, so it is cached by
that. Anything unusual (partials, wrapped or callable objects, a __signature__) still goes to NiceGUI's own check.

    from . import speedups; speedups.install()
"""

import types

from nicegui import helpers
from nicegui.helpers import functions

_orig = functions.expects_arguments
_cache = {}


def _key(func):
    bound = isinstance(func, types.MethodType)
    f = func.__func__ if bound else func
    if type(f) is not types.FunctionType or hasattr(f, "__wrapped__") or hasattr(f, "__signature__"):
        return None
    return f.__code__, len(f.__defaults__ or ()), frozenset(f.__kwdefaults__ or ()), bound


def expects_arguments(func):
    k = _key(func)
    if k is None:
        return _orig(func)
    v = _cache.get(k)
    if v is None:
        v = _cache[k] = _orig(func)
    return v


def install():
    helpers.expects_arguments = expects_arguments
    functions.expects_arguments = expects_arguments
