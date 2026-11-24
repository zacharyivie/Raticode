package dev.raticode.mobile.live

import org.json.JSONObject

/**
 * One thing for this phone's alerts. [kind] is `reply` or `task_update` for Rem's chat messages, `proposals` for one self-work
 * round's batch, `organization` for an organization's task, `workflow` for a message a workflow sent to the phone and `decision`
 * for a question of Rem's that waits for the user while no task stops for it (auto mode). [reason] is the alert switch it counts
 * under, empty when it counts because this phone asked for the work. A workflow message names its [workflowId] and [runId], which a
 * tap opens. [moreFromRun] is set on a run's one "And N more from this run" notice: N, the messages past the desktop's per-run cap
 * that it stands for. A question names its [decisionId], which a tap opens on the Decisions screen, its [question], its
 * [projectName] (empty for one about the user) and Rem's [recommended] answer.
 */
data class RemNotice(val id: String, val kind: String, val body: String, val createdAt: String, val title: String, val needsUser: Boolean, val reason: String = "",
                     val workflowId: String = "", val runId: String = "", val workflowName: String = "", val moreFromRun: Int = 0,
                     val decisionId: String = "", val question: String = "", val projectName: String = "", val recommended: String = "")

/**
 * The desktop's `rem.notices` answer. [cursor] is the last message position it read; [more] means read again at once.
 * [mark] is the desktop's opaque position in everything that isn't a Rem chat message; empty when the read carried no filter.
 * [dropped] counts workflow messages the desktop deleted, to keep its newest 500, before this phone read them.
 */
data class RemNotices(val cursor: Long, val notices: List<RemNotice>, val skipped: Int, val more: Boolean, val mark: String = "", val dropped: Int = 0)

/**
 * What Rem's chat alerts say. The desktop picks which messages count; the phone decides what an alert shows.
 * Unless the user turns on "Show Rem's words", an alert never carries Rem's text or a task name.
 */
object RemNoticeText {
    const val MAX = 8
    private const val SHOWN = 600
    /** Messages kept in one alert's history, including the user's replies. */
    const val HISTORY = 12
    private const val MARK_MAX = 256
    private const val COUNT_MAX = 1_000_000
    private val KINDS = setOf("task_update", "proposals", "organization", RemAlertRules.WORKFLOW, RemAlertRules.DECISION)
    /** A decision ID as the desktop writes it, such as `D-12`. */
    private val DECISION_ID = Regex("D-[0-9]{1,9}")

    /**
     * The `rem.notices` request. Only a desktop whose overview advertises the filter gets [include] or [mark], and only while
     * a switch is on; every other read is the plain request an older desktop already takes.
     */
    fun readParams(after: Long?, filterSupported: Boolean, include: List<String>, mark: String?): JSONObject {
        val params = JSONObject()
        if (after != null) params.put("after", after)
        if (filterSupported && include.isNotEmpty()) {
            params.put("include", org.json.JSONArray(include))
            if (!mark.isNullOrEmpty()) params.put("mark", mark)
        }
        return params
    }

    fun parse(json: JSONObject): RemNotices {
        val cursor = json.getLong("cursor")
        require(cursor in 0 until (1L shl 53))
        val list = json.optJSONArray("notices")
        val notices = (0 until minOf(list?.length() ?: 0, MAX)).map { list!!.getJSONObject(it) }.map {
            val kind = it.optString("kind").takeIf { name -> name in KINDS } ?: "reply"
            // A question without a decision ID it could open is shown as an ordinary Needs you line.
            val decision = it.optJSONObject("decision")?.takeIf { kind == RemAlertRules.DECISION }
            val decisionId = decision?.optString("id")?.takeIf { id -> DECISION_ID.matches(id) }.orEmpty()
            val kept = if (kind == RemAlertRules.DECISION && decisionId.isEmpty()) "task_update" else kind
            val reason = when (kept) {
                "proposals" -> RemAlertRules.PROPOSALS
                RemAlertRules.WORKFLOW -> RemAlertRules.WORKFLOW
                // Questions count under Needs you, whatever else the answer says.
                RemAlertRules.DECISION -> RemAlertRules.NEEDS_YOU
                else -> it.optString("reason").takeIf { name -> name in RemAlertRules.FILTERS }.orEmpty()
            }
            val workflow = kept == RemAlertRules.WORKFLOW
            val asks = kept == RemAlertRules.DECISION
            RemNotice(it.getString("id").take(128), kept, it.optString("body").take(1500), it.optString("created_at").take(80),
                it.optString("title").take(160), (it.optBoolean("needs_user") && !workflow) || asks, reason,
                if (workflow) it.optString("workflow_id").take(200) else "", if (workflow) it.optString("run_id").take(128) else "",
                if (workflow) it.optString("workflow_name").take(160) else "",
                if (workflow) it.optInt("more_from_run").coerceIn(0, COUNT_MAX) else 0,
                decisionId, if (asks) decision!!.optString("question").take(500) else "",
                if (asks) it.optString("project_name").take(160) else "", if (asks) decision!!.optString("recommended").take(300) else "")
        }
        // An opaque position can't be shortened, so one that is too long counts as none.
        val mark = (json.opt("mark") as? String)?.takeIf { it.length <= MARK_MAX }.orEmpty()
        return RemNotices(cursor, notices, json.optInt("skipped").coerceIn(0, 100_000), json.optBoolean("more"), mark,
            json.optInt("dropped").coerceIn(0, COUNT_MAX))
    }

    /** The alert line for one message. */
    fun line(notice: RemNotice, showText: Boolean): String {
        if (notice.kind == RemAlertRules.DECISION) return RemAlertRules.decisionText(notice, showText)
        val generic = when {
            notice.kind == "proposals" -> "Proposals are waiting"
            notice.kind == RemAlertRules.WORKFLOW -> RemAlertRules.WORKFLOW_GENERIC
            notice.reason == RemAlertRules.NEEDS_YOU -> "A task needs you"
            notice.reason == RemAlertRules.FINISHED -> "A task finished"
            notice.kind == "task_update" && notice.needsUser -> "A task needs you"
            notice.kind == "task_update" || notice.kind == "organization" -> "Task update"
            else -> "New message"
        }
        return if (showText) plain(notice.body).ifBlank { generic } else generic
    }

    /** The user's own reply as the alert shows it. */
    fun reply(text: String, showText: Boolean): String = if (showText) plain(text).ifBlank { "You replied" } else "You replied"

    /** A note for messages that didn't fit in one read. */
    fun earlier(skipped: Int): String? = when {
        skipped <= 0 -> null
        skipped == 1 -> "1 earlier message from Rem"
        else -> "$skipped earlier messages from Rem"
    }

    /** Markdown reduced to one readable line for the notification shade. */
    fun plain(markdown: String): String {
        val text = markdown
            .replace(Regex("```[^\\n]*"), " ")
            .replace(Regex("!\\[([^\\]]*)\\]\\([^)]*\\)"), "$1")
            .replace(Regex("\\[([^\\]]+)\\]\\([^)]*\\)"), "$1")
            .replace(Regex("(?m)^\\s{0,3}(#{1,6}|>|[-*+]|\\d+[.)])\\s+"), "")
            .replace(Regex("(\\*\\*|__|\\*|`|~~)"), "")
            .replace(Regex("\\s+"), " ")
            .trim()
        return if (text.length <= SHOWN) text else text.take(SHOWN - 1).trimEnd() + "…"
    }

    /** The message time in milliseconds, or null when the desktop's time can't be read. */
    fun millis(createdAt: String): Long? = runCatching { java.time.OffsetDateTime.parse(createdAt).toInstant().toEpochMilli() }.getOrNull()
}

/**
 * Drops queued `rem.notices` reads that carry the filter, for a desktop that has stopped advertising it. With [workflowOnly],
 * drops only the reads that name the workflow switch, for a desktop that still takes the filter but not that switch.
 */
internal fun dropQueuedNoticeFilters(state: JSONObject, workflowOnly: Boolean = false): Boolean {
    val outbox = state.optJSONArray("outbox") ?: return false
    val kept = org.json.JSONArray()
    for (i in 0 until outbox.length()) {
        val event = JSONObject(outbox.getString(i))
        val payload = event.optJSONObject("payload")
        val include = payload?.optJSONObject("params")?.optJSONArray("include")
        val named = include != null && (!workflowOnly || (0 until include.length()).any { include.optString(it) == RemAlertRules.WORKFLOW })
        val filtered = event.optString("type") == "desktop.read" && payload?.optString("op") == "rem.notices" && named
        if (!filtered) kept.put(outbox.getString(i))
    }
    if (kept.length() == outbox.length()) return false
    state.put("outbox", kept)
    return true
}

/**
 * Drops `rem.notices` reads left in the outbox from an earlier session. The next desktop may be an older one that
 * rejects the unknown op and closes the session; the phone asks again once that desktop's overview reports support.
 */
internal fun dropQueuedNoticeReads(state: JSONObject): Boolean {
    val outbox = state.optJSONArray("outbox") ?: return false
    val kept = org.json.JSONArray()
    for (i in 0 until outbox.length()) {
        val event = JSONObject(outbox.getString(i))
        if (event.optString("type") != "desktop.read" || event.optJSONObject("payload")?.optString("op") != "rem.notices") kept.put(outbox.getString(i))
    }
    if (kept.length() == outbox.length()) return false
    state.put("outbox", kept)
    return true
}
