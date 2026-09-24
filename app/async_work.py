"""Cancellation-safe bridge for synchronous, side-effecting repository work."""
import asyncio


async def durable_call(fn, /, *args, **kwargs):
    """Drain an admitted write before propagating cancellation to its owner.

    Cancelling to_thread alone leaves its transaction running after the caller
    has released locks or finalized the run. Never replay an uncertain write.
    """
    task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    try:
        result = task.result()
    finally:
        if cancelled:
            raise asyncio.CancelledError
    return result
