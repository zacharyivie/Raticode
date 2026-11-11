package dev.raticode.mobile.voice

/** Turns Rem's Markdown into words worth reading aloud. Pure, so it is unit tested. */
internal object SpeechText {
    /** About 40 seconds of speech. Longer replies stop at a sentence and point to the screen. */
    const val LIMIT = 600
    const val REST = "The rest is on screen."
    private const val CODE = "There's code on screen."

    fun forSpeech(markdown: String, limit: Int = LIMIT): String {
        var text = markdown.replace("\r\n", "\n")
        // Fenced code reads badly aloud; say that it is there instead.
        text = Regex("(?s)```.*?(```|$)").replace(text) { "\n$CODE\n" }
        text = Regex("!\\[[^\\]]*]\\([^)]*\\)").replace(text, "")
        text = Regex("\\[([^\\]]+)]\\([^)]*\\)").replace(text) { it.groupValues[1] }
        text = Regex("<(https?://[^>\\s]+)>").replace(text, "a link")
        text = Regex("https?://\\S+").replace(text, "a link")
        text = Regex("<[^>\\n]+>").replace(text, "")
        text = Regex("`([^`]*)`").replace(text) { it.groupValues[1] }
        val lines = text.split('\n').mapNotNull { raw ->
            var line = raw.trim()
            // Table rules and horizontal rules carry no words.
            if (line.matches(Regex("\\|?\\s*:?-{3,}:?\\s*(\\|\\s*:?-{3,}:?\\s*)*\\|?")) || line.matches(Regex("([-*_]\\s*){3,}"))) return@mapNotNull null
            line = line.replace(Regex("^#{1,6}\\s+"), "").replace(Regex("^>\\s?"), "")
                .replace(Regex("^([-*+]|\\d+[.)])\\s+(\\[[ xX]]\\s+)?"), "")
            if (line.contains('|')) line = line.trim('|').split('|').map { it.trim() }.filter { it.isNotEmpty() }.joinToString(", ")
            line = line.replace("**", "").replace("__", "").replace("~~", "").replace(Regex("(?<![\\w*])\\*(?!\\s)|(?<!\\s)\\*(?![\\w*])"), "")
                // _emphasis_ but not snake_case names.
                .replace(Regex("(?<!\\w)_(\\S(?:[^_]*\\S)?)_(?!\\w)")) { it.groupValues[1] }
            line.trim().takeIf { it.isNotEmpty() }
        }
        // Lines without their own stop, such as list items, still get a pause.
        val joined = lines.joinToString(" ") { if (it.last() in ".!?:;,") it else "$it." }.replace(Regex("\\s+"), " ").trim()
        return shorten(joined, limit)
    }

    private fun shorten(text: String, limit: Int): String {
        if (text.length <= limit) return text
        val head = text.substring(0, limit)
        val sentence = Regex("[.!?](\\s|$)").findAll(head).lastOrNull()?.range?.first
        val cut = when {
            sentence != null && sentence >= limit / 3 -> head.substring(0, sentence + 1)
            else -> head.substringBeforeLast(' ', head).trimEnd(',', ';', ':') + "…"
        }
        return "$cut $REST"
    }
}
