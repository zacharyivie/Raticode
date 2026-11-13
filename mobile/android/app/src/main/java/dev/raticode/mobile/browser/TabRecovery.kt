package dev.raticode.mobile.browser

/**
 * What both built-in browsers do when a tab's page stops: its process was ended (Android frees memory from apps in the
 * background, and WebView or Gecko content processes go first) or crashed, or it stopped answering taps.
 *
 * Rules, the same for Raticode (chromium) and Raticode (gecko):
 * - Nothing is shown when a page stops. The tab keeps its address and back/forward list and reloads by itself the
 *   next time it's on screen: at once when the user is looking at it, otherwise when they come back to it.
 * - A page whose process is gone has lost whatever it held (unsent typing, a playing video), so reloading loses
 *   nothing more. Downloads run in Android's download manager, outside the page, and aren't affected.
 * - A page that keeps stopping gets [MAX_RESTORES] automatic reloads within [LOOP_WINDOW_MS], each one later than the
 *   last ([BACKOFF_MS]). After that the tab shows a quiet Reload button in place of the page, never a dialog.
 * - A page that stops answering taps but is still alive is left alone while the user isn't looking at it. Only the
 *   tab on screen, still not answering after [HANG_GRACE_MS], is ended and reloaded, and never while a page plays
 *   sound or a download from the page is being read, since those are still working.
 */
internal class TabRecovery(private val clock: () -> Long = System::currentTimeMillis) {
    companion object {
        const val MAX_RESTORES = 3
        const val LOOP_WINDOW_MS = 120_000L
        val BACKOFF_MS = longArrayOf(0L, 2_000L, 8_000L)
        const val HANG_GRACE_MS = 10_000L
    }

    sealed interface Decision {
        /** Reload by itself the next time the tab is on screen, but not sooner than [delayMs] from now. */
        data class Restore(val delayMs: Long) : Decision
        /** It has stopped too often: show a quiet Reload button in the tab instead. */
        data object GiveUp : Decision
    }

    enum class Hang { WAIT, END, LEAVE }

    private val restores = HashMap<String, ArrayDeque<Long>>()
    private val hungSince = HashMap<String, Long>()

    /** Tab [id]'s page process is gone. */
    fun gone(id: String): Decision {
        hungSince.remove(id)
        val recent = recent(id)
        return if (recent.size >= MAX_RESTORES) Decision.GiveUp else Decision.Restore(BACKOFF_MS[recent.size.coerceAtMost(BACKOFF_MS.lastIndex)])
    }

    /** Tab [id] was reloaded automatically. */
    fun restored(id: String) { restores.getOrPut(id) { ArrayDeque() }.addLast(clock()) }

    /** The user reloaded, went somewhere else in the tab, or closed it: start counting again. */
    fun forget(id: String) { restores.remove(id); hungSince.remove(id) }

    /** How many automatic reloads tab [id] had within the last [LOOP_WINDOW_MS]. */
    fun recentRestores(id: String) = recent(id).size

    private fun recent(id: String): ArrayDeque<Long> {
        val list = restores[id] ?: return ArrayDeque()
        val now = clock()
        while (list.isNotEmpty() && now - list.first() > LOOP_WINDOW_MS) list.removeFirst()
        return list
    }

    /**
     * Tab [id]'s page didn't answer a tap or a navigation in time. [inView]: it's the tab on screen, with Raticode in
     * front. [busy]: a page plays sound or a page file is being read, so ending the process would stop real work.
     */
    fun unresponsive(id: String, inView: Boolean, busy: Boolean): Hang {
        if (!inView) { hungSince.remove(id); return Hang.LEAVE }
        val since = hungSince.getOrPut(id) { clock() }
        if (clock() - since < HANG_GRACE_MS) return Hang.WAIT
        if (busy) return Hang.LEAVE
        hungSince.remove(id)
        return Hang.END
    }

    /** Tab [id]'s page answers again. */
    fun responsive(id: String) { hungSince.remove(id) }
}
