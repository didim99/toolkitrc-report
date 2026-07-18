"""
Small shared helpers.
"""

from __future__ import annotations


def format_duration(seconds: int) -> str:
    """
    Format a duration in seconds as H:MM:SS.
    """

    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return '{}:{:02d}:{:02d}'.format(hours, minutes, secs)
