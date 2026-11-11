package dev.raticode.mobile.browser

/**
 * Decides when the browser's top controls fold into the slim address strip and when they come back. Scrolling the page
 * down folds them, scrolling up brings them back, and they fold on their own after a while without use. Distances are
 * in dp and times in milliseconds of one clock. Plain Kotlin, so unit tests can run it without a device.
 */
internal class BrowserControlsMotion {
    var expanded = true
        private set
    private var held = false
    private var lastY: Int? = null
    private var travelled = 0
    private var settlesAt = 0L
    private var lastActivity = 0L

    /** The page's root scroll position moved to [y]. */
    fun scrolled(y: Int, now: Long) {
        val previous = lastY
        lastY = y
        if (previous == null || y == previous || held) { if (held) travelled = 0; return }
        // Folding resizes the page, which can nudge its scroll position. Those moves aren't the user's.
        if (now < settlesAt) { travelled = 0; return }
        val delta = y - previous
        if ((delta > 0) != (travelled > 0) && travelled != 0) travelled = 0
        travelled += delta
        if (expanded) {
            if (delta < 0) lastActivity = now
            if (travelled >= HIDE_AFTER_DP && y > TOP_DP) change(false, now)
        } else if (travelled <= -SHOW_AFTER_DP || (delta < 0 && y <= TOP_DP)) change(true, now)
    }

    /** The user asked for the controls by tapping the strip or the bottom bar's Browser item. */
    fun reveal(now: Long) {
        show(now)
        // The page may still be coasting from the last swipe; that mustn't fold what the user just asked for.
        settlesAt = maxOf(settlesAt, now + REVEAL_HOLD_MS)
    }

    /** Another page or tab is showing: its scroll position is unrelated to the last one. */
    fun pageChanged(now: Long) { lastY = null; travelled = 0; show(now) }

    /** The user is working with the controls: the idle wait starts again. */
    fun touched(now: Long) { lastActivity = now }

    /** While held the controls stay open: editing the address, a menu or dialog, an error page, or a screen reader. */
    fun hold(on: Boolean, now: Long) { held = on; if (on) show(now) else touched(now) }

    /** Milliseconds until the controls should fold for being idle; zero or less means now. */
    fun idleRemaining(now: Long, timeout: Long): Long = if (timeout == Long.MAX_VALUE) Long.MAX_VALUE else lastActivity + timeout - now

    fun idled(now: Long) { if (!held) change(false, now) }

    private fun show(now: Long) { change(true, now); lastActivity = now }

    private fun change(to: Boolean, now: Long) {
        if (expanded == to) return
        expanded = to
        travelled = 0
        settlesAt = now + SETTLE_MS
        lastActivity = now
    }

    companion object {
        const val HIDE_AFTER_DP = 24
        const val SHOW_AFTER_DP = 32
        const val TOP_DP = 2
        const val SETTLE_MS = 350L
        const val REVEAL_HOLD_MS = 1_500L
        const val BUTTON_DP = 48
        const val MIN_ADDRESS_DP = 96
        private const val ROW_PADDING_DP = 8

        /** How long the controls stay open unused. Emulator tests that use the controls after a slow page load turn this off. */
        @Volatile var idleMillis = 5_000L

        /**
         * Whether Forward fits on the toolbar of a window [widthDp] wide while leaving the address readable. When it
         * doesn't, Forward is in the menu instead.
         */
        fun forwardFits(widthDp: Int, hasPageBack: Boolean): Boolean {
            val buttons = (if (hasPageBack) 6 else 5) * BUTTON_DP
            return widthDp - ROW_PADDING_DP - buttons >= MIN_ADDRESS_DP
        }
    }
}
