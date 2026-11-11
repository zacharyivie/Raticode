package dev.raticode.mobile.live

/**
 * Notices a session that went quiet after the phone sent something.
 *
 * A TCP connection can die without a close: the desktop sleeps, the phone changes networks, or
 * a Tailscale path moves. Writes still succeed into the socket buffer and reads just time out,
 * so without this the phone would show "connected" until the kernel gives up, many minutes
 * later. A healthy desktop answers the phone's sync request every few seconds, so any frame
 * from it clears the watch.
 */
internal class ReplyWatch(private val limitMillis: Long = STALL_MILLIS) {
    private var waitingSince: Long? = null
    fun sent(now: Long) { if (waitingSince == null) waitingSince = now }
    fun heard() { waitingSince = null }
    fun stalled(now: Long): Boolean = waitingSince?.let { now - it >= limitMillis } == true
    companion object {
        /** Well above the 5 s sync interval and slow relayed replies, far below TCP's own timeout. */
        const val STALL_MILLIS = 45_000L
    }
}

internal fun stalledGuidance(route: String) =
    "The desktop stopped answering over $route. Reconnecting; queued messages stay on this phone."
