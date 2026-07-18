"""
Small shared helpers.
"""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """
    Format a duration for display, according to its magnitude.

    Values below one minute are shown as ``SS s``, below one hour as
    ``mm:SS`` and the rest as ``HH:mm:SS``.
    """

    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if total < 60:
        return '{} s'.format(secs)
    if total < 3600:
        return '{:02d}:{:02d}'.format(minutes, secs)
    return '{:02d}:{:02d}:{:02d}'.format(hours, minutes, secs)


def format_number(value: float, precision: int = 1) -> str:
    """
    Format a number with a no-break space as thousands separator.
    """

    text = '{:,.{}f}'.format(value, precision)
    return text.replace(',', '\u00a0')
