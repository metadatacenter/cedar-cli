"""CEDAR release errors."""
from __future__ import annotations


class ReleaseError(RuntimeError):
    """A release input or immutable artifact failed validation."""


class RetryableReleaseError(ReleaseError):
    """A release step failed for a reason that may not hold a moment later.

    A release must survive a network fault without surviving a guard, so the two are
    different exceptions rather than the same one read for its wording. Direct
    transports and idempotent subprocesses may raise this for a narrow set of connection
    failures. A changed tree, authentication failure, registry byte mismatch, protected-ref
    refusal, or Nexus HTTP 500 is never retryable.
    """
