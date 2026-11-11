package dev.raticode.mobile.share

import dev.raticode.mobile.data.FileMetadata

/**
 * The rules for what another app may share with Rem, kept free of Android so they are unit-tested.
 * Nothing here reads a file: the share screen only learns names, sizes and types until you tap Send.
 */
object ShareIntake {
    /** Rem's chat takes at most eight files a message and 4,000 characters, as in the composer. */
    const val MAX_FILES = 8
    const val MAX_TEXT = 4000
    /** Below the phone's 50 MiB file store, so a full share still leaves room for the upload. */
    const val MAX_TOTAL = 40L * 1024 * 1024

    /**
     * A shared item as Android describes it. [own] is a URI served by this app, which is never sent; [unreadable]
     * is one whose app didn't answer in time.
     */
    data class Stream(val scheme: String?, val own: Boolean, val type: String?, val unreadable: Boolean = false)

    data class Plan(val text: String, val shortened: Boolean, val accepted: List<Int>, val refused: List<String>)

    /**
     * True for a URI served by this app. Android also resolves `user@authority` (another user's provider), so the
     * check uses the part after the last `@`. [providerPackage] names the app behind an authority, when visible;
     * when it can't be asked, the URI counts as this app's and is refused.
     */
    fun ownAuthority(authority: String?, ownPackage: String, providerPackage: (String) -> String?): Boolean {
        val name = authority?.substringAfterLast('@')?.takeIf { it.isNotEmpty() } ?: return false
        if (name == ownPackage || name.startsWith("$ownPackage.")) return true
        return runCatching { providerPackage(name) == ownPackage }.getOrDefault(true)
    }

    /** Types the manifest offers to receive. Android may still report another type for a file, which is refused. */
    fun supported(type: String?): Boolean {
        val t = type?.lowercase()?.substringBefore(';')?.trim() ?: return false
        return t.startsWith("text/") || t.startsWith("image/") || t.startsWith("video/") || t == "application/pdf"
    }

    /**
     * Browsers share a page title as the subject and the address as the text; both go in, title first.
     * Control characters and direction overrides that could disguise a link are removed.
     */
    fun text(subject: CharSequence?, text: CharSequence?): Pair<String, Boolean> {
        val body = clean(text)
        val title = clean(subject)
        val joined = if (title.isNotEmpty() && !body.contains(title)) listOf(title, body).filter { it.isNotEmpty() }.joinToString("\n\n") else body
        return if (joined.length > MAX_TEXT) joined.take(MAX_TEXT).trimEnd() to true else joined to false
    }

    private fun clean(value: CharSequence?): String = value?.toString().orEmpty()
        .filterNot { (it.isISOControl() && it != '\n' && it != '\t') || it in "\u202A\u202B\u202C\u202D\u202E\u2066\u2067\u2068\u2069" }
        .replace("\r", "").trim()

    /** Which shared items to offer for review, and a plain reason for each one left out. */
    fun plan(subject: CharSequence?, text: CharSequence?, streams: List<Stream>): Plan {
        val (body, shortened) = text(subject, text)
        val accepted = mutableListOf<Int>()
        val refused = mutableListOf<String>()
        var own = 0; var notContent = 0; var unreadable = 0; var unsupported = 0; var extra = 0
        streams.forEachIndexed { index, stream ->
            when {
                stream.scheme?.lowercase() != "content" -> notContent++
                stream.own -> own++
                stream.unreadable -> unreadable++
                !supported(stream.type) -> unsupported++
                accepted.size >= MAX_FILES -> extra++
                else -> accepted.add(index)
            }
        }
        if (unsupported > 0) refused.add(items(unsupported, "isn't a type", "aren't types") + " Rem takes from other apps: text, pictures, videos and PDFs.")
        if (unreadable > 0) refused.add(items(unreadable, "couldn't", "couldn't") + " be opened: the app that shared it didn't answer.")
        if (notContent > 0) refused.add(items(notContent, "wasn't", "weren't") + " shared through Android's file system and was left out.")
        if (own > 0) refused.add(items(own, "came", "came") + " from Raticode itself and was left out.")
        if (extra > 0) refused.add("Rem takes $MAX_FILES files at a time, so $extra more ${if (extra == 1) "was" else "were"} left out.")
        return Plan(body, shortened, accepted, refused)
    }

    private fun items(n: Int, one: String, many: String) = if (n == 1) "One item $one" else "$n items $many"

    /**
     * The name Rem sees. The desktop picks a file's type from its extension, so a name without one gets the
     * extension for its type. The phone never writes a file under this name.
     */
    fun name(display: String?, extension: String?, index: Int): String {
        val safe = FileMetadata.safeName(display.orEmpty()).let { if (it == "attachment") "shared-${index + 1}" else it }
        val ext = extension?.lowercase()?.filter { it.isLetterOrDigit() }?.take(10)?.takeIf { it.isNotEmpty() }
        return if (ext != null && !safe.substringAfterLast('/').contains('.')) "$safe.$ext".take(160) else safe
    }

    /** Why a reviewed file can't go, or null when it can. Sizes a provider doesn't report are checked while reading. */
    fun sizeProblem(size: Long?): String? = if (size != null && size > FileMetadata.MAX_FILE) "Larger than 10 MB, so Rem can't take it." else null

    /** Why the reviewed set can't go together, or null when it can. */
    fun totalProblem(sizes: List<Long?>): String? =
        if (sizes.sumOf { it ?: 0L } > MAX_TOTAL) "These files add up to more than 40 MB. Remove some, then send." else null
}
