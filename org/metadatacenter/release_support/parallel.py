"""Bounded independent release checks; callers retain ownership of ledger writes."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context


def checked_map(check, items, *, workers=4):
    """Return results in input order and propagate failures after running work settles."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(copy_context().run, check, item) for item in items]
        try:
            return [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            raise
