# Python asyncio timeouts

asyncio.timeout() (Python 3.11) cancels the enclosing task when the deadline passes and raises TimeoutError. asyncio.wait_for does the same for one awaitable. Cancelled tasks receive CancelledError at their next await; re-raise it after cleanup. Shield work that must finish with asyncio.shield.
