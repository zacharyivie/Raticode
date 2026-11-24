package dev.raticode.mobile.browser

/**
 * One navigation queue per Gecko tab while its back/forward list is being restored.
 *
 * A tab whose session was closed (to save memory, after a profile switch, or after its page process ended) reopens by
 * restoring its saved list. GeckoView applies `restoreState` later, on its own thread, so an address loaded straight
 * after it could be replaced by the restored page. The tab therefore restores first, waits for the restored page's
 * location change, then loads the address the user asked for, keeping the back history. If the restore never reports
 * within [WAIT_MS], the address loads anyway. A newer address replaces an older one still waiting.
 */
internal class RestoreQueue(private val clock: () -> Long = System::currentTimeMillis) {
    companion object {
        const val WAIT_MS = 2_000L
    }

    private class Restoring(val deadline: Long, var url: String? = null)

    private val restoring = HashMap<String, Restoring>()

    /** Tab [id] has just asked Gecko to restore its list. */
    fun started(id: String) { restoring[id] = Restoring(clock() + WAIT_MS) }

    /**
     * Tab [id] should load [url]. Returns true when it must wait for the restore (the queue keeps the newest address);
     * false when nothing is being restored, or the restore is overdue, and it can load now.
     */
    fun defer(id: String, url: String): Boolean {
        val entry = restoring[id] ?: return false
        // A restore that never reported doesn't hold up later addresses.
        if (clock() >= entry.deadline) { restoring.remove(id); return false }
        entry.url = url
        return true
    }

    /**
     * Tab [id]'s page changed to [address]. The first real page after a restore ends the wait; returns the address to
     * load now, or null when none was waiting. The initial about:blank of a new session doesn't count.
     */
    fun locationChanged(id: String, address: String): String? {
        if (address == "about:blank") return null
        return restoring.remove(id)?.url
    }

    /** The restore didn't report in time: returns the address to load anyway, or null when nothing is due. */
    fun expired(id: String): String? {
        val entry = restoring[id] ?: return null
        if (clock() < entry.deadline) return null
        restoring.remove(id)
        return entry.url
    }

    /** Tab [id]'s session closed or the tab went away; whatever waited is dropped. */
    fun forget(id: String) { restoring.remove(id) }
}
