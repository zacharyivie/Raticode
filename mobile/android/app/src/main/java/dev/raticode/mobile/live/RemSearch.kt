package dev.raticode.mobile.live

import org.json.JSONObject

/** Part of a search snippet: plain words, or words that matched. */
data class RemSearchPart(val text: String, val match: Boolean)

/** One message that matched, as the desktop's search (or this phone's own, offline) found it. */
data class RemSearchResult(val id: String, val role: String, val kind: String, val createdAt: String, val where: String,
                           val snippet: List<RemSearchPart>, val title: List<RemSearchPart> = emptyList())

/**
 * A page of results, newest first. [total] counts every match; [next] asks for the page after this one.
 * [local] means this phone searched only the messages it holds, because it isn't connected or the desktop is older.
 */
data class RemSearchPage(val query: String, val total: Int, val results: List<RemSearchResult>, val next: Long? = null,
                         val local: Boolean = false)

/** Rem chat messages from before what rem.home sends, oldest first; [earlier] are still before them. */
data class RemHistoryPage(val messages: List<RemMessage>, val earlier: Int, val tickets: Map<String, RemTicket>)

/** What the Rem chat's search shows: the words, where the search is, its results and any problem. */
data class RemSearchState(val query: String = "", val status: Status = Status.IDLE, val page: RemSearchPage? = null,
                          val error: String = "", val loadingMore: Boolean = false, val note: String = "") {
    enum class Status { IDLE, LOADING, READY, ERROR }
}

/**
 * Searching Rem's chat without a model. The desktop searches its whole saved history (`rem.search`) and pages back
 * to a result (`rem.history`); both need a desktop whose rem.home says `search`. Offline, or with an older desktop,
 * the phone searches the messages it holds by the same rules: every word must appear, ignoring case, in a message's
 * text, its details, its task title or its file names.
 */
object RemSearch {
    const val SEARCH = "rem.search"
    const val HISTORY = "rem.history"
    const val MAX_QUERY = 200
    const val MAX_TERMS = 8
    /** How long typing must pause before the phone asks the desktop. */
    const val DELAY_MS = 300L
    /** Pages fetched one after another to reach a result before giving up. */
    const val MAX_HOPS = 25
    const val OFFLINE = "Not connected. Showing matches in the messages on this phone."
    const val UPDATE_DESKTOP = "Update Raticode on your desktop to search all of Rem's chat. Showing matches in the messages on this phone."
    private const val SNIPPET = 180
    private const val LEAD = 60

    private val LINK = Regex("!?\\[([^\\]]*)\\]\\([^)]*\\)")
    private val MARKUP = Regex("(?m)^\\s{0,3}(?:#{1,6}\\s+|>\\s?|[-*+]\\s+|\\d+[.)]\\s+)|[*_`~]{1,3}")

    /** The distinct words of a query, at most [MAX_TERMS], in typed order. */
    fun terms(query: String): List<String> =
        query.take(MAX_QUERY).split(Regex("\\s+")).filter { it.isNotEmpty() }.distinctBy { it.lowercase() }.take(MAX_TERMS)

    fun normalized(query: String) = terms(query).joinToString(" ")

    fun params(query: String, before: Long?): JSONObject =
        JSONObject().put("query", normalized(query)).apply { if (before != null) put("before", before) }

    fun historyParams(before: String, through: String?): JSONObject =
        JSONObject().put("before", before).apply { if (!through.isNullOrBlank()) put("through", through) }

    /** Markdown as one plain line, as the desktop searches it. */
    fun plain(text: String): String = MARKUP.replace(LINK.replace(text) { it.groupValues[1] }, "").split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")

    private fun fields(message: RemMessage): List<Pair<String, String>> = buildList {
        add("message" to plain(message.body))
        if (message.details.isNotBlank()) add("details" to plain(message.details))
        message.work?.title?.takeIf { it.isNotBlank() }?.let { add("task" to it) }
        message.result?.title?.takeIf { it.isNotBlank() }?.let { add("result" to it) }
        (message.attachments + message.files).forEach { if (it.name.isNotBlank()) add("file" to it.name) }
    }.filter { it.second.isNotEmpty() }

    private fun pattern(terms: List<String>) = Regex(terms.sortedByDescending { it.length }.joinToString("|") { Regex.escape(it) }, RegexOption.IGNORE_CASE)

    /** [text] split into plain and matched parts, in order. */
    fun parts(text: String, pattern: Regex): List<RemSearchPart> {
        val parts = mutableListOf<RemSearchPart>()
        var at = 0
        for (found in pattern.findAll(text)) {
            if (found.range.isEmpty()) continue
            if (found.range.first > at) parts += RemSearchPart(text.substring(at, found.range.first), false)
            parts += RemSearchPart(found.value, true)
            at = found.range.last + 1
        }
        if (at < text.length) parts += RemSearchPart(text.substring(at), false)
        return parts
    }

    private fun snippet(text: String, pattern: Regex): List<RemSearchPart> {
        var start = maxOf(0, (pattern.find(text)?.range?.first ?: 0) - LEAD)
        if (start > 0) text.indexOf(' ', start).takeIf { it in start until start + 20 }?.let { start = it + 1 }
        var end = minOf(text.length, start + SNIPPET)
        if (end < text.length) text.lastIndexOf(' ', end - 1).takeIf { it > start && it >= end - 20 }?.let { end = it }
        return parts((if (start > 0) "…" else "") + text.substring(start, end) + (if (end < text.length) "…" else ""), pattern)
    }

    /** Messages this phone holds that match [query], newest first; used offline and with an older desktop. */
    fun local(messages: List<RemMessage>, query: String): RemSearchPage {
        val terms = terms(query)
        if (terms.isEmpty()) return RemSearchPage("", 0, emptyList(), local = true)
        val pattern = pattern(terms)
        val results = messages.asReversed().filter { it.state != "waiting" }.mapNotNull { message ->
            val fields = fields(message)
            val folded = fields.joinToString("\n") { it.second }.lowercase()
            if (!terms.all { it.lowercase() in folded }) return@mapNotNull null
            val (where, text) = fields.firstOrNull { pattern.containsMatchIn(it.second) } ?: fields.firstOrNull() ?: ("message" to "")
            RemSearchResult(message.id, if (message.role == "user") "user" else "rem", message.kind, message.createdAt, where, snippet(text, pattern),
                message.work?.title?.takeIf { it.isNotBlank() }?.let { parts(it.take(160), pattern) }.orEmpty())
        }
        return RemSearchPage(terms.joinToString(" "), results.size, results, local = true)
    }

    private fun partsOf(json: JSONObject, key: String): List<RemSearchPart> = json.optJSONArray(key)?.let { array ->
        (0 until array.length()).mapNotNull { array.optJSONObject(it) }.map { RemSearchPart(it.optString("text"), it.optBoolean("match")) }
    }.orEmpty()

    fun parse(json: JSONObject): RemSearchPage {
        val results = json.optJSONArray("results")?.let { array -> (0 until array.length()).mapNotNull { array.optJSONObject(it) } }.orEmpty().map {
            RemSearchResult(it.optString("id"), if (it.optString("role") == "user") "user" else "rem", it.optString("kind"), it.optString("created_at"),
                it.optString("where"), partsOf(it, "snippet"), partsOf(it, "title"))
        }
        val next = (json.opt("next") as? Number)?.toLong()
        return RemSearchPage(json.optString("query"), json.optInt("total"), results, next)
    }

    /** Who wrote a result and, for a match outside the message text, where it was found. */
    fun label(result: RemSearchResult): Pair<String, String> {
        val who = if (result.role == "user") "You" else if (result.kind == "task_update") "Task update" else "Rem"
        val where = when (result.where) {
            "task" -> "In the task title"
            "file" -> "In a file name"
            "details" -> "In the details"
            "result" -> "In the result"
            else -> ""
        }
        return who to where
    }

    fun summary(state: RemSearchState): String {
        val page = state.page
        return when {
            state.query.isBlank() -> ""
            state.status == RemSearchState.Status.LOADING && page == null -> "Searching…"
            state.status == RemSearchState.Status.ERROR -> ""
            page == null || page.total == 0 -> "No matches"
            else -> (if (page.total == 1) "1 match" else "%,d matches".format(page.total)) +
                (if (page.results.size < page.total) ", showing %,d".format(page.results.size) else "")
        }
    }

    /** Earlier pages followed by the newest messages, each message once. */
    fun joined(earlier: List<RemMessage>, newest: List<RemMessage>): List<RemMessage> {
        if (earlier.isEmpty()) return newest
        val ids = newest.mapTo(HashSet()) { it.id }
        return earlier.filter { it.id !in ids } + newest
    }

    /**
     * The earlier pages to keep after rem.home sends [current]. Messages that slid out of its newest window since
     * [previous] join the pages, so nothing goes missing between them. When the window holds the whole chat, or no
     * longer overlaps what came before (such as after the history was cleared), the pages are dropped.
     */
    fun keepEarlier(earlier: List<RemMessage>, previous: RemHome?, current: RemHome): List<RemMessage> {
        if (earlier.isEmpty()) return earlier
        if (current.earlier == 0 && current.messages.isNotEmpty() || current.messages.isEmpty()) return emptyList()
        val before = previous?.messages ?: return earlier
        val ids = current.messages.mapTo(HashSet()) { it.id }
        if (before.none { it.id in ids }) return emptyList()
        val slid = before.takeWhile { it.id !in ids && it.state != "waiting" }
        val held = earlier.mapTo(HashSet()) { it.id }
        return earlier + slid.filter { it.id !in held }
    }
}
