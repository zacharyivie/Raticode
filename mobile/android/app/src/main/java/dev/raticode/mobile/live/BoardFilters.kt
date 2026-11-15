package dev.raticode.mobile.live

import org.json.JSONObject

/** The global board's filters on this phone. The column on screen is kept too, so the board opens where you left it. */
data class GlobalBoardFilters(val scope: String = Kanban.ALL, val assignee: String = Kanban.ANY, val project: String = Kanban.ALL, val column: String = "todo") {
    /** How many filters narrow the tickets shown. */
    val active get() = listOf(scope != Kanban.ALL, assignee != Kanban.ANY, project != Kanban.ALL).count { it }
    /** Every filter off; the column stays. */
    fun cleared() = copy(scope = Kanban.ALL, assignee = Kanban.ANY, project = Kanban.ALL)
}

/** One organization's board filters on this phone, and its column on screen. */
data class OrganizationBoardFilters(val project: String = "", val worker: String = "", val needsMe: Boolean = false, val column: String = "todo") {
    val active get() = listOf(project.isNotEmpty(), worker.isNotEmpty(), needsMe).count { it }
    fun cleared() = copy(project = "", worker = "", needsMe = false)
}

/**
 * Board filters kept on this phone: one set for the global board and one per organization. They come back after leaving the
 * board and after the app closes. They stay on the phone; the desktop keeps its own. A saved person, project or organization
 * that is no longer there falls back to showing everything for that filter alone.
 */
object BoardFilters {
    const val GLOBAL = "global"
    const val GLOBAL_TAB = "global.tab"
    fun organization(id: String) = "organization:$id"
    /** The global board's sections, as in KanbanTabs. */
    val TABS = setOf("board", "proposed", "people", "self")
    /** Old organizations' filters are dropped, least recently changed first. */
    const val MAX_BOARDS = 100
    private const val MAX_TEXT = 500

    private fun read(text: String?): JSONObject? = text?.let { runCatching { JSONObject(it) }.getOrNull() }
    private fun JSONObject.text(name: String, fallback: String): String =
        (opt(name) as? String)?.takeIf { it.length <= MAX_TEXT } ?: fallback
    private fun JSONObject.flag(name: String, fallback: Boolean): Boolean = opt(name) as? Boolean ?: fallback

    fun parseGlobal(text: String?): GlobalBoardFilters {
        val json = read(text) ?: return GlobalBoardFilters()
        val default = GlobalBoardFilters()
        return GlobalBoardFilters(json.text("scope", default.scope), json.text("assignee", default.assignee), json.text("project", default.project),
            json.text("column", default.column).takeIf { it in Kanban.COLUMNS } ?: default.column)
    }

    fun parseOrganization(text: String?): OrganizationBoardFilters {
        val json = read(text) ?: return OrganizationBoardFilters()
        val default = OrganizationBoardFilters()
        return OrganizationBoardFilters(json.text("project", default.project), json.text("worker", default.worker), json.flag("needs_me", default.needsMe),
            json.text("column", default.column).takeIf { it in OrganizationModel.COLUMNS } ?: default.column)
    }

    fun parseTab(text: String?): String = text?.takeIf { it in TABS } ?: "board"

    fun encode(filters: GlobalBoardFilters, at: Long): String = JSONObject().put("scope", filters.scope).put("assignee", filters.assignee)
        .put("project", filters.project).put("column", filters.column).put("at", at).toString()

    fun encode(filters: OrganizationBoardFilters, at: Long): String = JSONObject().put("project", filters.project).put("worker", filters.worker)
        .put("needs_me", filters.needsMe).put("column", filters.column).put("at", at).toString()

    /** Organizations' boards beyond [MAX_BOARDS] to forget, oldest first; the global board is never dropped. [saved] maps each key to its stored text. */
    fun overflow(saved: Map<String, String?>, keep: String): List<String> {
        val boards = saved.filterKeys { it.startsWith("organization:") && it != keep }
        if (boards.size < MAX_BOARDS) return emptyList()
        return boards.entries.sortedByDescending { read(it.value)?.optLong("at") ?: 0L }.drop(MAX_BOARDS - 1).map { it.key }
    }

    /** The filters as the board can show them: an organization, person or project no longer on it shows everything again. */
    fun live(filters: GlobalBoardFilters, board: KanbanBoard): GlobalBoardFilters {
        val scope = filters.scope.takeIf { s -> Kanban.scopes(board).any { it.first == s } } ?: Kanban.ALL
        val assignee = filters.assignee.takeIf { a -> Kanban.assigneeFilters(board, scope).any { it.first == a } } ?: Kanban.ANY
        val project = filters.project.takeIf { p -> p == Kanban.ALL || Kanban.projectFilters(board).any { it.first == p } } ?: Kanban.ALL
        return filters.copy(scope = scope, assignee = assignee, project = project)
    }

    /** The same for an organization's board: a removed project or worker falls back alone. */
    fun live(filters: OrganizationBoardFilters, organization: Organization, plan: OrgPlan): OrganizationBoardFilters = filters.copy(
        project = filters.project.takeIf { p -> p.isEmpty() || plan.projects.any { it.id == p } } ?: "",
        worker = filters.worker.takeIf { w -> w.isEmpty() || organization.employees.any { it.id == w } } ?: "",
    )
}
