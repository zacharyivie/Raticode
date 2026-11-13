package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/**
 * Which desktop runs this phone started, and what the progress notification says about them. In plain Kotlin so
 * unit tests cover it; [TaskNotification] only carries the result out with Android's APIs.
 *
 * A run counts when the user asked for it here: a task Rem started in answer to a message sent from this phone, or
 * Run now, Retry review or a new task from this phone's task screens. Self-work never counts: the desktop posts no chat
 * message for its runs, so nothing links one to a request, and a run the phone didn't ask for is never claimed.
 */
object TaskProgress {
    /** Where the claims live in the encrypted journal. */
    const val KEY = "task_progress"
    /** How long after a request the phone looks for the run it started. Rem's reply can take minutes. */
    const val WATCH_MS = 10 * 60_000L
    /** All the lock screen shows, whatever the notification holds and whichever switches are on. */
    const val LOCK_TITLE = "Rem"
    const val LOCK_TEXT = "Rem is working on a task"
    const val STEPS = 4
    private const val KEPT = 16
    private const val TITLE_LIMIT = 120

    /** Something the phone asked for, and when, by the phone's clock. */
    data class Asked(val id: String, val at: Long)

    /**
     * [requests] are this phone's Rem chat messages, by ID. [asked] are `task:`, `run:` and `title:` names from its task screens.
     * [runs] are the runs found to be this phone's and still running. [hidden] are the runs the user dismissed.
     */
    data class Claims(val requests: List<Asked> = emptyList(), val asked: List<Asked> = emptyList(),
                      val runs: List<String> = emptyList(), val hidden: List<String> = emptyList())

    /** A phone-started run that is running now. */
    data class Run(val id: String, val title: String, val state: String, val createdAt: String, val reachedCheck: Boolean, val step: String = "")

    /** One notification's content. [generic] is the title without the user's words; [runs] are the runs it stands for. */
    data class Frame(val title: String, val generic: String, val text: String, val spoken: String, val chip: String,
                     val done: Int, val startedAt: Long?, val runs: List<String>) {
        val words get() = title != generic
        fun withoutWords() = copy(title = generic)
    }

    fun load(state: JSONObject): Claims {
        val json = state.optJSONObject(KEY) ?: return Claims()
        fun asked(name: String) = json.optJSONArray(name).let { list -> (0 until (list?.length() ?: 0)).map { list!!.getJSONObject(it) }.map { Asked(it.getString("id"), it.getLong("at")) } }
        fun names(name: String) = json.optJSONArray(name).let { list -> (0 until (list?.length() ?: 0)).map { list!!.getString(it) } }
        return Claims(asked("requests"), asked("asked"), names("runs"), names("hidden"))
    }

    fun save(state: JSONObject, claims: Claims) {
        fun asked(list: List<Asked>) = JSONArray(list.map { JSONObject().put("id", it.id).put("at", it.at) })
        state.put(KEY, JSONObject().put("requests", asked(claims.requests)).put("asked", asked(claims.asked))
            .put("runs", JSONArray(claims.runs)).put("hidden", JSONArray(claims.hidden)))
    }

    /** Records a desktop request that can start a run, in the journal write that queues it. Every other request adds nothing. */
    fun note(state: JSONObject, op: String, params: JSONObject, request: String, now: Long) {
        val claims = load(state)
        val asked = when (op) {
            "rem.task.run" -> "task:" + params.optString("task_id")
            "rem.run.retry_review" -> "run:" + params.optString("run_id")
            "rem.task.create" -> "title:" + params.optString("title")
            "rem.message" -> { save(state, claims.copy(requests = (claims.requests + Asked(request, now)).takeLast(KEPT))); return }
            else -> return
        }
        save(state, claims.copy(asked = (claims.asked + Asked(asked, now)).takeLast(KEPT)))
    }

    private fun recent(item: Asked, now: Long) = now - item.at in 0..WATCH_MS

    private fun immediate(task: RemTask) = task.schedule.kind == "once" && task.schedule.at.isBlank()

    /** Adds the running runs that [home] shows this phone started, and forgets the runs and requests that are over. */
    fun claim(claims: Claims, home: RemHome, now: Long): Claims {
        val running = home.tasks.filter { it.activeRun?.active == true }
        val found = linkedSetOf<String>()
        // Rem's messages after this phone's request, up to the next request from anywhere, are its answer.
        // A schedule's or the desktop's own run also posts a message, so only a new one-time run answers the request.
        val requests = claims.requests.filter { recent(it, now) }.mapTo(mutableSetOf()) { it.id }
        var request: RemMessage? = null
        for (message in home.messages) {
            if (message.role == "user") { request = message.takeIf { it.id in requests }; continue }
            val source = request ?: continue
            val work = message.work ?: continue
            val task = running.firstOrNull { it.id == work.taskId } ?: continue
            val run = task.activeRun!!
            if (run.id != work.runId || !immediate(task)) continue
            val started = RemRich.instant(run.createdAt) ?: continue
            val sent = RemRich.instant(source.createdAt) ?: continue
            if (!started.isBefore(sent)) found += run.id
        }
        val asked = claims.asked.filter { recent(it, now) }.filterNot { item ->
            val kind = item.id.substringBefore(':')
            val name = item.id.substringAfter(':')
            val task = running.firstOrNull { task -> when (kind) {
                "task" -> task.id == name
                "run" -> task.activeRun!!.id == name
                else -> task.title == name && immediate(task)
            } }
            task?.activeRun?.let { found += it.id }
            task != null
        }
        val active = running.mapTo(mutableSetOf()) { it.activeRun!!.id }
        return Claims(claims.requests.filter { recent(it, now) }, asked, (claims.runs + found).distinct().filter { it in active }.takeLast(KEPT), claims.hidden)
    }

    /** The claimed runs to show, newest first. A dismissed run stays out until it ends. */
    fun running(claims: Claims, home: RemHome): List<Run> = home.tasks.mapNotNull { task ->
        task.activeRun?.takeIf { it.active && it.id in claims.runs && it.id !in claims.hidden }?.let {
            Run(it.id, task.title, it.state, it.createdAt, it.verificationThreadId.isNotBlank(), it.step)
        }
    }.sortedWith(compareByDescending { RemRich.instant(it.createdAt) })

    /** Marks [runs] dismissed, so the notification doesn't come back for them after a restart or a new read. */
    fun hide(claims: Claims, runs: List<String>) = claims.copy(hidden = (claims.hidden + runs).distinct().takeLast(KEPT))

    /** Whether reading Rem's tasks in the background could change the notification. It stays false while the phone has no reason to look. */
    fun watching(claims: Claims, now: Long) = claims.runs.any { it !in claims.hidden } || claims.requests.any { recent(it, now) } || claims.asked.any { recent(it, now) }

    /** What to show for [runs], or null for nothing. Without the Show Rem's words opt-in it never names a task. */
    fun frame(runs: List<Run>, showWords: Boolean): Frame? {
        val run = runs.firstOrNull() ?: return null
        // A paused run isn't working, so it has no "Rem is working" notification.
        if (run.state == RunPause.PAUSED) return null
        val steps = RemRich.steps(run.state, run.reachedCheck, run.step)
        if (steps.isEmpty()) return null
        val generic = if (runs.size == 1) "Rem is working on your task" else "Rem is working on ${runs.size} tasks"
        val name = RemNoticeText.plain(run.title).let { if (it.length <= TITLE_LIMIT) it else it.take(TITLE_LIMIT - 1).trimEnd() + "…" }
        val title = if (!showWords || name.isBlank()) generic else if (runs.size == 1) name else "$name and ${runs.size - 1} more"
        return Frame(title, generic, steps.joinToString(" · ") { "${it.symbol} ${it.label}" }, steps.joinToString(", ") { it.spoken },
            steps.firstOrNull { it.state == RemRich.StepState.CURRENT }?.label ?: steps.last().label,
            steps.count { it.state == RemRich.StepState.DONE }, RemRich.instant(run.createdAt)?.toEpochMilli(), runs.map { it.id })
    }
}
