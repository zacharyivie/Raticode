package dev.raticode.mobile.live

import org.json.JSONObject

/**
 * The phone's model of the desktop's five-section Organizations view: Organization, Projects, Issues, Workers and
 * Knowledge. The rules follow the desktop's organizationModel.js, so a ticket, epic or milestone reads the same on
 * both. The desktop checks every action again; these rules only decide what the phone offers.
 */
data class OrgMilestone(val id: String, val title: String, val short: String, val dueAt: String, val projectId: String,
                        val parentId: String, val status: String)
data class OrgEpic(val id: String, val title: String, val milestoneId: String, val projectId: String, val owner: String,
                   val description: String, val status: String)
data class OrgProject(val id: String, val name: String, val owner: String, val color: String, val description: String,
                      val folder: String, val checks: Int)
/** [ticketAccess] null means every project. */
data class OrgWorker(val id: String, val persona: String, val responsibilities: List<String>, val role: String, val effort: String,
                     val permission: String, val fullAccess: Boolean, val heartbeatSeconds: Int,
                     val ticketAccess: List<Pair<String, List<String>>>?, val stalled: String)
data class OrgNote(val id: String, val kind: String, val title: String, val summary: String, val topic: String, val projectId: String,
                   val links: List<String>, val author: String, val updatedAt: String, val secondBrain: Boolean, val changed: Boolean)
data class OrgNoteLink(val from: String, val to: String, val state: String)
data class OrgRequest(val summary: String, val at: String)
data class OrgHours(val mode: String, val open: Boolean, val opensAt: String, val closesAt: String)
data class OrgCommit(val sha: String, val at: String, val author: String, val subject: String)
data class OrgRepository(val exists: Boolean, val files: Int, val branches: Int, val commits: List<OrgCommit>)
data class OrgUsage(val runs: Int, val tokens: Long)
data class OrgClaim(val text: String, val state: String, val kind: String)
/** What a ticket adds on a desktop with the five-section view. */
data class TicketExtras(val key: String, val assigneeId: String, val epicId: String, val projectId: String, val doneWhen: String,
                        val approvalRequired: Boolean, val notes: List<String>, val readNotes: List<String>, val startHint: String,
                        val evidence: Map<String, Int>, val claims: List<OrgClaim>, val createdAt: String)
/** The five-section view's parts. Null on an older desktop, which sends tasks and employees only. */
data class OrgPlan(val mission: String, val ticketPrefix: String, val milestones: List<OrgMilestone>, val epics: List<OrgEpic>,
                   val projects: List<OrgProject>, val workers: Map<String, OrgWorker>, val notes: List<OrgNote>, val noteLinks: List<OrgNoteLink>,
                   val requests: List<OrgRequest>, val hours: OrgHours, val repository: OrgRepository?, val archived: Boolean, val revision: Long,
                   val usage: OrgUsage, val schedule: OrgSchedule? = null)
/** One note with its body, read on its own. */
data class OrgNoteDetail(val id: String, val kind: String, val title: String, val summary: String, val topic: String, val projectId: String,
                         val body: String, val truncated: Boolean, val links: List<String>, val author: String, val createdAt: String,
                         val updatedAt: String, val secondBrain: Boolean, val sourceMissing: Boolean, val connections: List<OrgConnection>)
data class OrgConnection(val kind: String, val from: String, val to: String, val quote: String, val state: String, val actor: String, val at: String)

object OrganizationModel {
    const val NOTE = "organizations.note"
    const val KNOWLEDGE = "organizations.knowledge"
    const val WORKER = "organizations.worker"
    const val UPDATE_DESKTOP = "Update Raticode on your computer to use this here."
    val COLUMNS = listOf("backlog", "todo", "running", "review", "done")
    val COLUMN_LABELS = mapOf("backlog" to "Backlog", "todo" to "To do", "running" to "In progress", "review" to "Review", "done" to "Done")
    val KINDS = listOf("decision" to "Decision", "note" to "Note", "report" to "Report", "spec" to "Spec", "finding" to "Finding")
    val EVIDENCE_LABELS = mapOf("ok" to "Verified", "warn" to "Source changed", "bad" to "Failed", "none" to "No evidence",
        "queued" to "Ready to check", "running" to "Checking", "unverified" to "Not rerun")
    val PROJECT_COLORS = listOf("#215879", "#17767b", "#6a4bb0", "#8f5d16", "#2f6fb3", "#a13c36", "#5f6f2a", "#4d5d6a")
    val WORKER_COLORS = listOf("#6a4bb0", "#215879", "#17767b", "#2f6fb3", "#a13c36", "#8f5d16", "#5f6f2a", "#7a4a7f", "#3b6e5a", "#4d5d6a")

    fun hashColor(id: String, palette: List<String> = WORKER_COLORS): String {
        var hash = 0L
        for (char in id) hash = (hash * 31 + char.code) and 0xffffffffL
        return palette[(hash % palette.size).toInt()]
    }

    fun initials(name: String) = name.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString("") { it.take(1) }.take(2).uppercase().ifEmpty { "?" }

    /** Task status to board column. Blocked work stays in To do, flagged; cancelled work leaves the board. */
    fun columnOf(status: String) = when (status) {
        "backlog" -> "backlog"; "todo", "blocked" -> "todo"; "in_progress" -> "running"; "in_review" -> "review"; "done" -> "done"; else -> null
    }

    fun key(task: OrganizationTask) = task.extras?.key?.ifBlank { null } ?: "#?"

    /** What a ticket's card warns about, as the desktop's flag line does. */
    fun flag(task: OrganizationTask): String = when {
        task.status == "blocked" -> task.blockReason.ifBlank { "Blocked, with no reason recorded. Check its conversation, then reopen it." }
        task.needsApproval && task.status != "done" -> "Needs your approval before it runs"
        else -> ""
    }

    fun projectColor(plan: OrgPlan, projectId: String): String {
        val index = plan.projects.indexOfFirst { it.id == projectId }
        if (index < 0) return PROJECT_COLORS.last()
        return plan.projects[index].color.takeIf { it.matches(Regex("#[0-9a-fA-F]{6}")) } ?: PROJECT_COLORS[index % PROJECT_COLORS.size]
    }

    fun ticketsOfEpic(org: Organization, epicId: String) = org.tasks.filter { it.extras?.epicId == epicId && columnOf(it.status) != null }

    private fun average(values: List<Double>) = if (values.isEmpty()) 0.0 else values.sum() / values.size

    /** Progress counts only accepted (done) tickets, and averages upward through epics and milestones. */
    fun epicProgress(org: Organization, epicId: String): Double {
        val tickets = ticketsOfEpic(org, epicId)
        return if (tickets.isEmpty()) 0.0 else tickets.count { it.status == "done" }.toDouble() / tickets.size
    }
    fun milestoneProgress(org: Organization, plan: OrgPlan, milestoneId: String) =
        average(plan.epics.filter { it.milestoneId == milestoneId }.map { epicProgress(org, it.id) })
    fun orgMilestoneProgress(org: Organization, plan: OrgPlan, id: String) =
        average(plan.milestones.filter { it.parentId == id && it.projectId.isNotBlank() }.map { milestoneProgress(org, plan, it.id) })
    fun projectProgress(org: Organization, plan: OrgPlan, projectId: String): Double {
        val milestones = plan.milestones.filter { it.projectId == projectId }
        if (milestones.isNotEmpty()) return average(milestones.map { milestoneProgress(org, plan, it.id) })
        val tickets = org.tasks.filter { it.extras?.projectId == projectId && columnOf(it.status) != null }
        return if (tickets.isEmpty()) 0.0 else tickets.count { it.status == "done" }.toDouble() / tickets.size
    }
    fun openTickets(org: Organization, projectId: String? = null) = org.tasks.filter {
        columnOf(it.status).let { c -> c != null && c != "done" } && (projectId == null || it.extras?.projectId == projectId)
    }

    /** Planned, active or done, for the epic board and the project page. */
    fun epicState(org: Organization, epic: OrgEpic): String {
        val tickets = ticketsOfEpic(org, epic.id)
        val progress = epicProgress(org, epic.id)
        if (epic.status == "done" || (tickets.isNotEmpty() && progress == 1.0)) return "done"
        return if (tickets.any { columnOf(it.status) !in setOf("backlog", "todo") } || progress > 0) "active" else "planned"
    }

    /** "working", "needs", "paused" or "idle" with its label. */
    fun workerStatus(org: Organization, employee: OrganizationEmployee): Pair<String, String> {
        if (employee.paused) return "paused" to "Held"
        val mine = org.tasks.filter { it.extras?.assigneeId == employee.id }
        if (mine.any { columnOf(it.status) == "running" || it.running }) return "working" to "Working"
        if (mine.any { columnOf(it.status) == "review" }) return "needs" to "To review"
        return "idle" to "Idle"
    }

    /** The worker's current ticket, if one is running. */
    fun currentTicket(org: Organization, employeeId: String) =
        org.tasks.firstOrNull { it.extras?.assigneeId == employeeId && (it.running || it.status == "in_progress") }

    /** One row of the reporting tree, depth first from the people who report to you. */
    data class TreeRow(val employee: OrganizationEmployee, val depth: Int, val reports: Int)

    fun reportingTree(employees: List<OrganizationEmployee>): List<TreeRow> {
        val ids = employees.map { it.id }.toSet()
        val children = employees.groupBy { it.reportsTo.takeIf { manager -> manager in ids && manager != it.id }.orEmpty() }
        val rows = mutableListOf<TreeRow>()
        val seen = HashSet<String>()
        fun walk(manager: String, depth: Int) {
            for (employee in children[manager].orEmpty()) {
                // A loop in reportsTo must not hang the screen; each worker appears once.
                if (!seen.add(employee.id)) continue
                rows += TreeRow(employee, depth, children[employee.id].orEmpty().size)
                walk(employee.id, depth + 1)
            }
        }
        walk("", 0)
        // Workers caught in a reporting loop still appear, at the top.
        employees.filter { it.id !in seen }.forEach { if (seen.add(it.id)) { rows += TreeRow(it, 0, children[it.id].orEmpty().size); walk(it.id, 1) } }
        return rows
    }

    sealed interface NeedsYou {
        data class Request(val request: OrgRequest) : NeedsYou
        data class Review(val task: OrganizationTask) : NeedsYou
        data class Flag(val task: OrganizationTask, val text: String) : NeedsYou
        data class Stalled(val employee: OrganizationEmployee, val reason: String) : NeedsYou
    }

    /** What waits on you: Rem's requests, work to review, flagged work and stalled check-ins, in the desktop's order. */
    fun needsYou(org: Organization, plan: OrgPlan?): List<NeedsYou> {
        val items = mutableListOf<NeedsYou>()
        plan?.requests?.forEach { items += NeedsYou.Request(it) }
        val board = org.tasks.filter { columnOf(it.status) != null }
        board.filter { columnOf(it.status) == "review" }.forEach { items += NeedsYou.Review(it) }
        board.filter { columnOf(it.status) !in setOf("review", "done") }.forEach { t -> flag(t).takeIf { it.isNotBlank() }?.let { items += NeedsYou.Flag(t, it) } }
        org.employees.forEach { e -> plan?.workers?.get(e.id)?.stalled?.takeIf { it.isNotBlank() }?.let { items += NeedsYou.Stalled(e, it) } }
        return items
    }

    /** Where a ticket may move on the phone's board, and why not. Done always goes through review. */
    sealed interface Move {
        data class Allowed(val column: String, val pull: Boolean = false, val reopen: Boolean = false) : Move
        data class Review(val column: String = "done") : Move
        data class Refused(val reason: String) : Move
    }

    fun move(task: OrganizationTask, column: String): Move? {
        val from = columnOf(task.status) ?: return Move.Refused("Reopen it first.")
        if (from == column) return null
        if (column == "running") {
            if (task.status == "blocked") return Move.Refused("This ticket is blocked. Open it and reopen it first.")
            if (from in setOf("backlog", "todo")) return Move.Allowed(column, pull = true)
            return Move.Refused(if (from == "review") "Request changes to send it back for another run." else "Reopen it first.")
        }
        if (from == "running" || task.running) return Move.Refused("This ticket is running. Pause the organization or wait for the run to finish.")
        if (column == "review") return Move.Refused("Work moves to Review when a run finishes.")
        if (column == "done") return if (from == "review") Move.Review() else Move.Refused("Only reviewed work moves to Done. Review it first.")
        // Sending work back goes through Request changes, which records the review and clears its claims.
        if (from == "review") return Move.Refused("Open it to request changes, so the review is recorded.")
        if (task.status in setOf("done", "blocked")) return Move.Allowed(column, reopen = true)
        return Move.Allowed(column)
    }

    /** Why a To do ticket hasn't started, in the desktop's words. */
    fun startHint(task: OrganizationTask, org: Organization): String {
        val reason = task.extras?.startHint.orEmpty()
        val worker = org.employees.firstOrNull { it.id == task.extras?.assigneeId }?.name ?: "its worker"
        return when {
            reason.isBlank() -> ""
            reason == "Ready" -> if (org.state == "running") "Starts next: $worker picks it up within a few seconds." else "Ready. It starts when you start the organization."
            reason == "Organization is paused" -> "It starts when you start the organization."
            reason == "Assign an employee" -> "No worker is assigned, so it won't run."
            reason == "Employee is held" -> "$worker is held. Release the hold in their details to let it run."
            reason == "Waiting for plan approval" -> "Waiting for you to approve its plan."
            reason.startsWith("Next up: ") -> "Next up. ${reason.removePrefix("Next up: ")}; this one starts right after."
            reason.startsWith("Queued: ") -> "${reason.removePrefix("Queued: ")}; this one starts after."
            else -> if (reason.endsWith(".")) reason else "$reason."
        }
    }

    /**
     * Whether a To do ticket's start hint waits on you (`you`), on something that clears itself (`wait`) or will
     * run (`run`), as the desktop's startHint tones it. Only the tone; [startHint] keeps the phone's wording.
     */
    fun startTone(task: OrganizationTask, org: Organization): String {
        val reason = task.extras?.startHint.orEmpty()
        return when {
            reason.isBlank() -> ""
            reason == "Ready" -> if (org.state == "running") "run" else "you"
            reason.startsWith("Next up: ") || reason.startsWith("Queued: ") || reason.startsWith("Scheduled for ") || reason.startsWith("Off hours until ") -> "wait"
            reason in setOf("Organization is paused", "Organization is archived", "Assign an employee", "Employee is held", "Waiting for plan approval") -> "you"
            reason.startsWith("Off hours") || reason.startsWith("Monthly ") || reason.startsWith("Waiting for monthly run capacity") -> "you"
            Regex("cancelled|no longer exists|Reassign|Move the ticket|Reopen|reopen|Raise|unavailable|outside|Assign").containsMatchIn(reason) -> "you"
            else -> "wait"
        }
    }

    /** A comment's author in words, as the desktop's actorName: You, Raticode, Rem or the worker's name. */
    fun actorName(actor: String, org: Organization): String = when {
        actor == "user" -> "You"
        actor == "system" -> "Raticode"
        actor.startsWith("Rem") -> "Rem"
        actor.startsWith("employee:") -> actor.removePrefix("employee:").let { id -> org.employees.firstOrNull { it.id == id }?.name ?: id }
        else -> actor
    }

    /** Evidence counts as the risk the desktop warns about before accepting. */
    fun reviewCaution(task: OrganizationTask): String {
        val e = task.extras?.evidence.orEmpty()
        return listOfNotNull(e["bad"]?.takeIf { it > 0 }?.let { "$it failed" }, e["warn"]?.takeIf { it > 0 }?.let { "$it source changed" },
            e["none"]?.takeIf { it > 0 }?.let { "$it without verified evidence" }, e["pending"]?.takeIf { it > 0 }?.let { "$it not checked yet" })
            .joinToString(", ")
    }

    /** Notes linked to [noteId] by a written or a checked link, and tickets that use it. */
    data class Neighbors(val notes: List<OrgNote>, val tickets: List<OrganizationTask>)

    fun neighbors(org: Organization, plan: OrgPlan, noteId: String): Neighbors {
        val byId = plan.notes.associateBy { it.id }
        val linked = LinkedHashSet<String>()
        plan.notes.firstOrNull { it.id == noteId }?.links?.forEach { linked += it }
        plan.notes.filter { noteId in it.links }.forEach { linked += it.id }
        plan.noteLinks.forEach { link ->
            if (link.from == "kb:$noteId" && link.to.startsWith("kb:")) linked += link.to.removePrefix("kb:")
            if (link.to == "kb:$noteId" && link.from.startsWith("kb:")) linked += link.from.removePrefix("kb:")
        }
        linked -= noteId
        val tickets = org.tasks.filter { t -> t.extras?.let { noteId in it.notes || noteId in it.readNotes } == true && columnOf(t.status) != null }
        return Neighbors(linked.mapNotNull(byId::get), tickets)
    }

    /** Topics in order of size, each with its notes, newest first: the phone's list in place of the desktop's graph. */
    fun topics(notes: List<OrgNote>): List<Pair<String, List<OrgNote>>> =
        notes.groupBy { it.topic.ifBlank { "General" } }.entries.sortedWith(compareByDescending<Map.Entry<String, List<OrgNote>>> { it.value.size }.thenBy { it.key })
            .map { it.key to it.value }

    /** How many notes link to or from each note, for the size of its dot. */
    fun degree(plan: OrgPlan): Map<String, Int> {
        val counts = HashMap<String, Int>()
        fun bump(id: String) { counts[id] = (counts[id] ?: 0) + 1 }
        val ids = plan.notes.map { it.id }.toSet()
        plan.notes.forEach { note -> note.links.filter { it in ids && it != note.id }.forEach { bump(note.id); bump(it) } }
        plan.noteLinks.filter { it.from.startsWith("kb:") && it.to.startsWith("kb:") }.forEach { bump(it.from.removePrefix("kb:")); bump(it.to.removePrefix("kb:")) }
        return counts
    }

    // Parsing -------------------------------------------------------------------------------------------------------

    fun parseExtras(t: JSONObject): TicketExtras? {
        if (!t.has("key")) return null
        val evidence = t.optJSONObject("evidence_counts")
        return TicketExtras(t.optString("key").take(40), t.optString("assignee_id"), t.optString("epic_id"), t.optString("project_id"),
            t.optString("done_when"), t.optBoolean("approval_required"), t.strings("notes"), t.strings("read_notes"), t.optString("start_hint"),
            listOf("ok", "warn", "bad", "none", "pending").associateWith { evidence?.optInt(it) ?: 0 },
            t.objects("claims").map { OrgClaim(it.optString("text"), it.optString("state", "none"), it.optString("kind")) }, t.optString("created_at"))
    }

    fun parsePlan(json: JSONObject): OrgPlan? {
        if (!json.optBoolean("v2")) return null
        val hours = json.optJSONObject("hours")
        val repository = json.optJSONObject("repository")
        val usage = json.optJSONObject("usage")
        return OrgPlan(json.optString("mission"), json.optString("ticket_prefix"),
            json.objects("milestones").map { OrgMilestone(it.optString("milestone_id"), it.optString("title"), it.optString("short"), it.optString("due_at"),
                it.optString("project_id"), it.optString("parent_id"), it.optString("status")) },
            json.objects("epics").map { OrgEpic(it.optString("epic_id"), it.optString("title"), it.optString("milestone_id"), it.optString("project_id"),
                it.optString("owner"), it.optString("description"), it.optString("status")) },
            json.objects("initiatives").map { OrgProject(it.optString("project_id"), it.optString("name"), it.optString("owner"), it.optString("color"),
                it.optString("description"), it.optString("folder"), it.optInt("checks")) },
            json.objects("workers").associate { w -> w.optString("employee_id") to OrgWorker(w.optString("employee_id"), w.optString("persona"),
                w.strings("responsibilities"), w.optString("role"), w.optString("effort"), w.optString("permission"), w.optBoolean("full_access"),
                w.optInt("heartbeat_seconds"), w.optJSONArray("ticket_access")?.let { _ ->
                    w.objects("ticket_access").map { it.optString("project_id") to it.strings("allowed") } }, w.optString("stalled")) },
            json.objects("notes").map { OrgNote(it.optString("note_id"), it.optString("kind", "note"), it.optString("title"), it.optString("summary"),
                it.optString("topic", "General"), it.optString("project_id"), it.strings("links"), it.optString("author"), it.optString("updated_at"),
                it.optBoolean("second_brain"), it.optBoolean("changed")) },
            json.objects("note_links").map { OrgNoteLink(it.optString("from"), it.optString("to"), it.optString("state", "ok")) },
            json.objects("requests").map { OrgRequest(it.optString("summary"), it.optString("at")) },
            OrgHours(hours?.optString("mode").orEmpty(), hours?.optBoolean("open", true) ?: true, hours?.optString("opens_at").orEmpty(),
                hours?.optString("closes_at").orEmpty()),
            repository?.let { r -> OrgRepository(r.optBoolean("exists"), r.optInt("files"), r.optInt("branches"),
                r.objects("commits").map { OrgCommit(it.optString("sha"), it.optString("at"), it.optString("author"), it.optString("subject")) }) },
            json.optBoolean("archived"), json.optLong("revision"), OrgUsage(usage?.optInt("runs") ?: 0, usage?.optLong("tokens") ?: 0),
            // An older desktop has no work schedule.
            WorkSchedule.parse(json.optJSONObject("schedule")))
    }

    fun parseNote(json: JSONObject) = OrgNoteDetail(json.optString("note_id"), json.optString("kind", "note"), json.optString("title"),
        json.optString("summary"), json.optString("topic", "General"), json.optString("project_id"), json.optString("body"),
        json.optBoolean("truncated"), json.strings("links"), json.optString("author"), json.optString("created_at"), json.optString("updated_at"),
        json.optBoolean("second_brain"), json.optBoolean("source_missing"),
        json.objects("connections").map { OrgConnection(it.optString("kind"), it.optString("from"), it.optString("to"), it.optString("quote"),
            it.optString("state", "ok"), it.optString("actor"), it.optString("at")) })

    private fun JSONObject.objects(key: String): List<JSONObject> = optJSONArray(key)?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
    private fun JSONObject.strings(key: String): List<String> = optJSONArray(key)?.let { a -> (0 until a.length()).map { a.optString(it) } }.orEmpty()
}
