package dev.raticode.mobile.childlock

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * Where the child lock's edge handle is. The handle on the main window, on full-screen video and on the lock screen
 * share this, so it stays where the user left it and the lock that turns child lock on is the one that turns it off.
 * It starts tucked away off the right edge; a tap on that edge brings it out.
 */
internal object EdgeHandle {
    /** How long a finger must stay on the lock before Android is asked for the screen lock. Not an animation: it doesn't shrink when animations are off. */
    const val HOLD_MS = 1_500L
    /** How long the handle stays out with nothing touching it, before Android's accessibility timeout is applied. */
    const val IDLE_MS = 4_000L
    /** How long the first tap of the handle waits for the second. */
    const val ARMED_MS = 3_000L
    /** Dragged further than this share of its width, the handle goes away when let go. */
    const val HIDE_SHARE = 0.4f
    /** A flick faster than this, in dp per second, decides the direction on its own. */
    const val FLING_DP_PER_S = 400f

    var shown by mutableStateOf(false); private set
    /** Changes with every touch of the handle, which restarts its idle time. */
    var touches by mutableIntStateOf(0); private set
    /** Set when the lock has just turned on, so the lock screen's handle closes its padlock instead of starting closed. */
    var justLocked by mutableStateOf(false)

    fun show() { shown = true; touches++ }
    fun hide() { shown = false }
    fun touched() { touches++ }

    /**
     * Whether a handle let go after a drag goes away. [dragged] is how far it was pushed off the edge, from 0 (fully out)
     * to 1 (fully away); [velocity] is in dp per second, positive towards the edge.
     */
    fun hidesAfterDrag(dragged: Float, velocity: Float): Boolean = when {
        velocity > FLING_DP_PER_S -> true
        velocity < -FLING_DP_PER_S -> false
        else -> dragged > HIDE_SHARE
    }

    /** Whether a shown handle may tuck itself away now: not while a tap is armed, a finger holds it or Android is asking. */
    fun mayIdleHide(armed: Boolean, holding: Boolean, asking: Boolean) = !armed && !holding && !asking

    /** What the lock screen says after a touch, depending on whether the lock is out to be held. */
    fun lockedHint(shown: Boolean) =
        if (shown) "Child lock is on. Press and hold the lock to unlock."
        else "Child lock is on. Tap the right edge to show the lock, then press and hold it to unlock."
}
