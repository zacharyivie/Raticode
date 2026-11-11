package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/** A global board ticket as the desktop's board has it now. [uid] is the board's own ticket ID ([KanbanTicket.uid]). */
data class RemTicket(val ticketId: String, val key: String, val title: String, val status: String, val scope: String,
                     val organizationId: String, val proposed: Boolean = false) {
    val uid get() = "$organizationId/$ticketId"
}

/**
 * A ticket a task's run created ("created"), worked on ("worked") or changed ("updated"), with the title it had then,
 * so a card still names a ticket that was later deleted.
 */
data class RemTicketRef(val ticketId: String, val key: String, val title: String, val scope: String, val organizationId: String, val role: String) {
    val uid get() = "$organizationId/$ticketId"
}

/** Work Rem started on its own. Cards one self-work pass started share [batch], and the chat shows them together. */
data class RemSelfWork(val kind: String, val batch: String)

/**
 * Board ticket keys in Rem's chat (REM-5, AGE-18) show as pills with the ticket's title. The desktop resolves keys
 * against the real board and sends only tickets that exist (`tickets` in `rem.home` and `rem.task`); any other key-shaped
 * text, such as UTF-8, stays plain. Same rule as the desktop's TICKET_KEY: a whole word, not part of a path, word or version.
 */
object TicketMentions {
    val KEY = Regex("(?<![A-Za-z0-9_\\-/.#@])[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,6}(?![A-Za-z0-9_-]|\\.[0-9])")
    private val ROLES = setOf("created", "worked", "updated")
    private val SCOPES = setOf("rem", "organization")

    /** Keys written in [text], first mention first, each once. */
    fun keys(text: String): List<String> = if ('-' !in text) emptyList() else KEY.findAll(text).map { it.value }.distinct().toList()

    /** Where keys of tickets in [tickets] are in [text]. */
    fun inText(text: String, tickets: Map<String, RemTicket>): List<Pair<IntRange, RemTicket>> =
        if (tickets.isEmpty() || '-' !in text) emptyList() else KEY.findAll(text).mapNotNull { m -> tickets[m.value]?.let { m.range to it } }.toList()

    fun statusLabel(status: String): String = when (status) {
        "backlog" -> "Backlog"
        "todo" -> "To do"
        "in_progress" -> "In progress"
        "in_review" -> "In review"
        "blocked" -> "Blocked"
        "done" -> "Done"
        "cancelled" -> "Closed"
        else -> ""
    }

    /** What TalkBack says for a pill, and what it does when tapped. */
    fun label(ticket: RemTicket): String {
        val status = statusLabel(ticket.status).takeIf { it.isNotEmpty() }?.let { " ($it${if (ticket.proposed) ", waiting for your approval" else ""})" }.orEmpty()
        return "Ticket ${ticket.key}: ${ticket.title}$status"
    }

    fun goneLabel(ref: RemTicketRef): String = "${ref.key}: ${ref.title.ifBlank { "ticket" }}, no longer on the board"

    fun roleLabel(role: String): String = when (role) {
        "created" -> "Created by this task"
        "worked" -> "Worked on by this task"
        else -> "Changed by this task"
    }

    /** The desktop's ticket index, by key; anything malformed is left out. */
    fun parseIndex(json: JSONObject?): Map<String, RemTicket> {
        json ?: return emptyMap()
        val found = LinkedHashMap<String, RemTicket>()
        for (key in json.keys()) {
            val t = json.optJSONObject(key) ?: continue
            if (!KEY.matches(key) || t.optString("ticket_id").isBlank()) continue
            found[key] = RemTicket(t.optString("ticket_id").take(128), key, t.optString("title").take(200), t.optString("status").take(20),
                t.optString("scope").takeIf { it in SCOPES } ?: "rem", t.optString("organization_id").take(100), t.optBoolean("proposed"))
            if (found.size >= 400) break
        }
        return found
    }

    fun parseRefs(array: JSONArray?): List<RemTicketRef> {
        array ?: return emptyList()
        return (0 until array.length()).mapNotNull { array.optJSONObject(it) }.filter { KEY.matches(it.optString("key")) }.take(40).map {
            RemTicketRef(it.optString("ticket_id").take(128), it.optString("key"), it.optString("title").take(200),
                it.optString("scope").takeIf { s -> s in SCOPES } ?: "rem", it.optString("organization_id").take(100),
                it.optString("role").takeIf { r -> r in ROLES } ?: "updated")
        }
    }

    fun parseSelfWork(json: JSONObject?): RemSelfWork? = json?.let { RemSelfWork(it.optString("kind").take(20), it.optString("batch").take(64)) }

    /** The live ticket for a card's reference: the same ticket by ID, so a reused key never shows another ticket. */
    fun live(ref: RemTicketRef, tickets: Map<String, RemTicket>): RemTicket? =
        tickets[ref.key]?.takeIf { ref.ticketId.isBlank() || it.ticketId == ref.ticketId }

    /** A card's tickets: the live run's, else the ones saved with the message. */
    fun cardTickets(work: RemWork, run: RemRun?): List<RemTicketRef> = run?.tickets?.takeIf { it.isNotEmpty() } ?: work.tickets

    /** Every ticket a task's runs touched, newest run first, one per ticket. */
    fun taskTickets(task: RemTask): List<RemTicketRef> =
        (listOfNotNull(task.activeRun, task.lastRun) + task.runs).flatMap { it.tickets }.distinctBy { it.ticketId.ifBlank { it.key } }

    /** The line over cards one self-work pass started, as on the desktop. */
    fun batchLine(messages: List<RemMessage>): String {
        val counts = messages.groupingBy { it.selfWork?.kind?.ifBlank { null } ?: it.work?.selfWork?.ifBlank { null } ?: "ticket" }.eachCount()
        val parts = listOf(Triple("ticket", "ticket", "tickets"), Triple("triage", "backlog ranking", "backlog rankings"),
            Triple("research", "research run", "research runs")).mapNotNull { (kind, one, many) ->
            counts[kind]?.let { "$it ${if (it == 1) one else many}" }
        }
        val list = when {
            parts.isEmpty() -> "${messages.size} tasks"
            parts.size == 1 -> parts[0]
            else -> parts.dropLast(1).joinToString(", ") + " and " + parts.last()
        }
        return "Started ${messages.size} things while you were away: $list."
    }
}
