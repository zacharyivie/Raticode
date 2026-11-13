package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/**
 * The phone's model of the desktop's global board and Rem's self-work. Tickets come from every organization and from Rem.
 * The desktop owns the tickets, self-work's schedule and its limits and checks every action again; these rules only decide
 * what the phone offers. Nothing here is saved on the phone.
 */
object Kanban {
    const val BOARD = "kanban.board"
    const val TICKET = "kanban.ticket"
    const val SELF_WORK = "selfwork.read"
    const val SETTINGS = "selfwork.settings"
    const val SEEN = "selfwork.report.seen"
    const val PARTICIPANT = "kanban.participant"
    /** One ticket and its history: status changes with their reasons, and comments. */
    const val TIMELINE = "kanban.timeline"
    val READS = setOf(BOARD, SELF_WORK, TIMELINE)
    val ACTIONS = setOf(TICKET, SETTINGS, SEEN, PARTICIPANT)
    const val UPDATE_DESKTOP = "Update Raticode on your computer to use the global board and Rem's self-work here."
    const val UPDATE_PARTICIPANTS = "Update Raticode on your computer to see the board's people and their projects here."
    const val UPDATE_COMMENTS = "Update Raticode on your computer to see each ticket's history and comments here."

    const val SCOPE_REM = "rem"
    const val SCOPE_ORG = "organization"
    /** The assignee ID that means Rem itself. */
    const val REM = "rem"
    /** The scope filter that shows every ticket. */
    const val ALL = ""
    /** The assignee value for tickets with no one assigned. */
    const val UNASSIGNED = ""
    /** The assignee filter that shows every ticket. */
    const val ANY = "*"
    /** An assignment choice that hands a Rem ticket to an organization, optionally with one of its workers after [HANDOVER_WORKER]. */
    const val HANDOVER = "to:"
    const val HANDOVER_WORKER = "\u0000"

    /** The global board has its own Blocked column, as on the desktop: work that is waiting, stopped or failed. */
    val COLUMNS = listOf("backlog", "todo", "running", "review", "blocked", "done")
    val COLUMN_LABELS = OrganizationModel.COLUMN_LABELS + ("blocked" to "Blocked")
    val STATUS_LABELS = mapOf("backlog" to "Backlog", "todo" to "To do", "in_progress" to "In progress", "in_review" to "In review",
        "blocked" to "Blocked", "done" to "Done", "cancelled" to "Cancelled")
    val PRIORITIES = listOf("low" to "Low", "medium" to "Medium", "high" to "High", "critical" to "Critical")

    fun isOp(op: String) = op in READS || op in ACTIONS
    fun isComment(op: String, params: JSONObject) = op == TICKET && params.optString("action") == "comment"

    /** True for an action's short status line, which isn't a whole board or self-work view. */
    fun isShortAnswer(key: String, body: JSONObject) = when (key) {
        BOARD -> !body.has("tickets")
        SELF_WORK -> !body.has("settings")
        else -> false
    }

    // Board ---------------------------------------------------------------------------------------------------------

    /** Proposed tickets wait for the user's answer in their own list. Cancelled tickets leave the board. */
    fun onBoard(ticket: KanbanTicket) = !ticket.proposed && column(ticket) != null
    fun column(ticket: KanbanTicket) = if (ticket.status == "blocked") "blocked" else OrganizationModel.columnOf(ticket.status)

    fun proposed(board: KanbanBoard) = board.tickets.filter { it.proposed && it.status != "cancelled" }
        .sortedWith(compareByDescending<KanbanTicket> { it.createdAt }.thenBy { it.id })

    /** [scope] is [ALL], [SCOPE_REM] or an organization's ID. [assignee] is [ANY], [UNASSIGNED] or an assignee's ID. */
    fun matches(ticket: KanbanTicket, scope: String, assignee: String) =
        (scope == ALL || (scope == SCOPE_REM && ticket.isRem) || (!ticket.isRem && ticket.organizationId == scope)) &&
            (assignee == ANY || ticket.assigneeId == assignee)

    /** [project] is a project's name, or [ALL] for every project. */
    fun shown(board: KanbanBoard, scope: String, assignee: String, project: String = ALL): List<KanbanTicket> =
        board.tickets.filter { onBoard(it) && matches(it, scope, assignee) && (project == ALL || it.project == project) }

    /** The project filter's choices; empty when no ticket on the board names a project. */
    fun projectFilters(board: KanbanBoard): List<Pair<String, String>> {
        val names = board.tickets.filter(::onBoard).map { it.project }.filter { it.isNotBlank() }.distinct().sortedBy { it.lowercase() }
        return if (names.isEmpty()) emptyList() else listOf(ALL to "Any project") + names.map { it to it }
    }

    /** Running first, then flagged, then the highest priority, then the newest. */
    fun ordered(tickets: List<KanbanTicket>): List<KanbanTicket> = tickets.sortedWith(
        compareByDescending<KanbanTicket> { it.running }.thenBy { it.blockReason.isBlank() && it.status != "blocked" }
            .thenBy { rank(it.priority) }.thenByDescending { it.createdAt }.thenBy { it.id })

    private fun rank(priority: String) = when (priority) { "critical" -> 0; "high" -> 1; "medium" -> 2; "low" -> 3; else -> 2 }

    /** The assignee filter's choices for [scope]. [ANY] shows every ticket. */
    fun assigneeFilters(board: KanbanBoard, scope: String): List<Pair<String, String>> {
        val inScope = when (scope) {
            ALL -> board.organizations
            SCOPE_REM -> emptyList()
            else -> board.organizations.filter { it.id == scope }
        }
        val showRem = scope == ALL || scope == SCOPE_REM
        return listOf(ANY to "Anyone", UNASSIGNED to "Unassigned") + (if (showRem) listOf(REM to "Rem") else emptyList()) +
            inScope.flatMap { o -> o.employees.map { it.id to it.name.ifBlank { it.id } } }.distinctBy { it.first }
    }

    /** Who a new ticket in this place can be given to: Rem's tickets are Rem's; an organization has its workers. */
    fun assignees(board: KanbanBoard, scope: String, organizationId: String): List<Pair<String, String>> {
        if (scope == SCOPE_REM) return listOf(REM to "Rem")
        val employees = board.organizations.firstOrNull { it.id == organizationId }?.employees.orEmpty()
        return listOf(UNASSIGNED to "Unassigned") + employees.map { it.id to it.name.ifBlank { it.id } }
    }

    /** A Rem ticket can only be handed over while it isn't running and hasn't finished. */
    fun canHandOver(ticket: KanbanTicket) = ticket.isRem && !ticket.proposed && !ticket.running && ticket.status in setOf("backlog", "todo", "blocked")

    /**
     * Who an existing ticket can go to. An organization's ticket goes to its workers, never to Rem. A Rem ticket stays with
     * Rem or is handed to an organization, which the desktop does by creating the ticket there and closing Rem's.
     */
    fun assignChoices(board: KanbanBoard, ticket: KanbanTicket): List<Pair<String, String>> {
        if (!ticket.isRem) return assignees(board, SCOPE_ORG, ticket.organizationId)
        return listOf(REM to "Rem") + board.organizations.sortedBy { it.name.lowercase() }.flatMap { org ->
            listOf("$HANDOVER${org.id}" to "${org.name} (no worker yet)") +
                org.employees.map { "$HANDOVER${org.id}$HANDOVER_WORKER${it.id}" to "${org.name}: ${it.name}" }
        }
    }

    fun isHandOver(choice: String) = choice.startsWith(HANDOVER)

    /** Where a new ticket can go: Rem, then the organizations by name. */
    fun places(board: KanbanBoard): List<Pair<String, String>> =
        listOf(SCOPE_REM to "Rem") + board.organizations.sortedBy { it.name.lowercase() }.map { it.id to it.name.ifBlank { it.id } }

    /** The scope filter's choices. */
    fun scopes(board: KanbanBoard): List<Pair<String, String>> = listOf(ALL to "Everything") + places(board)

    /** Where a ticket may move. A proposal has to be answered first; organization tickets follow their board's rules. */
    fun move(ticket: KanbanTicket, column: String): OrganizationModel.Move? = when {
        column == "blocked" && ticket.status == "blocked" -> null
        column == "blocked" -> OrganizationModel.Move.Refused("Tickets move to Blocked on their own when their work is waiting, stopped or failed.")
        ticket.proposed -> OrganizationModel.Move.Refused("Approve or decline this proposal first.")
        ticket.isRem -> remMove(ticket, column)
        ticket.running -> OrganizationModel.Move.Refused("This ticket is running. Pause the organization or wait for the run to finish.")
        // Out of Blocked by reopening it straight into the chosen column, as the desktop does.
        ticket.status == "blocked" && column in setOf("backlog", "todo") -> OrganizationModel.Move.Allowed(column, reopen = true)
        else -> OrganizationModel.move(OrganizationTask(ticket.id, ticket.title, "", ticket.status, ticket.priority, ticket.assignee, ticket.revision,
            ticket.running, false, ticket.status == "in_review", false, ticket.blockReason, "", "", emptyList()), column)
    }

    /** Rem's tickets reach Review on their own while their run's review checks the work, and only you mark one done by hand. */
    private fun remMove(ticket: KanbanTicket, column: String): OrganizationModel.Move? {
        // A paused ticket keeps its run: moving it to In progress resumes that run, nothing else.
        if (ticket.paused.isNotBlank()) return if (column == "running") OrganizationModel.Move.Allowed(column, pull = true)
            else OrganizationModel.Move.Refused("This ticket's task is paused. Move it to In progress to resume it, or stop its run first.")
        if (column(ticket) == column) return null
        if (ticket.running) return OrganizationModel.Move.Refused("This ticket is running. Stop its run or wait for it to finish.")
        return when (column) {
            "running" -> if (ticket.status in setOf("backlog", "todo")) OrganizationModel.Move.Allowed(column, pull = true)
                else OrganizationModel.Move.Refused("Move it back to To do before starting it.")
            "review" -> OrganizationModel.Move.Refused("Rem's tickets move to Review on their own while their run's review checks the work.")
            else -> OrganizationModel.Move.Allowed(column, reopen = ticket.status in setOf("done", "blocked", "cancelled"))
        }
    }

    fun blockText(ticket: KanbanTicket) = when {
        ticket.status != "blocked" -> ""
        ticket.blockReason.isNotBlank() -> ticket.blockReason
        else -> "Blocked, with no reason recorded."
    }

    // People --------------------------------------------------------------------------------------------------------

    /** Rem first, then each organization's agents by name, as a ticket tool lists its members. */
    fun people(board: KanbanBoard): List<KanbanParticipant> = board.participants.sortedWith(
        compareBy<KanbanParticipant> { !it.isRem }.thenBy { it.organizationName.lowercase() }.thenBy { it.name.lowercase() }.thenBy { it.id })

    /** The list under its section titles: Rem's first, then one for each organization. */
    fun peopleSections(people: List<KanbanParticipant>): List<Pair<String, List<KanbanParticipant>>> =
        people.groupBy { if (it.isRem) "Rem" else it.organizationName.ifBlank { "Organization" } }.toList()

    fun matchesPerson(person: KanbanParticipant, query: String): Boolean {
        val words = query.trim().lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val text = listOf(person.name, person.title, person.organizationName).joinToString(" ").lowercase()
        return words.all { it in text }
    }

    fun person(board: KanbanBoard, id: String) = board.participants.firstOrNull { it.id == id }

    fun projectName(board: KanbanBoard, id: String) = board.projects.firstOrNull { it.id == id }?.name?.ifBlank { null } ?: "A project that isn't open"

    /** Names of the projects [person] can work in, in the desktop's catalog order. */
    fun projectNames(board: KanbanBoard, person: KanbanParticipant): List<String> {
        val order = board.projects.map { it.id }
        return person.projects.sortedBy { id -> order.indexOf(id).let { if (it < 0) Int.MAX_VALUE else it } }.map { projectName(board, it) }
    }

    /** What the list and the person's page say about where this participant can work. */
    fun boundaryText(person: KanbanParticipant): String = when {
        person.projects.isEmpty() && person.hiddenProjects == 0 -> "No projects, so no self-work."
        person.projects.isEmpty() -> "${person.hiddenProjects} ${if (person.hiddenProjects == 1) "project" else "projects"} this phone can't see."
        person.hiddenProjects > 0 -> "Plus ${person.hiddenProjects} this phone can't see."
        person.unavailable.isNotEmpty() -> "${person.unavailable.size} not open on your computer, so unused for now."
        else -> ""
    }

    /** Every project the person can be given, and any they hold that can't be given again, so nothing silently drops. */
    fun projectChoices(board: KanbanBoard, person: KanbanParticipant): List<KanbanProject> {
        val ids = (person.assignable + person.projects).distinct()
        val order = board.projects.map { it.id }
        return ids.map { id -> board.projects.firstOrNull { it.id == id } ?: KanbanProject(id, projectName(board, id), available = false) }.sortedBy { p -> order.indexOf(p.id).let { if (it < 0) Int.MAX_VALUE else it } }
    }

    fun toggled(selected: List<String>, project: String) = if (project in selected) selected - project else selected + project

    fun sameProjects(left: List<String>, right: List<String>) = left.toSet() == right.toSet()

    /** Whether this project can be ticked: it's offered to this person and is open now. Otherwise it can only be unticked. */
    fun canAdd(person: KanbanParticipant, project: KanbanProject) = project.available && project.id in person.assignable && project.id !in person.unavailable

    /** The person a ticket is with: Rem for Rem's tickets, the assigned agent for an organization's. */
    fun assignedPerson(board: KanbanBoard, ticket: KanbanTicket) =
        if (ticket.isRem) person(board, REM)
        else board.participants.firstOrNull { !it.isRem && it.organizationId == ticket.organizationId && it.employeeId == ticket.assigneeId }

    /** A person's open tickets, running first. */
    fun openTicketsOf(board: KanbanBoard, person: KanbanParticipant): List<KanbanTicket> = ordered(board.tickets.filter { t ->
        onBoard(t) && column(t) != "done" && (if (person.isRem) t.isRem else !t.isRem && t.organizationId == person.organizationId && t.assigneeId == person.employeeId)
    })

    /** Why self-work won't take one of Rem's tickets, in the desktop's terms. Empty when it can, or for tickets self-work doesn't choose. */
    fun outsideProject(ticket: KanbanTicket): String {
        if (!ticket.isRem || ticket.status in setOf("done", "cancelled")) return ""
        return when (ticket.selfWorkScope) {
            "no_project" -> "This ticket has no project, so Rem won't pick it up during self-work. Starting it yourself still works."
            "unassigned" -> "Rem isn't assigned to ${ticket.project.ifBlank { "this project" }}, so it won't pick this up during self-work. Starting it yourself still works."
            "unavailable" -> "${ticket.project.ifBlank { "This project" }} isn't open on your computer, so Rem won't pick this up during self-work."
            else -> ""
        }
    }

    /** The same warning for a new Rem ticket being written. */
    fun outsideProject(board: KanbanBoard, projectId: String): String {
        val rem = person(board, REM) ?: return ""
        if (projectId.isBlank()) return "With no project, Rem won't pick this up during self-work."
        if (projectId in rem.projects && projectId !in rem.unavailable) return ""
        return "Rem isn't assigned to ${projectName(board, projectId)}, so it won't pick this up during self-work."
    }

    /** Where to look on the board for one person's tickets: the scope filter and the assignee filter. */
    fun boardFilterFor(person: KanbanParticipant): Pair<String, String> =
        if (person.isRem) SCOPE_REM to REM else person.organizationId to person.employeeId

    // Requests ------------------------------------------------------------------------------------------------------

    /** A ticket's own address on the desktop, repeated on every action. */
    private fun address(ticket: KanbanTicket, action: String) = JSONObject().put("action", action).put("ticket_id", ticket.id).put("scope", ticket.scope)
        .put("expected_revision", ticket.revision).apply { if (!ticket.isRem) put("organization_id", ticket.organizationId) }

    /** [place] is [SCOPE_REM] or an organization's ID. */
    fun createParams(place: String, title: String, description: String, priority: String, column: String, assignee: String, kind: String = "develop", project: String = "") = JSONObject()
        .put("action", "create").put("scope", if (place == SCOPE_REM) SCOPE_REM else SCOPE_ORG).put("title", title.trim().take(200))
        .put("description", description.trim().take(4000)).put("priority", priority).put("status", if (column == "todo") "todo" else "backlog")
        .apply {
            if (place == SCOPE_REM) { put("type", kind); if (project.isNotBlank()) put("project_id", project) } else put("organization_id", place)
            if (assignee != UNASSIGNED) put("assignee_id", assignee)
        }

    /** [choice] is an assignee's ID, or a handover from [assignChoices]. */
    fun assignParams(ticket: KanbanTicket, choice: String): JSONObject {
        val params = address(ticket, "assign")
        if (!isHandOver(choice)) return params.put("assignee_id", choice)
        val parts = choice.removePrefix(HANDOVER).split(HANDOVER_WORKER, limit = 2)
        return params.put("to_organization_id", parts[0]).put("assignee_id", parts.getOrElse(1) { "" })
    }
    fun moveParams(ticket: KanbanTicket, column: String) = address(ticket, "move").put("column", column)
    fun commentParams(ticket: KanbanTicket, body: String) = address(ticket, "comment").put("body", body.trim())
    /** The read for a ticket's history, by its board ID: `organization/ticket`, with no organization for Rem's. */
    fun timelineParams(uid: String): JSONObject {
        val organization = uid.substringBefore('/'); val id = uid.substringAfter('/')
        return JSONObject().put("ticket_id", id).put("scope", if (organization.isBlank()) SCOPE_REM else SCOPE_ORG).put("organization_id", organization)
    }
    fun timelineKey(params: JSONObject) = TIMELINE + ":" + params.optString("organization_id") + "/" + params.optString("ticket_id")

    // Timeline ------------------------------------------------------------------------------------------------------

    /** How an entry is colored: by the column it moved to. Comments have no column. */
    fun tone(comment: TicketComment) = when {
        comment.kind != "status" -> "note"
        comment.to == "blocked" -> "bad"
        comment.to == "done" -> "done"
        comment.to == "in_review" -> "review"
        comment.to == "in_progress" -> "run"
        else -> "idle"
    }

    fun headline(comment: TicketComment) = comment.title.ifBlank {
        if (comment.kind == "status" && comment.to.isNotBlank()) "Moved to ${STATUS_LABELS[comment.to] ?: comment.to}" else ""
    }

    fun move(comment: TicketComment) = if (comment.kind == "status" && comment.from.isNotBlank() && comment.to.isNotBlank())
        "${STATUS_LABELS[comment.from] ?: comment.from} → ${STATUS_LABELS[comment.to] ?: comment.to}" else ""

    fun checksLine(checks: List<TicketCheck>): String {
        if (checks.isEmpty()) return ""
        val passed = checks.count { it.result == "pass" }
        return if (passed == checks.size) (if (passed == 1) "The check passed" else "All $passed checks passed") else "$passed of ${checks.size} checks passed"
    }

    fun parseTimeline(json: JSONObject) = TicketTimeline(json.optJSONObject("ticket")?.let(::ticket)?.takeIf { it.id.isNotBlank() },
        json.objects("comments").map { c -> TicketComment(c.optString("id"), c.optString("at"), c.optString("author", "system"), c.optString("author_name"),
            c.optString("kind", "comment"), c.optString("title"), c.optString("body"), c.optString("details"), c.optString("from"), c.optString("to"),
            c.objects("checks").map { TicketCheck(it.optString("name"), it.optString("result")) }.filter { it.name.isNotBlank() }, c.optString("thread_id")) },
        json.optInt("earlier"), json.optBoolean("can_comment", true))
    fun decideParams(ticket: KanbanTicket, approve: Boolean, reason: String = "") = address(ticket, if (approve) "approve" else "decline")
        .apply { if (!approve && reason.isNotBlank()) put("reason", reason.trim().take(1000)) }

    fun settingsParams(settings: SelfWorkSettings, enabled: Boolean? = null, maxThreads: Int? = null, autoPull: Boolean? = null) =
        JSONObject().put("expected_revision", settings.revision).apply {
            enabled?.let { put("enabled", it) }
            maxThreads?.let { put("max_threads", it) }
            autoPull?.let { put("auto_pull_backlog", it) }
        }

    fun seenParams(report: SelfWorkReport) = JSONObject().put("updated_at", report.updatedAt)

    /** [projects] replaces what this phone can see; [revision] is the one the edit began from, so a change made elsewhere isn't overwritten. */
    fun participantParams(person: KanbanParticipant, projects: List<String>, revision: Long = person.revision) = JSONObject()
        .put("participant_id", person.id).put("projects", JSONArray(projects.distinct().sorted())).put("expected_revision", revision)

    // Self-work ---------------------------------------------------------------------------------------------------------

    /** One line for what self-work is doing now. */
    fun status(view: SelfWorkView): String {
        val s = view.settings; val state = view.state
        return when {
            !s.enabled || state.mode == "off" -> "Self-work is off"
            state.detail.isNotBlank() -> state.detail
            state.mode == "paused" -> "Self-work is paused"
            state.mode == "working" -> state.threadLimit.let { limit -> "Working in ${state.activeThreads} of $limit ${if (limit == 1) "thread" else "threads"}" }
            else -> "Starts after ${s.idleMinutes} minutes without a message from you"
        }
    }

    /** What the backlog setting does now: with the board off on the computer, it has nothing to pull. */
    fun autoPullDetail(view: SelfWorkView) =
        if (!view.state.boardEnabled) "Needs the Kanban board. It's off on your computer, so Rem doesn't take tickets."
        else afterTodos(view.settings.autoPull)

    /** What self-work does without the board, or null while the board is on. */
    fun boardOffNote(view: SelfWorkView) = if (view.state.boardEnabled) null
        else "The Kanban board is off on your computer, so Rem leaves tickets alone. It researches its projects and lists the ideas in the report."

    /** What self-work does once its To do list is empty, in the words the setting explains. */
    fun afterTodos(autoPull: Boolean) =
        if (autoPull) "When To do is empty, Rem pulls work from Backlog and does it."
        else "When To do is empty, Rem reviews and deduplicates Backlog and proposes new tickets for you."

    /** The short line on Rem's home page, or empty while the report has nothing new to say. */
    fun reportHeadline(report: SelfWorkReport?): String {
        if (report == null || !report.unseen || report.isEmpty) return ""
        val toApprove = report.ticketsAdded.count { it.proposed }
        val finished = report.workDone.count { it.state == "done" }
        val blocked = report.workDone.count { it.state == "blocked" }
        val researched = report.workDone.count { it.state == "researched" }
        val parts = listOfNotNull(
            report.ticketsAdded.size.takeIf { it > 0 }?.let { "$it ${if (it == 1) "ticket" else "tickets"} added" },
            toApprove.takeIf { it > 0 }?.let { "$it to approve" },
            finished.takeIf { it > 0 }?.let { "$it done" },
            researched.takeIf { it > 0 }?.let { "$it researched" },
            blocked.takeIf { it > 0 }?.let { "$it ${if (it == 1) "needs" else "need"} you" },
            report.duplicatesMerged.size.takeIf { it > 0 }?.let { "$it merged" })
        return parts.joinToString(" · ").ifBlank { report.notes.lastOrNull()?.text.orEmpty().lineSequence().first().take(120) }
    }

    // Parsing -------------------------------------------------------------------------------------------------------

    fun parseBoard(json: JSONObject) = KanbanBoard(json.objects("tickets").map(::ticket).filter { it.id.isNotBlank() },
        json.objects("organizations").map { o -> KanbanOrganization(o.optString("organization_id"), o.optString("name"), o.optString("state"),
            o.objects("employees").map { KanbanEmployee(it.optString("employee_id"), it.optString("name"), it.optString("title")) }) }.filter { it.id.isNotBlank() },
        json.optInt("earlier"), participants(json), json.objects("projects").map { KanbanProject(it.optString("project_id"), it.optString("name"), it.optBoolean("available", true), it.optString("organization_name")) }.filter { it.id.isNotBlank() },
        json.has("participants"))

    private fun participants(json: JSONObject) = json.objects("participants").map { p -> KanbanParticipant(p.optString("participant_id"), p.optString("kind", "agent"),
        p.optString("name"), p.optString("title"), p.optString("organization_id"), p.optString("organization_name"), p.optString("employee_id"), p.optBoolean("paused"),
        p.strings("projects"), p.strings("assignable"), p.optInt("hidden_projects"), p.optLong("revision"), p.strings("unavailable")) }.filter { it.id.isNotBlank() }.distinctBy { it.id }

    private fun ticket(t: JSONObject) = KanbanTicket(t.optString("ticket_id"), t.optString("scope", SCOPE_ORG), t.optString("organization_id"),
        t.optString("organization_name"), t.optString("key"), t.optString("title"), t.optString("description"), t.optString("status"),
        t.optString("priority", "medium").let { if (it == "urgent") "critical" else it }, t.optString("assignee_id"), t.optString("assignee"), t.optBoolean("proposed"), t.optString("proposal_reason"),
        t.optString("duplicate_of"), t.optString("created_by"), t.optString("created_at"), t.optString("updated_at"), t.optLong("revision", 1),
        t.optBoolean("running"), t.optString("block_reason"), t.optString("summary"), t.optString("type"), t.optString("project"), t.optString("project_id"), t.optString("self_work_scope"),
        RunPause.by(t.optString("paused")))

    fun parseSelfWork(json: JSONObject): SelfWorkView {
        val s = json.optJSONObject("settings"); val l = json.optJSONObject("limits"); val st = json.optJSONObject("state"); val r = json.optJSONObject("report")
        val low = l?.optInt("max_threads_min", 1) ?: 1
        val high = (l?.optInt("max_threads_max", 8) ?: 8).coerceAtLeast(low)
        return SelfWorkView(
            SelfWorkSettings(s?.optBoolean("enabled") == true, (s?.optInt("max_threads", low) ?: low), s?.optBoolean("auto_pull_backlog") == true,
                s?.optInt("idle_minutes", 15) ?: 15, s?.optLong("revision", 0) ?: 0,
                // An older desktop has no windows, so the saved switch is the one in effect.
                (s?.opt("saved_enabled") as? Boolean) ?: (s?.optBoolean("enabled") == true)),
            SelfWorkLimits(low, high),
            SelfWorkState(st?.optString("mode", "off") ?: "off", st?.optInt("active_threads") ?: 0, st?.optInt("todo_remaining") ?: 0,
                st?.optString("last_prompt_at").orEmpty(), st?.optString("starts_at").orEmpty(), st?.optString("detail").orEmpty(),
                st?.optInt("thread_limit", 0)?.takeIf { it > 0 } ?: (s?.optInt("max_threads", low) ?: low), st?.optInt("proposals_waiting") ?: 0,
                st?.takeIf { it.has("assigned_projects") }?.optInt("assigned_projects"), st?.optInt("outside_projects") ?: 0,
                // An older desktop doesn't say, and always worked with the board.
                st?.optBoolean("board_enabled", true) ?: true,
                st?.optInt("paused_threads") ?: 0, st?.optInt("paused_for_you") ?: 0),
            r?.let { report -> SelfWorkReport(report.optString("since"), report.optString("updated_at"), report.optBoolean("unseen"), report.optString("summary"),
                report.objects("tickets_added").map { ReportTicket(it.optString("ticket_id"), it.optString("title"), it.optString("scope"),
                    it.optString("organization_id"), it.optString("organization_name"), it.optBoolean("proposed"), it.optString("at")) },
                report.objects("work_done").map { ReportWork(it.optString("ticket_id"), it.optString("title"), it.optString("state"), it.optString("summary"),
                    it.optString("thread_id"), it.optString("at")) },
                report.objects("duplicates_merged").map { ReportMerged(it.optString("ticket_id"), it.optString("title"), it.optString("kept_title"), it.optString("reason"), it.optString("at")) },
                report.objects("notes").map { ReportNote(it.optString("text"), it.optString("at")) }) },
            json.optJSONObject("schedule")?.let(::schedule))
    }

    /** Timed self-work windows. An older desktop sends none, and the page shows nothing new. */
    private fun schedule(s: JSONObject) = SelfWorkSchedule(s.optString("timezone").take(64), s.optJSONObject("active")?.let(::window)?.takeIf { it.id.isNotBlank() },
        s.objects("upcoming").take(5).map(::window).filter { it.id.isNotBlank() },
        s.optJSONObject("last_period")?.let { p -> SelfWorkPeriod(p.optString("id").take(128), p.optString("started_at").take(80), p.optString("ended_at").take(80),
            p.optString("ended_by").take(20), p.optString("summary").take(600), p.optString("message_id").take(160)) }?.takeIf { it.id.isNotBlank() })

    private fun window(w: JSONObject) = SelfWorkWindow(w.optString("id").take(128), w.optString("start_at").take(80),
        w.optString("end_at").takeUnless { w.isNull("end_at") }.orEmpty().take(80), w.optString("until").take(40), w.optString("timezone").take(64),
        w.optString("after_end", "restore").take(20), w.optBoolean("wait_for_idle"), w.optString("label").take(160), w.optString("end_label").take(160),
        w.optString("after_label").take(80))

    // Self-work windows -------------------------------------------------------------------------------------------------

    /** Said while a timed window keeps self-work on although its switch is saved off; null otherwise or from an older desktop. */
    fun windowNote(view: SelfWorkView): String? {
        val s = view.settings
        if (s.savedEnabled == s.enabled || !s.enabled) return null
        return if (view.schedule?.active?.afterEnd == "on") "On for this window; stays on after it." else "On for this window; goes back to off after it."
    }

    /** A window's second line: when it ends and what the switch does then, as the desktop words them. */
    fun windowDetail(window: SelfWorkWindow, idleMinutes: Int): String = listOfNotNull(
        listOfNotNull(window.endLabel.takeIf { it.isNotBlank() }?.let { "Ends $it" }, window.afterLabel.takeIf { it.isNotBlank() })
            .joinToString(", ").takeIf { it.isNotBlank() },
        if (window.waitForIdle) "Waits for $idleMinutes quiet minutes first" else null).joinToString(" · ")

    /** How a period of self-work ended, in words. */
    fun periodEnd(period: SelfWorkPeriod): String = when (period.endedBy) {
        "window" -> "Its window ended"
        "user" -> "Turned off"
        "condition" -> "Its running tasks finished"
        else -> "Ended"
    }

    private fun JSONObject.objects(key: String): List<JSONObject> = optJSONArray(key)?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
    private fun JSONObject.strings(key: String): List<String> = optJSONArray(key)?.let { a -> (0 until a.length()).map { a.optString(it) }.filter { it.isNotBlank() } }.orEmpty()
}

/** One ticket's history from the desktop, oldest first. [earlier] counts older entries left out. [ticket] is the ticket as the desktop has it now. */
data class TicketTimeline(val ticket: KanbanTicket?, val comments: List<TicketComment>, val earlier: Int, val canComment: Boolean)
/** A status change, with its reason from the run or review, or a comment. [author] is `system`, `rem`, `user` or `worker`. */
data class TicketComment(val id: String, val at: String, val author: String, val authorName: String, val kind: String, val title: String,
                         val body: String, val details: String, val from: String, val to: String, val checks: List<TicketCheck>, val threadId: String = "")
/** An acceptance check as the review judged it: `pass`, `fail` or not yet known. */
data class TicketCheck(val name: String, val result: String)
data class KanbanEmployee(val id: String, val name: String, val title: String)
data class KanbanOrganization(val id: String, val name: String, val state: String, val employees: List<KanbanEmployee>)
/** [organizationId] and [organizationName] are empty on Rem's own tickets. */
data class KanbanTicket(val id: String, val scope: String, val organizationId: String, val organizationName: String, val key: String, val title: String,
                        val description: String, val status: String, val priority: String, val assigneeId: String, val assignee: String, val proposed: Boolean,
                        val proposalReason: String, val duplicateOf: String, val createdBy: String, val createdAt: String, val updatedAt: String,
                        val revision: Long, val running: Boolean, val blockReason: String,
                        /** What Rem's last run on it said; empty on organization tickets. */
                        val summary: String = "",
                        /** `develop`, `research`, `maintain` or `errand` on Rem's tickets. */
                        val kind: String = "",
                        /** The project's name, never its folder. */
                        val project: String = "",
                        /** The project's ID in this phone's catalog, when it has one. */
                        val projectId: String = "",
                        /** Whether self-work can take it: `assigned`, `unassigned`, `unavailable` or `no_project`. Empty on organization tickets. */
                        val selfWorkScope: String = "",
                        /** Its task is paused: "user", or "priority" when self-work made way for the user's own work. */
                        val paused: String = "") {
    val isRem get() = scope == Kanban.SCOPE_REM
    /** Ticket IDs are unique only within their organization; this one is unique on the board. */
    val uid get() = "$organizationId/$id"
    val owner get() = if (isRem) "Rem" else organizationName.ifBlank { "Organization" }
}
/** [earlier] counts tickets the desktop left out to keep the answer small. */
data class KanbanBoard(val tickets: List<KanbanTicket>, val organizations: List<KanbanOrganization>, val earlier: Int,
                       val participants: List<KanbanParticipant> = emptyList(), val projects: List<KanbanProject> = emptyList(),
                       /** False from a desktop that doesn't send participants yet. */
                       val participantsKnown: Boolean = false)
/** [available] is false once the project is closed or gone on the computer. [organization] is the organization that owns it, if any. */
data class KanbanProject(val id: String, val name: String, val available: Boolean = true, val organization: String = "")
/**
 * Someone on the board: Rem or an organization's agent. [projects] and [assignable] are catalog IDs; [hiddenProjects] counts
 * assigned projects this phone has no ID for. The desktop enforces these during self-work.
 */
data class KanbanParticipant(val id: String, val kind: String, val name: String, val title: String, val organizationId: String, val organizationName: String,
                             val employeeId: String, val paused: Boolean, val projects: List<String>, val assignable: List<String>, val hiddenProjects: Int, val revision: Long,
                             /** Assigned projects that can't be used now, such as one closed on the computer. */
                             val unavailable: List<String> = emptyList()) {
    val isRem get() = id == Kanban.REM
}
/**
 * [enabled] is the state in effect, on while a timed window runs; [savedEnabled] is the switch as saved, what self-work
 * returns to after the window.
 */
data class SelfWorkSettings(val enabled: Boolean, val maxThreads: Int, val autoPull: Boolean, val idleMinutes: Int, val revision: Long,
                            val savedEnabled: Boolean = enabled)
/**
 * A timed self-work window. [endAt] is empty when it ends on a condition: [until] `running_done` waits for the tasks that ran
 * when it was set. [afterEnd] is `restore`, `on` or `off`. The labels come formatted from the desktop and show as they are.
 */
data class SelfWorkWindow(val id: String, val startAt: String, val endAt: String, val until: String, val timezone: String, val afterEnd: String,
                          val waitForIdle: Boolean, val label: String, val endLabel: String, val afterLabel: String)
/** The last period of self-work: [endedBy] is `window`, `user` or `condition`; [messageId] is the Rem chat message with the full report. */
data class SelfWorkPeriod(val id: String, val startedAt: String, val endedAt: String, val endedBy: String, val summary: String, val messageId: String)
/** [active] runs now; [upcoming] haven't started, soonest first. */
data class SelfWorkSchedule(val timezone: String, val active: SelfWorkWindow?, val upcoming: List<SelfWorkWindow>, val lastPeriod: SelfWorkPeriod?) {
    val isEmpty get() = active == null && upcoming.isEmpty() && lastPeriod == null
}
data class SelfWorkLimits(val min: Int, val max: Int)
/** [mode] is `off`, `waiting`, `working` or `paused`. */
data class SelfWorkState(val mode: String, val activeThreads: Int, val todoRemaining: Int, val lastPromptAt: String, val startsAt: String, val detail: String,
                         /** How many threads Rem may use now, which available slots can hold below the maximum. */
                         val threadLimit: Int = 1, val proposalsWaiting: Int = 0,
                         /** How many projects Rem can use for self-work now; null from a desktop without assignments. */
                         val assignedProjects: Int? = null,
                         /** To-dos and backlog tickets waiting outside Rem's projects. */
                         val outsideProjects: Int = 0,
                         /** Whether the desktop shows the Kanban board. Off, self-work runs research tasks and leaves tickets alone. */
                         val boardEnabled: Boolean = true,
                         /** Self-work tasks paused now; they hold no thread. */
                         val pausedThreads: Int = 0,
                         /** Of those, the ones that made way for the user's own work and resume on their own. */
                         val pausedForYou: Int = 0)
data class ReportTicket(val id: String, val title: String, val scope: String, val organizationId: String, val organizationName: String, val proposed: Boolean, val at: String)
data class ReportWork(val ticketId: String, val title: String, val state: String, val summary: String, val threadId: String, val at: String)
data class ReportMerged(val ticketId: String, val title: String, val keptTitle: String, val reason: String, val at: String)
data class ReportNote(val text: String, val at: String)
data class SelfWorkReport(val since: String, val updatedAt: String, val unseen: Boolean, val summary: String, val ticketsAdded: List<ReportTicket>, val workDone: List<ReportWork>,
                          val duplicatesMerged: List<ReportMerged> = emptyList(), val notes: List<ReportNote> = emptyList()) {
    /** The desktop always writes a summary, even with nothing to report. */
    val isEmpty get() = ticketsAdded.isEmpty() && workDone.isEmpty() && duplicatesMerged.isEmpty() && notes.isEmpty()
}
/** [schedule] is null from a desktop without timed windows. */
data class SelfWorkView(val settings: SelfWorkSettings, val limits: SelfWorkLimits, val state: SelfWorkState, val report: SelfWorkReport?,
                        val schedule: SelfWorkSchedule? = null)
