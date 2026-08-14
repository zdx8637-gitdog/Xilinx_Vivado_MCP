"""
api_category.py — API classification decorators for all Zynq MCPs.

query   — read-only, idempotent, no side effects
set     — modifies state, idempotent (same inputs → same result)
command — NOT idempotent, produces new state each call
"""

from functools import wraps


def query(func):
    """Read-only, always idempotent."""
    func._api_category = "query"
    return func


def set_op(func):
    """Modifies state, idempotent."""
    func._api_category = "set"
    return func


def command(func):
    """Not idempotent, produces new state."""
    func._api_category = "command"
    return func
