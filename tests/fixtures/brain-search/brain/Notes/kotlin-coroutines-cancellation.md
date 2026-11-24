# Kotlin coroutines cancellation

Cancellation is cooperative: a coroutine only stops at a suspension point or when it checks isActive. Use withTimeout to bound work; it throws TimeoutCancellationException. Wrap cleanup in finally and use withContext(NonCancellable) for suspending cleanup. Never swallow CancellationException in a catch block.
