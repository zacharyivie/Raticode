package dev.raticode.mobile.live

/**
 * Links in desktop messages. Web addresses open in the phone's browser. File links name a
 * file on the paired desktop; tapping one asks the desktop for it with the `files.link`
 * action, which runs no model. The phone sends the link exactly as the message has it plus
 * the message it came from, and the desktop resolves and checks it. The phone never builds
 * a path itself.
 */
sealed interface ChatLink {
    data class Web(val url: String) : ChatLink
    /** [destination] is the link text exactly as written in the message. */
    data class File(val destination: String, val name: String) : ChatLink
}

/**
 * The message a link came from. [kind] is `rem` for Rem's chat (no thread), `thread` for a
 * shared thread and `rem_thread` for a Rem work thread. [messageId] is the desktop's message ID.
 */
data class LinkSource(val kind: String, val threadId: String?, val messageId: String) {
    init {
        require(kind in KINDS && messageId.isNotEmpty() && messageId.length <= 200)
        require((kind == "rem") == (threadId == null))
    }
    companion object {
        val KINDS = setOf("rem", "thread", "rem_thread")
        /** Null when the message can't be named to the desktop; its file links are then shown but not fetched. */
        fun of(kind: String, threadId: String?, messageId: String): LinkSource? = runCatching { LinkSource(kind, threadId, messageId) }.getOrNull()
    }
}

object ChatLinks {
    const val MAX_LINK = 2048
    const val KEY = "files.link:"
    private val scheme = Regex("^[A-Za-z][A-Za-z0-9+.-]*:")
    private val drive = Regex("^[A-Za-z]:[\\\\/]")
    // `src/a.py:12` is a file and line, not a scheme. Same rule as desktop fileLinks.js.
    private val lineSuffix = Regex("(\\.[^:/?#]+):[1-9]\\d*(?::[1-9]\\d*)?$")
    private val bareUrl = Regex("https?://[^\\s<>\"'`]+")
    private val barePath = Regex("(?<![\\w/.~:-])(?:~/|/)(?:[\\w.+@-]+/)+[\\w+@-][\\w.+@-]*\\.[A-Za-z0-9]{1,10}(?::[1-9]\\d*(?::[1-9]\\d*)?)?")

    /** Classifies a Markdown link destination; null when the phone should not act on it. */
    fun classify(destination: String): ChatLink? {
        val value = destination.trim()
        if (value.isEmpty() || value.length > MAX_LINK || value.any { it.isISOControl() }) return null
        if (value.startsWith("#") || value.startsWith("//")) return null
        if (drive.containsMatchIn(value) || value.startsWith("\\\\")) return ChatLink.File(value, fileName(value))
        val schemeFree = value.split('?', '#').first().replace(lineSuffix, "$1")
        val found = scheme.find(schemeFree)?.value?.dropLast(1)?.lowercase()
        return when (found) {
            null -> ChatLink.File(value, fileName(value))
            "file" -> ChatLink.File(value, fileName(value))
            "http", "https" -> value.takeIf(::webAddress)?.let { ChatLink.Web(it) }
            else -> null
        }
    }

    private fun webAddress(value: String): Boolean = runCatching {
        val uri = java.net.URI(value)
        !uri.host.isNullOrBlank() && uri.rawUserInfo == null
    }.getOrDefault(false)

    /** Mentions from the desktop, bounded and longest first so a longer path wins over its own prefix. */
    fun mentions(values: List<String>): List<String> = values.asSequence()
        .filter { (it.startsWith("/") || it.startsWith("~/")) && it.length in 2..1024 && it.none(Char::isISOControl) }
        .distinct().take(32).sortedByDescending { it.length }.toList()

    /**
     * A full path the desktop found in Rem's text. The link is the text as written, which the desktop
     * reads exactly, so spaces, "#" and "?" stay part of the name.
     */
    fun mention(text: String): ChatLink.File = ChatLink.File(text, text.trimEnd('/').substringAfterLast('/').ifBlank { "File" }.take(160))

    // Like the desktop's fileMentions.js, a mention starts where a path can start, not partway into one.
    private val mentionStart = Regex("[\\s(\\[{<\"'`*_]")

    /** Where each mention appears in [text], earliest first and never overlapping; longer mentions win ties. */
    fun mentioned(text: String, mentions: List<String>): List<Pair<IntRange, ChatLink>> {
        val found = mutableListOf<Pair<IntRange, ChatLink>>()
        var cursor = 0
        while (cursor < text.length) {
            var best: Pair<Int, String>? = null
            for (mention in mentions) {
                var index = text.indexOf(mention, cursor)
                while (index > 0 && !mentionStart.matches(text[index - 1].toString())) index = text.indexOf(mention, index + 1)
                if (index >= 0 && (best == null || index < best.first)) best = index to mention
            }
            val (index, mention) = best ?: break
            found.add(index until index + mention.length to mention(mention))
            cursor = index + mention.length
        }
        return found
    }

    /** Links in a stretch of plain text: the desktop's mentions first, then addresses and paths found here. */
    fun inText(text: String, mentions: List<String>): List<Pair<IntRange, ChatLink>> {
        val found = mentioned(text, mentions).toMutableList()
        for (item in bare(text)) if (found.none { it.first.first <= item.first.last && item.first.first <= it.first.last }) found.add(item)
        return found.sortedBy { it.first.first }
    }

    /**
     * Inline code that is exactly a link: a mention, a web or file: address, or a full path. Other code,
     * such as `npm test` or `src/a.py`, stays plain.
     */
    fun inCode(code: String, mentions: List<String>): ChatLink? {
        val value = code.trim()
        if (value.isEmpty()) return null
        if (value in mentions) return mention(value)
        if (value.startsWith("http://", true) || value.startsWith("https://", true) || value.startsWith("file:", true)) return classify(value)
        return bare(value).singleOrNull()?.takeIf { it.first == 0 until value.length }?.second
    }

    /** The file's own name, for the download sheet. */
    fun fileName(destination: String): String {
        var path = destination.split('?', '#').first()
        if (path.startsWith("file:", ignoreCase = true)) path = path.substring(5)
        path = path.replace(lineSuffix, "$1")
        val decoded = runCatching { java.net.URLDecoder.decode(path.replace("+", "%2B"), "UTF-8") }.getOrDefault(path)
        return decoded.trimEnd('/', '\\').substringAfterLast('/').substringAfterLast('\\').ifBlank { "File" }.take(160)
    }

    /**
     * Web addresses and absolute file paths written as plain text, like desktop GitHub-style
     * autolinks. Each match keeps the text as written, minus trailing sentence punctuation.
     */
    fun bare(text: String): List<Pair<IntRange, ChatLink>> {
        val found = mutableListOf<Pair<IntRange, ChatLink>>()
        for (match in bareUrl.findAll(text) + barePath.findAll(text)) {
            val raw = trimTrailing(match.value)
            if (raw.isEmpty()) continue
            val range = match.range.first until match.range.first + raw.length
            if (found.any { it.first.first <= range.last && range.first <= it.first.last }) continue
            classify(raw)?.let { found.add(range to it) }
        }
        return found.sortedBy { it.first.first }
    }

    private fun trimTrailing(value: String): String {
        var end = value.length
        while (end > 0) {
            val c = value[end - 1]
            end = when {
                c in ".,;:!?*_~" -> end - 1
                c == ')' && value.substring(0, end).count { it == '(' } < value.substring(0, end).count { it == ')' } -> end - 1
                c == ']' && value.substring(0, end).count { it == '[' } < value.substring(0, end).count { it == ']' } -> end - 1
                else -> break
            }
        }
        return value.substring(0, end)
    }
}
