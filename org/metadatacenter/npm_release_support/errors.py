"""Errors the npm release route raises."""
from __future__ import annotations


class NpmReleaseError(RuntimeError):
    """A release precondition or stage failed; the ledger keeps what was proved before it."""


class NpmReleaseWait(NpmReleaseError):
    """A stage cannot finish yet because something outside the route has not happened.

    The run stops without a failure, and `resume` asks again.
    """
