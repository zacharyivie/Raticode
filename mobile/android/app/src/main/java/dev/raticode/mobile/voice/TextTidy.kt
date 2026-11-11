package dev.raticode.mobile.voice

import java.util.Locale

/**
 * Tidies a dictated text message on the phone, with fixed rules and no model, so a text never waits for the desktop
 * and never sends its words anywhere but the person it is for. It only removes and fixes; it never adds or swaps
 * meaning words:
 *
 * - drops hesitation sounds ("um", "uh") and a word said twice in a row by stumbling ("I I", "the the");
 * - turns spoken punctuation into marks ("comma", "question mark", "new line", and "period" only at the very end);
 * - writes swear words out where the recognizer masked them and only one word fits ("f***" becomes "fuck");
 * - capitalizes sentences and "I", tidies spaces before punctuation, and ends the message with a full stop when it
 *   has none, or a question mark when it starts like a question.
 *
 * Profanity is never removed or masked. Pure, so it is unit tested.
 */
object TextTidy {
    private val FILLERS = Regex("(?i)(?<![\\w'])(?:um+|uh+|uhm+|erm+|er+|hmm+|mm+)(?![\\w'])[,.]?")
    // Only short words people stumble on; "no no no" or "very very" are said on purpose and stay.
    private val STUMBLE = Regex("(?i)\\b(i|the|a|an|to|and|it|we|you|is|that|but|so|my|in|on|of)( \\1\\b)+")
    private val SPOKEN = listOf(
        Regex("(?i) ?\\bquestion mark\\b") to "?",
        Regex("(?i) ?\\bexclamation (?:mark|point)\\b") to "!",
        Regex("(?i) ?\\bcomma\\b") to ",",
        Regex("(?i) ?\\b(?:new line|newline)\\b ?") to "\n",
        Regex("(?i) ?\\bnew paragraph\\b ?") to "\n\n",
    )
    private val END_PERIOD = Regex("(?i) ?\\b(?:period|full stop)$")
    /** Masked swear words with only one likely word, by first letter and length as recognizers mask them. */
    private val UNMASK = mapOf(
        "f***" to "fuck", "f***ing" to "fucking", "f*****g" to "fucking", "f***ed" to "fucked", "f****d" to "fucked",
        "f***er" to "fucker", "f****r" to "fucker", "s***" to "shit", "s***ty" to "shitty", "s****y" to "shitty",
        "b****" to "bitch", "b*****" to "bitchy", "a******" to "asshole", "a**hole" to "asshole", "b*******" to "bullshit",
        "m***********" to "motherfucker", "p****" to "pussy", "d******" to "dumbass",
    )
    private val MASKED = Regex("\\b([A-Za-z][A-Za-z]*\\*+[A-Za-z*]*)")
    private val QUESTION_START = Regex("(?i)^(?:who|what|when|where|why|how|which|whose|is|are|am|was|were|do|does|did|can|could|will|would|should|shall|have|has|had|may|might|wanna|want to|you (?:up|there|coming|free|good|ok|okay|home))\\b")

    fun tidy(said: String): String {
        var s = said.trim().replace(Regex("[ \\t]+"), " ")
        if (s.isEmpty()) return ""
        s = MASKED.replace(s) { m -> unmask(m.value) }
        s = FILLERS.replace(s, "")
        s = STUMBLE.replace(s) { it.groupValues[1] }
        s = END_PERIOD.replace(s, ".")
        for ((pattern, mark) in SPOKEN) s = pattern.replace(s, mark)
        // Spaces: none before marks, one after, no runs.
        s = s.replace(Regex(" +([,.!?;:])"), "$1").replace(Regex("([,.!?;:])(?=[A-Za-z])"), "$1 ")
            .replace(Regex("[ \\t]+"), " ").replace(Regex(" *\\n *"), "\n").replace(Regex(",+"), ",").trim().trim(',').trim()
        if (s.isEmpty()) return ""
        s = s.replace(Regex("\\bi\\b(?=$|[ ',.!?])")) { "I" }.replace(Regex("\\bi'(m|ll|ve|d)\\b")) { "I'" + it.groupValues[1] }
        s = capitalizeSentences(s)
        if (s.last() !in ".!?…)\"'" && !s.last().isEmoji()) s += if (QUESTION_START.containsMatchIn(lastSentence(s))) "?" else "."
        return s
    }

    private fun unmask(word: String): String {
        val lower = word.lowercase(Locale.ROOT)
        val plain = UNMASK[lower] ?: return word
        return if (word.first().isUpperCase()) plain.replaceFirstChar { it.uppercase() } else plain
    }

    private fun capitalizeSentences(s: String): String {
        val out = StringBuilder(s.length)
        var start = true
        for (c in s) {
            if (start && c.isLetter()) { out.append(c.uppercaseChar()); start = false; continue }
            out.append(c)
            if (c in ".!?\n") start = true else if (!c.isWhitespace()) start = false
        }
        return out.toString()
    }

    private fun lastSentence(s: String): String = s.split(Regex("(?<=[.!?\\n])\\s*")).lastOrNull { it.isNotBlank() }.orEmpty()

    private fun Char.isEmoji(): Boolean = Character.getType(this) == Character.SURROGATE.toInt() || Character.getType(this) == Character.OTHER_SYMBOL.toInt()
}
