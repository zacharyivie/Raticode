package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/**
 * Phone views of desktop Rem, organizations, workflows, source control, settings and usage.
 * The desktop owns this data and carries out every action; nothing here is saved on the phone.
 */
object DesktopViews {
    const val MAX_PARTS = 48
    val READS = setOf("overview", "media.image", "media.preview", "rem.home", "rem.draft", "rem.memories", "rem.notices", "rem.task", "rem.thread", RemSearch.SEARCH, RemSearch.HISTORY, "organizations.list", "organizations.read", OrganizationModel.NOTE, "settings.get", "usage.overview", DeveloperLog.OP, DesktopBrowser.TABS) + Kanban.READS + Workflows.READS + SourceControl.READS + FileExplorer.READS
    val ACTIONS = setOf("rem.message", "rem.instruct", "rem.task.create", "rem.task.run", "rem.task.pause", "rem.task.resume",
        "rem.task.delete", "rem.run.cancel", "rem.run.acknowledge", "rem.run.retry_review",
        "rem.memory.add", "rem.memory.edit", "rem.memory.forget", "rem.memory.mode", "rem.memory.undo", VoiceTidy.KEY, "organizations.control", "organizations.task", OrganizationModel.KNOWLEDGE, OrganizationModel.WORKER, WorkSchedule.OP, "swarms.start", "settings.update", "files.link", FileExplorer.OPEN, RemAttachments.OP, DesktopBrowser.OPEN) + Kanban.ACTIONS + Workflows.ACTIONS
    val OPERATIONS = READS + ACTIONS

    /** The screen a read or action result refreshes. */
    fun key(op: String, params: JSONObject): String = when (op) {
        "rem.task" -> "rem.task:" + params.optString("task_id")
        "rem.thread", "rem.instruct" -> "rem.thread:" + params.optString("thread_id")
        "organizations.read", "organizations.control", "organizations.task", OrganizationModel.KNOWLEDGE, OrganizationModel.WORKER, WorkSchedule.OP ->
            "organizations.read:" + params.optString("organization_id")
        OrganizationModel.NOTE -> OrganizationModel.NOTE + ":" + params.optString("organization_id") + ":" + params.optString("note_id")
        "settings.update" -> "settings.get"
        Kanban.TICKET, Kanban.PARTICIPANT -> Kanban.BOARD
        Kanban.TIMELINE -> Kanban.timelineKey(params)
        Kanban.SETTINGS, Kanban.SEEN -> Kanban.SELF_WORK
        "media.image" -> ChatImages.KEY + params.optString("url")
        "media.preview" -> ChatPreviews.KEY + params.optString("url")
        "rem.draft" -> "rem.draft"
        "rem.notices" -> "rem.notices"
        // One search and one page of earlier messages at a time; each answer says what it is for.
        RemSearch.SEARCH -> RemSearch.SEARCH
        RemSearch.HISTORY -> RemSearch.HISTORY
        // Every memory change answers with the memory list; Undo's reply note refreshes with the chat.
        "rem.memories", "rem.memory.add", "rem.memory.edit", "rem.memory.forget", "rem.memory.mode", "rem.memory.undo" -> "rem.memories"
        "files.link" -> ChatLinks.KEY + params.optString("href")
        in FileExplorer.READS -> FileExplorer.viewKey(op, params)
        FileExplorer.OPEN -> FileExplorer.KEY + params.optString("project_id") + "\n" + params.optString("path")
        RemAttachments.OP -> RemAttachments.KEY + params.optString("file_id")
        VoiceTidy.KEY -> VoiceTidy.KEY
        // Run, stop and resume refresh the workflow screen, whichever run it shows.
        Workflows.READ, in Workflows.ACTIONS -> Workflows.READ + ":" + params.optString("workflow_id")
        in SourceControl.READS - SourceControl.LIST -> SourceControl.viewKey(op, params)
        else -> if (op.startsWith("rem.")) "rem.home" else op
    }

    fun parse(key: String, json: JSONObject): Any = when (key.substringBefore(':')) {
        "overview" -> DesktopOverview(json.objects("projects").map { it.optString("project_id") to it.optString("name") },
            json.optBoolean("rem"), json.optBoolean("organizations"), json.optBoolean("swarms"), json.optBoolean("settings"), json.optBoolean("usage"),
            json.optBoolean("media"), json.optBoolean("file_links"), json.optBoolean("workflows"), json.optBoolean("rich_results"),
            json.optBoolean("draft_deltas"), json.optBoolean("review_retry"), json.optBoolean("memories"), json.optBoolean("thread_read"),
            json.optBoolean("rem_notices"), json.optBoolean("voice_tidy"), json.optBoolean("source_control"),
            json.optBoolean("source_control_history"), json.optBoolean("attachment_files"), json.optBoolean("developer_log"),
            json.optBoolean("organizations_v2"), json.optBoolean("file_explorer"), browser = json.optBoolean("browser"), kanban = json.optBoolean("kanban"), kanbanParticipants = json.optBoolean("kanban_participants"),
            kanbanComments = json.optBoolean("kanban_comments"), organizationSchedule = json.optBoolean("organization_schedule"),
            remNoticesFilter = json.optBoolean("rem_notices_filter"), wakeMac = json.opt(WakeOnLan.FIELD) as? String)
        Kanban.BOARD -> Kanban.parseBoard(json)
        Kanban.SELF_WORK -> Kanban.parseSelfWork(json)
        Kanban.TIMELINE -> Kanban.parseTimeline(json)
        DesktopBrowser.TABS -> DesktopBrowser.parse(json)
        DesktopBrowser.OPEN -> DesktopBrowser.parseSent(json)
        "rem.draft" -> RemDraftDelta(json.optBoolean("replying"), json.optString("turn_label"), json.optInt("start"), json.optString("text"),
            json.optInt("length"), json.optBoolean("reset"), json.optLong("attempt", -1).takeIf { it >= 0 && !json.isNull("attempt") })
        "rem.notices" -> RemNoticeText.parse(json)
        RemSearch.SEARCH -> RemSearch.parse(json)
        RemSearch.HISTORY -> RemHistoryPage(json.objects("messages").map(::remMessage), json.optInt("earlier"),
            TicketMentions.parseIndex(json.optJSONObject("tickets")))
        Workflows.LIST -> Workflows.parseList(json)
        Workflows.READ -> Workflows.parse(json)
        in SourceControl.READS -> SourceControl.parseView(key, json)
        in FileExplorer.READS -> FileExplorer.parseView(key, json)
        "rem.home" -> RemHome(json.optString("turn_state", "idle"), json.optString("provider"), json.optString("model"), json.optInt("earlier"),
            json.objects("messages").map(::remMessage), json.objects("tasks").map(::remTask),
            json.optString("files_thread").takeIf(::canonicalId), json.optBoolean("media"), json.optBoolean("file_links"),
            json.optString("turn_label"), json.optString("draft_reply"), json.optBoolean("rich_results"),
            json.optBoolean("draft_deltas"), json.optBoolean("review_retry"), json.optBoolean("memories"),
            json.optBoolean("screen_context"), json.optBoolean("replies"), json.optBoolean("voice_tidy"), json.optBoolean("attachment_files"),
            TicketMentions.parseIndex(json.optJSONObject("tickets")), json.optBoolean("search"))
        "rem.memories" -> RemMemories(json.optString("mode", "on"), json.objects("memories").map {
            RemMemory(it.optString("id"), it.optString("text"), it.optString("created_at"), it.optString("edited_at"), it.optBoolean("added_by_user")) })
        "rem.task" -> remTask(json).copy(ticketIndex = TicketMentions.parseIndex(json.optJSONObject("tickets")))
        "rem.thread" -> RemThread(json.optString("thread_id"), json.optString("title"), json.optBoolean("running"), json.optInt("earlier"),
            json.objects("messages").map { RemThreadMessage(it.optString("id"), it.optString("role"), it.optString("body"), it.optString("kind"),
                it.optInt("count"), it.objects("steps").map { s -> RemThreadStep(s.optString("kind"), s.optString("title"), s.optString("detail"),
                    s.optString("body"), s.optString("command"), s.optString("status")) }, it.optString("sender")) })
        "organizations.list" -> json.objects("organizations").map { OrganizationSummary(it.optString("organization_id"), it.optString("name"),
            it.optString("state"), it.optInt("employees"), it.optInt("open_tasks"), it.strings("projects")) }
        OrganizationModel.NOTE -> OrganizationModel.parseNote(json)
        DeveloperLog.OP -> DeveloperLog.parse(json)
        "organizations.read" -> Organization(json.optString("organization_id"), json.optString("name"), json.optString("description"),
            json.optString("state"), json.strings("projects"),
            json.objects("employees").map { OrganizationEmployee(it.optString("employee_id"), it.optString("name"), it.optString("title"),
                it.optString("provider"), it.optString("model"), it.optBoolean("paused"), it.optString("reports_to")) },
            json.objects("tasks").map { t -> OrganizationTask(t.optString("task_id"), t.optString("title"), t.optString("description"),
                t.optString("status"), t.optString("priority"), t.optString("assignee"), t.optLong("revision", 1), t.optBoolean("running"),
                t.optBoolean("needs_approval"), t.optBoolean("needs_review"), t.optBoolean("attention"), t.optString("block_reason"),
                t.optString("evidence"), t.optString("due_at"),
                t.objects("comments").map { OrganizationComment(it.optString("actor"), it.optString("at"), it.optString("body"), it.optString("decision")) },
                OrganizationModel.parseExtras(t)) }, OrganizationModel.parsePlan(json), json.optString("pause_reason"), json.optInt("pending_requests"))
        "settings.get" -> DesktopSettings(json.optBoolean("available"), json.optInt("pending"),
            json.optJSONObject("values")?.let { v -> v.keys().asSequence().associateWith { k -> v.opt(k)?.takeUnless { it == JSONObject.NULL } } }.orEmpty(),
            json.objects("settings").map { s -> DesktopSetting(s.optString("key"), s.optString("group"), s.optString("label"), s.optString("kind"),
                (s.optJSONArray("options") ?: JSONArray()).let { a -> (0 until a.length()).map { a.getJSONArray(it).let { o -> o.getString(0) to o.getString(1) } } },
                s.optDouble("min", 0.0), s.optDouble("max", 0.0), s.optDouble("step", 1.0)) })
        "usage.overview" -> json.optJSONObject("activity").let { activity -> DesktopUsage(json.optInt("days", 30), json.optString("observed_at"),
            json.objects("accounts").map { a -> UsageAccount(a.optString("label"), a.optString("status"), a.optString("reason"),
                a.objects("windows").map { w -> UsageWindow(w.optString("label"), w.optDoubleOrNull("used_percent"), w.optString("resets_at")) }) },
            activity?.optInt("calls") ?: 0, activity?.optLongOrNull("input_tokens"), activity?.optLongOrNull("output_tokens"),
            activity?.objects("providers").orEmpty().map { UsageProvider(it.optString("provider"), it.optInt("calls"), it.optLongOrNull("input_tokens"), it.optLongOrNull("output_tokens")) }) }
        else -> json
    }

    private fun remMessage(m: JSONObject) = RemMessage(m.optString("id"), m.optString("role"), m.optString("body"), m.optString("created_at"),
        m.optJSONObject("work")?.let { RemWork(it.optString("task_id"), it.optString("run_id"), it.optString("thread_id"), it.optString("title"),
            it.optBoolean("task_available", true), it.optBoolean("thread_available", true), TicketMentions.parseRefs(it.optJSONArray("tickets")),
            it.optString("self_work").take(20)) },
        m.optString("state"),
        m.objects("attachments").map { RemFile(it.optString("file_id"), it.optString("name"), it.optLong("size")) },
        m.objects("files").map { RemFile(it.optString("file_id"), it.optString("name"), it.optLong("size")) },
        m.optString("details"), m.strings("suggestions").filter { it.isNotBlank() }.take(3),
        m.optJSONObject("meta")?.let { RemMeta(it.optString("provider"), it.optString("model"), it.optDoubleOrNull("seconds")) },
        m.optJSONObject("result")?.let(::remResult), m.optString("kind"), m.optJSONObject("memory")?.let(::remMemoryNote),
        // Only Rem's own replies may suggest phone actions; the phone checks each against its allowlist.
        if (m.optString("role") == "user") PhoneActionList.EMPTY else PhoneActions.parse(m.optJSONArray("phone_actions")),
        m.optString("refers_to").take(128), m.optJSONObject("referenced")?.let { remReference(m.optString("refers_to"), it) },
        // Full paths of files the desktop found in Rem's text; tapping one still goes through files.link.
        if (m.optString("role") == "user") emptyList() else ChatLinks.mentions(m.strings("file_mentions")),
        TicketMentions.parseSelfWork(m.optJSONObject("self_work")).takeIf { m.optJSONObject("work") != null },
        if (m.optString("role") == "user") m.strings("queued_after").filter { it.isNotBlank() }.take(10).map { it.take(160) } else emptyList())

    /** What a reply shows of the message it refers to. The desktop builds it; the phone only bounds it. */
    private fun remReference(id: String, r: JSONObject): RemReference? = when {
        id.isBlank() || r.optString("id") != id -> null
        r.optBoolean("unavailable") -> RemReference(id.take(128), unavailable = true)
        else -> RemReference(id.take(128), r.optString("role").take(20), r.optString("body").take(300), r.optString("created_at").take(80),
            r.optString("kind").take(40), r.optString("title").take(160), r.optInt("files").coerceIn(0, 99))
    }

    private fun remMemoryNote(n: JSONObject): RemMemoryNote? {
        fun entries(key: String) = n.objects(key).take(10).map { RemMemoryEntry(it.optString("id"), it.optString("text"), it.optBoolean("removed")) }
        val note = RemMemoryNote(entries("saved"), entries("updated"), entries("forgotten"), n.optInt("refused"), n.optBoolean("undone"))
        return note.takeIf { it.saved.isNotEmpty() || it.updated.isNotEmpty() || it.forgotten.isNotEmpty() || it.refused > 0 }
    }

    /** A finished task's card. Every field is optional; an older desktop sends none of it. */
    private fun remResult(r: JSONObject) = RemResult(r.optString("title"), r.optString("type"), r.optString("state"),
        remChecks(r.optJSONObject("checks")),
        r.objects("artifacts").filter { it.optString("href").isNotBlank() }.take(4).map {
            RemArtifact(it.optString("name"), it.optString("title"), it.optString("kind"), it.optLong("size"), it.optString("href")) },
        r.objects("sources").filter { ChatImages.fetchable(it.optString("url")) }.take(8).map {
            RemSource(it.optString("url"), it.optString("title"), it.optString("kind")) },
        r.optString("thread_id"))

    private fun remChecks(c: JSONObject?) = RemChecks(c?.optInt("passed") ?: 0, c?.optInt("failed") ?: 0, c?.optInt("total") ?: 0)

    private fun canonicalId(value: String) = runCatching { java.util.UUID.fromString(value).toString() == value }.getOrDefault(false)

    private fun remRun(r: JSONObject?) = r?.let { RemRun(it.optString("run_id"), it.optString("state"), it.optString("created_at"), it.optString("finished_at"),
        it.optString("summary"), it.optBoolean("needs_user"), it.optBoolean("acknowledged"), it.optString("thread_id"), it.optString("verification_thread_id"),
        it.optString("phase_label"), it.optString("waiting_reason"), remChecks(it.optJSONObject("checks")), it.optBoolean("review_retryable"),
        // Each check with its reason, sent only by a desktop that has them and only for a task's newest runs.
        if (it.has("criteria")) it.objects("criteria").take(40).map { c ->
            RemCheck(c.optString("id").take(128), c.optString("description").take(600), c.optString("result").take(20), c.optString("evidence").take(1500))
        } else null, TicketMentions.parseRefs(it.optJSONArray("tickets")), it.optString("step").take(20)) }

    private fun remTask(t: JSONObject) = RemTask(t.optString("task_id"), t.optString("title"), t.optString("type"), t.optString("state"),
        t.optLong("revision", 1), t.optJSONObject("schedule").let { s -> RemSchedule(s?.optString("kind") ?: "once", s?.optString("at").orEmpty(),
            s?.optString("local_time").orEmpty(), s?.strings("weekdays").orEmpty(), s?.optInt("every_minutes") ?: 0) },
        t.optString("next_occurrence_at"), t.optString("project_name"), remRun(t.optJSONObject("active_run")), remRun(t.optJSONObject("last_run")),
        t.optString("prompt"), t.optString("provider"), t.optString("model"), t.strings("criteria"),
        t.objects("runs").mapNotNull { remRun(it) }, t.optString("ran_at"), (t.opt("listed") as? Boolean), t.optString("cleanup_at"),
        dependency = t.optJSONObject("dependency")?.let(::remDependency))

    /** A waiting task's start condition. Older desktops never send one, and the task reads as before. */
    private fun remDependency(d: JSONObject) = RemDependency(d.optString("state").take(20), d.optString("reason").take(600),
        d.objects("prerequisites").take(10).map { RemPrerequisite(it.optString("task_id").take(128), it.optString("title").take(160),
            it.optString("state").take(20), it.optString("reason").take(300), it.optString("run_id").take(128), it.optString("run_state").take(40),
            it.optBoolean("retryable")) })

    private fun JSONObject.objects(key: String): List<JSONObject> = optJSONArray(key)?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
    private fun JSONObject.strings(key: String): List<String> = optJSONArray(key)?.let { a -> (0 until a.length()).map { a.optString(it) } }.orEmpty()
    private fun JSONObject.optDoubleOrNull(key: String): Double? = (opt(key) as? Number)?.toDouble()
    private fun JSONObject.optLongOrNull(key: String): Long? = (opt(key) as? Number)?.toLong()
}

/** Joins a result's parts. Partial results are bounded so abandoned requests cannot grow without limit. */
internal class DesktopParts(private val limit: Int = 16) {
    private val pending = LinkedHashMap<String, Array<String?>>()

    /** Returns the complete JSON body once every part arrived. */
    fun add(request: String, payload: JSONObject): JSONObject? {
        val count = payload.getInt("parts")
        val parts = pending.getOrPut(request) { arrayOfNulls(count) }
        require(parts.size == count) { "Result part count changed" }
        val index = payload.getInt("part")
        require(parts[index] == null || parts[index] == payload.getString("body")) { "Conflicting result part" }
        parts[index] = payload.getString("body")
        while (pending.size > limit) pending.remove(pending.keys.first())
        if (parts.any { it == null }) return null
        pending.remove(request)
        return JSONObject(parts.joinToString(""))
    }

    fun drop(request: String) { pending.remove(request) }
}

data class DesktopOverview(val projects: List<Pair<String, String>>, val rem: Boolean, val organizations: Boolean, val swarms: Boolean,
                           val settings: Boolean, val usage: Boolean, val media: Boolean = false, val fileLinks: Boolean = false,
                           val workflows: Boolean = false, val richResults: Boolean = false, val draftDeltas: Boolean = false,
                           val reviewRetry: Boolean = false, val memories: Boolean = false,
                           /** The desktop accepts thread.manage `read` and `unread` from this phone. */
                           val threadRead: Boolean = false,
                           /** The desktop answers `rem.notices`, the read behind Rem's chat alerts. */
                           val remNotices: Boolean = false,
                           /** Voice tidying is on in desktop Settings, so the phone sends dictations through `rem.tidy`. */
                           val voiceTidy: Boolean = false,
                           /** The desktop answers `source_control.list` and `source_control.read`. */
                           val sourceControl: Boolean = false,
                           /** The desktop also answers `source_control.history`, `.commit` and `.diff`. */
                           val sourceControlHistory: Boolean = false,
                           /** The desktop offers any Rem chat attachment with `rem.attachment`. */
                           val attachmentFiles: Boolean = false,
                           /** The desktop answers `developer.log` with its application log. */
                           val developerLog: Boolean = false,
                           /** The desktop answers `organizations.note` and takes `.knowledge`, `.worker` and the board's moves. */
                           val organizationsV2: Boolean = false,
                           /** The desktop answers `files.list` and `files.text` and takes `files.open`. */
                           val fileExplorer: Boolean = false,
                           /** The desktop answers `browser.tabs` and takes `browser.open`. */
                           val browser: Boolean = false,
                           /** The desktop answers `kanban.board` and `selfwork.read` and takes their actions. */
                           val kanban: Boolean = false,
                           /** The board also lists its people, and the desktop takes `kanban.participant`. */
                           val kanbanParticipants: Boolean = false,
                           /** The desktop answers `kanban.timeline` and takes a ticket's `comment` action. */
                           val kanbanComments: Boolean = false,
                           /** Organization reads carry the work schedule, and the desktop takes `organizations.schedule`. */
                           val organizationSchedule: Boolean = false,
                           /** `rem.notices` takes `include` and `mark`, for alerts about work the phone didn't start. */
                           val remNoticesFilter: Boolean = false,
                           /** Set only while the desktop's wake setting is on. */
                           val wakeMac: String? = null)
/**
 * Rem's reply text added since the phone's last `rem.draft` read; [reset] means [text] is the whole draft. [attempt] numbers
 * Rem's model calls within a reply, so a later read can say which one the phone's text came from; an older desktop omits it.
 */
data class RemDraftDelta(val replying: Boolean, val turnLabel: String, val start: Int, val text: String, val length: Int, val reset: Boolean,
                         val attempt: Long? = null)
/**
 * A chat message's link to a task run and its work thread. It outlives the task's place in the task list.
 * The desktop clears the availability flags once it has removed the task or thread; an older desktop omits them.
 */
data class RemWork(val taskId: String, val runId: String, val threadId: String, val title: String,
                   val taskAvailable: Boolean = true, val threadAvailable: Boolean = true,
                   /** Board tickets the run created, worked on or changed, as they were when the message was written. */
                   val tickets: List<RemTicketRef> = emptyList(),
                   /** "ticket", "triage" or "research" for work Rem started on its own; blank for requested work. */
                   val selfWork: String = "")
/** A file on a Rem chat message: one the phone attached, or one the desktop offered to this phone. */
data class RemFile(val id: String, val name: String, val size: Long)
/**
 * A Rem chat message. [details], [suggestions], [meta] and [result] add to [body], which stays complete on its own:
 * the result card's report links are also in the body, so an older phone or desktop loses nothing.
 * [phoneActions] are cards the user may tap to open another app; the desktop sends them only to the phone that asked.
 */
data class RemMessage(val id: String, val role: String, val body: String, val createdAt: String, val work: RemWork?, val state: String,
                      val attachments: List<RemFile> = emptyList(), val files: List<RemFile> = emptyList(),
                      val details: String = "", val suggestions: List<String> = emptyList(), val meta: RemMeta? = null,
                      val result: RemResult? = null, val kind: String = "", val memory: RemMemoryNote? = null,
                      val phoneActions: PhoneActionList = PhoneActionList.EMPTY,
                      /** The earlier message this one replies to, and what the desktop shows of it. */
                      val refersTo: String = "", val referenced: RemReference? = null,
                      /** Full paths of existing desktop files written in the body or Details, exactly as written. */
                      val fileMentions: List<String> = emptyList(),
                      /** Set on the card of work Rem started on its own; cards of one pass share its batch. */
                      val selfWork: RemSelfWork? = null,
                      /** On a request the user queued behind other tasks: the titles of those tasks. Rem got it once they succeeded. */
                      val queuedAfter: List<String> = emptyList())
/**
 * The message a reply refers to: who wrote it, when, and its opening words. [unavailable] means it is no longer in
 * Rem's chat, such as after the history was cleared; the reply itself stays.
 */
data class RemReference(val id: String, val role: String = "", val body: String = "", val createdAt: String = "",
                        val kind: String = "", val title: String = "", val files: Int = 0, val unavailable: Boolean = false)
/** One memory a reply saved, changed or forgot. [removed] means the user later forgot it, so its words are gone. */
data class RemMemoryEntry(val id: String, val text: String, val removed: Boolean)
/** What one Rem reply did to its memory, with how many secret-looking items it skipped. The desktop can undo it once. */
data class RemMemoryNote(val saved: List<RemMemoryEntry>, val updated: List<RemMemoryEntry>, val forgotten: List<RemMemoryEntry>,
                         val refused: Int, val undone: Boolean) {
    val undoable get() = !undone && (saved.isNotEmpty() || updated.isNotEmpty() || forgotten.isNotEmpty())
}
/** A fact or preference Rem remembers on the desktop. */
data class RemMemory(val id: String, val text: String, val createdAt: String, val editedAt: String, val addedByUser: Boolean)
/** [mode] is "on" (use and save), "paused" (use, save nothing new) or "off" (neither). */
data class RemMemories(val mode: String, val memories: List<RemMemory>)
/** The chat model and how long Rem took to reply. */
data class RemMeta(val provider: String, val model: String, val seconds: Double?)
data class RemChecks(val passed: Int, val failed: Int, val total: Int)
/** A saved report. [href] is the path exactly as the message's own link names it, which is what `files.link` accepts. */
data class RemArtifact(val name: String, val title: String, val kind: String, val size: Long, val href: String)
/** A web page the worker cited, read or found. */
data class RemSource(val url: String, val title: String, val kind: String)
data class RemResult(val title: String, val type: String, val state: String, val checks: RemChecks, val artifacts: List<RemArtifact>,
                     val sources: List<RemSource>, val threadId: String)
data class RemRun(val id: String, val state: String, val createdAt: String, val finishedAt: String, val summary: String,
                  val needsUser: Boolean, val acknowledged: Boolean, val threadId: String, val verificationThreadId: String,
                  val phaseLabel: String = "", val waitingReason: String = "", val checks: RemChecks = RemChecks(0, 0, 0),
                  val reviewRetryable: Boolean = false,
                  /** Null when the desktop sent counts only: an older desktop, or a task list rather than one task. */
                  val details: List<RemCheck>? = null,
                  /** Board tickets this run created, worked on or changed. */
                  val tickets: List<RemTicketRef> = emptyList(),
                  /** "plan", "work" or "check" from the desktop; blank from an older one, where [state] decides. */
                  val step: String = "") {
    val active get() = state in setOf("queued", "executing", "verifying", "repairing", "cancelling")
    val attention get() = state in setOf("blocked", "failed", "outcome_unknown") && !acknowledged
}
data class RemSchedule(val kind: String, val at: String, val localTime: String, val weekdays: List<String>, val everyMinutes: Int)
data class RemTask(val id: String, val title: String, val type: String, val state: String, val revision: Long, val schedule: RemSchedule,
                   val nextAt: String, val project: String, val activeRun: RemRun?, val lastRun: RemRun?, val prompt: String = "",
                   val provider: String = "", val model: String = "", val criteria: List<String> = emptyList(), val runs: List<RemRun> = emptyList(),
                   val ranAt: String = "", val listed: Boolean? = null, val cleanupAt: String = "",
                   /** The board tickets this task's runs name, by key, as they are now; only on a single task's read. */
                   val ticketIndex: Map<String, RemTicket> = emptyMap(),
                   /** Set while the task waits for other tasks to succeed before its first run. */
                   val dependency: RemDependency? = null)
/** What a waiting task waits for: [state] is `waiting`, `held` (one of them didn't succeed) or `met`, with [reason] in words. */
data class RemDependency(val state: String, val reason: String, val prerequisites: List<RemPrerequisite>) {
    val held get() = state == "held"
}
/** One task a waiting task needs. [retryable] means Run now on it would try it again. */
data class RemPrerequisite(val taskId: String, val title: String, val state: String, val reason: String, val runId: String,
                           val runState: String, val retryable: Boolean)

/**
 * The phone's Rem task list: whatever ran most recently first, without one-time tasks that already finished.
 * The desktop keeps those for the chat's links and removes them itself later; the phone deletes nothing.
 */
object RemTaskList {
    private val ENDED = setOf("succeeded", "cancelled", "failed", "blocked", "outcome_unknown")

    /**
     * A one-time task whose run ended and needs nothing more from the user. A failed run stays listed until dismissed.
     * The desktop's `listed` decides when present; the same rule covers an older desktop that sends every task.
     */
    fun finishedOnce(task: RemTask): Boolean {
        task.listed?.let { return !it }
        val last = task.lastRun ?: return false
        return task.schedule.kind == "once" && task.activeRun?.active != true && task.nextAt.isBlank() && last.finishedAt.isNotBlank() &&
            last.state in ENDED && !last.attention
    }

    /**
     * Running tasks first, since they are running now; then by the desktop's `ran_at`. Without it (an older desktop)
     * by when the last run finished, and tasks that never ran keep the desktop's order at the end.
     */
    fun shown(tasks: List<RemTask>): List<RemTask> = tasks.filterNot(::finishedOnce)
        .sortedWith(compareByDescending<RemTask> { it.activeRun?.active == true }.thenByDescending(::ranAt))

    private fun ranAt(task: RemTask): java.time.Instant? {
        instant(task.ranAt)?.let { return it }
        val run = task.activeRun?.takeIf { it.active } ?: task.lastRun ?: return null
        return instant(if (run.active) run.createdAt else run.finishedAt.ifBlank { run.createdAt })
    }

    private fun instant(value: String) = runCatching { java.time.OffsetDateTime.parse(value).toInstant() }.getOrNull()
}
/** [turnLabel] says what Rem is doing while it replies; [draftReply] is its reply as it is being written. */
data class RemHome(val turnState: String, val provider: String, val model: String, val earlier: Int, val messages: List<RemMessage>, val tasks: List<RemTask>,
                   val filesThread: String? = null, val media: Boolean = false, val fileLinks: Boolean = false,
                   val turnLabel: String = "", val draftReply: String = "", val richResults: Boolean = false,
                   val draftDeltas: Boolean = false, val reviewRetry: Boolean = false, val memories: Boolean = false,
                   /** The desktop accepts screen text with a Rem message, for that reply only. */
                   val screenContext: Boolean = false,
                   /** The desktop accepts `refers_to` with a Rem message and shows each message's [RemMessage.referenced]. */
                   val replies: Boolean = false,
                   /** Voice tidying is on in desktop Settings. */
                   val voiceTidy: Boolean = false,
                   /** The desktop offers any Rem chat attachment with `rem.attachment`. */
                   val attachmentFiles: Boolean = false,
                   /** Board tickets named in these messages and on these tasks, by key. Only these keys show as ticket pills. */
                   val tickets: Map<String, RemTicket> = emptyMap(),
                   /** The desktop searches its whole saved chat (`rem.search`) and pages back through it (`rem.history`). */
                   val search: Boolean = false)
/** One merged tool or thinking step from a worker's thoughts, as the desktop's thought list shows it. */
data class RemThreadStep(val kind: String, val title: String, val detail: String, val body: String, val command: String, val status: String)
/** A work thread entry. Kind `thoughts` is one run of steps: [count] in all, the newest of them in [steps]. */
data class RemThreadMessage(val id: String, val role: String, val body: String, val kind: String,
                            val count: Int = 0, val steps: List<RemThreadStep> = emptyList(),
                            /** Who wrote an instruction: `rem` or `user`; empty from an older desktop. */
                            val sender: String = "")
data class RemThread(val id: String, val title: String, val running: Boolean, val earlier: Int, val messages: List<RemThreadMessage>)
data class OrganizationSummary(val id: String, val name: String, val state: String, val employees: Int, val openTasks: Int, val projects: List<String>)
data class OrganizationEmployee(val id: String, val name: String, val title: String, val provider: String, val model: String, val paused: Boolean,
                                val reportsTo: String = "")
data class OrganizationComment(val actor: String, val at: String, val body: String, val decision: String)
data class OrganizationTask(val id: String, val title: String, val description: String, val status: String, val priority: String,
                            val assignee: String, val revision: Long, val running: Boolean, val needsApproval: Boolean, val needsReview: Boolean,
                            val attention: Boolean, val blockReason: String, val evidence: String, val dueAt: String, val comments: List<OrganizationComment>,
                            /** Key, epic, evidence and notes; null from an older desktop. */
                            val extras: TicketExtras? = null)
/** [plan] holds the five-section view's parts; an older desktop sends tasks and employees only. */
data class Organization(val id: String, val name: String, val description: String, val state: String, val projects: List<String>,
                        val employees: List<OrganizationEmployee>, val tasks: List<OrganizationTask>, val plan: OrgPlan? = null,
                        val pauseReason: String = "", val pendingRequests: Int = 0)
data class DesktopSetting(val key: String, val group: String, val label: String, val kind: String, val options: List<Pair<String, String>>,
                          val min: Double, val max: Double, val step: Double)
data class DesktopSettings(val available: Boolean, val pending: Int, val values: Map<String, Any?>, val items: List<DesktopSetting>)
data class UsageWindow(val label: String, val usedPercent: Double?, val resetsAt: String)
data class UsageAccount(val label: String, val status: String, val reason: String, val windows: List<UsageWindow>)
data class UsageProvider(val provider: String, val calls: Int, val inputTokens: Long?, val outputTokens: Long?)
data class DesktopUsage(val days: Int, val observedAt: String, val accounts: List<UsageAccount>, val calls: Int, val inputTokens: Long?,
                        val outputTokens: Long?, val providers: List<UsageProvider>)
