package dev.raticode.mobile.live

/**
 * The words Ask Rem puts in Rem's message box when asked about a file, a change or a commit: the project, the file's
 * path inside it, the lines or commit ID, and any text picked. They only ever fill the box: the user reads them, edits
 * them and sends them, and Rem handles the message under the desktop's normal rules. A file with a secret-looking name
 * gets no words at all.
 */
object AskRem {
    /** The most selected or changed text put in a message, as for a browser selection, so the question still fits. */
    const val MAX_QUOTE = 2000

    // The desktop's rule for names that usually hold secrets (`SECRET_NAME` and `SECRET_IN_FOLDER` in gofer/ui/rem_home.py).
    // The desktop stays the authority and refuses to read these files; this keeps a phone from passing one on if an older
    // desktop sends it anyway.
    private val SECRET_NAME = Regex("credential|secret|token|oauth|password|passwd|apikey|api[_-]key|^auth\\.json$" +
        "|^\\.env(\\..*)?$|^id_[^.]+(\\.pub)?$|\\.(pem|key|p12|pfx|jks|keystore|kdbx|ppk)$" +
        "|^[._]netrc$|^\\.npmrc$|^\\.pypirc$|^\\.pgpass$|^\\.dockercfg$|^kubeconfig$" +
        "|\\.tfstate(\\.backup)?$", RegexOption.IGNORE_CASE)
    private val SECRET_IN_FOLDER = setOf(".git" to "config", ".docker" to "config.json", ".kube" to "config")

    /** Whether [path] has a name that usually holds secrets, so Ask Rem is refused for it. */
    fun refused(path: String): Boolean {
        val parts = path.trimEnd('/').split('/')
        val name = parts.last()
        return SECRET_NAME.containsMatchIn(name) || (parts.getOrElse(parts.size - 2) { "" } to name) in SECRET_IN_FOLDER
    }

    /** For text selected in the file viewer. [lines] are 1-based, or null when the phone can't tell which lines they are. */
    fun file(project: String, path: String, lines: IntRange?, selected: String): String? {
        if (refused(path)) return null
        return message("About this file: ${oneLine(path)}", project, listOf(lines?.let { "Lines: ${rangeText(it)}" }), quote(selected))
    }

    /** For one file's whole change. */
    fun diffFile(project: String, path: String, source: String, hash: String): String? {
        if (refused(path)) return null
        return message("About this change: ${oneLine(path)}", project, listOf(change(source, hash)))
    }

    /** For one hunk of a file's change: its lines in the file, and the hunk itself. */
    fun hunk(project: String, path: String, source: String, hash: String, hunk: DiffHunk): String? {
        if (refused(path)) return null
        val body = (listOf(hunk.header) + hunk.lines.map { unified(it) }).joinToString("\n")
        return message("About this change: ${oneLine(path)}", project, listOf(change(source, hash), hunkLines(hunk.header)), quote(body, "diff"))
    }

    fun commit(project: String, hash: String): String = message("About this commit: ${oneLine(hash)}", project)

    /** Which lines a hunk covers, from its `@@ -a,b +c,d @@` line. A hunk that only removes lines names them in the earlier version. */
    fun hunkLines(header: String): String? {
        val match = HUNK.find(header) ?: return null
        fun count(group: Int) = match.groupValues[group].ifEmpty { "1" }.toInt()
        val old = match.groupValues[1].toInt()
        val new = match.groupValues[3].toInt()
        return if (count(4) > 0) "Lines: ${rangeText(new until new + count(4))}"
        else "Lines: ${rangeText(old until old + count(2))} of the earlier version"
    }

    /** Lines as people say them: "7" or "7–12". */
    fun rangeText(lines: IntRange) = if (lines.first >= lines.last) "${lines.first}" else "${lines.first}–${lines.last}"

    private val HUNK = Regex("""^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@""")

    private fun change(source: String, hash: String) = when (source) {
        "staged" -> "Change: staged"
        "unstaged" -> "Change: not staged"
        else -> "Change: in commit ${oneLine(hash)}"
    }

    private fun message(title: String, project: String, details: List<String?> = emptyList(), quoted: String? = null) =
        (listOf(title, oneLine(project).takeIf { it.isNotBlank() }?.let { "Project: $it" }) + details).filterNotNull().joinToString("\n") + quoted.orEmpty()

    /** Names and hashes stay on their own line, whatever a file name holds. */
    private fun oneLine(value: String) = value.replace(Regex("\\p{Cntrl}+"), " ").trim()

    private fun unified(line: DiffLine) = when (line.kind) {
        '+' -> "+" + line.text
        '-' -> "-" + line.text
        '\\' -> "\\ " + line.text
        else -> " " + line.text
    }

    /** [text] as a block set apart from the rest, shortened if long; null when there is nothing to quote. */
    private fun quote(text: String, language: String = ""): String? {
        val clean = text.trim('\n', '\r')
        if (clean.isBlank()) return null
        var shown = clean.take(MAX_QUOTE)
        if (shown.isNotEmpty() && shown.last().isHighSurrogate()) shown = shown.dropLast(1)
        // A fence longer than any run of backticks in the text, so the text can't end the block.
        val fence = "`".repeat((Regex("`+").findAll(shown).maxOfOrNull { it.value.length } ?: 0).coerceAtLeast(2) + 1)
        val note = if (shown.length < clean.length) "\n(Shortened to the first ${String.format(java.util.Locale.US, "%,d", MAX_QUOTE)} characters.)" else ""
        return "\n\n$fence$language\n$shown\n$fence$note"
    }
}
