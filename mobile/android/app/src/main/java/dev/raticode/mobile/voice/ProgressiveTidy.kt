package dev.raticode.mobile.voice

import dev.raticode.mobile.live.TidyResult

/**
 * Tidies a dictation while the user is still talking, so voice mode shows tidied words as they come instead of
 * nothing until Send. [kept] is the text that was already in the box; only the words said after it are tidied.
 *
 * While the user keeps talking, a round is due once [WORDS] new words have built up and [GAP_MS] have passed since
 * the last round. Both have to hold, and there is no upper limit: a fast talker's round just carries more words, and
 * a slow one isn't cut up every few words. A pause of [PAUSE_MS] also sends whatever is left. Every round sends
 * everything said so far (not just the newest words), so the model can mend a sentence the last round cut in half
 * and the shown text reads as one piece. One request is out at a time: words said while it is out wait for its
 * answer, and an answer is shown only if it is for the latest request. A failed answer changes nothing on screen;
 * the words stay safe in the dictation and Send tidies them again. Pure, so it is unit tested.
 */
internal class ProgressiveTidy(kept: String, startedAt: Long = 0L) {
    private val kept = kept.trim()
    /** The words said after [kept] that the shown text tidies, and that tidied text. */
    private var tidiedFrom: String? = null
    private var tidied = ""
    /** The words of the request that is out, if one is. */
    var asking: String? = null; private set
    /** How many words the last request carried, answered or not, so the next round waits for [WORDS] more. */
    private var askedWords = 0
    /** When the last request went, or listening began, so rounds are at least [GAP_MS] apart while talking. */
    private var askedAt = startedAt
    /** Words whose round just failed; asked again only once more is said, so a failing desktop isn't looped on. */
    private var failedOn: String? = null

    /** What voice mode shows while listening: the kept text, then the latest tidied words. */
    val shown: String get() = DictationText.join(kept, tidied)

    /**
     * The words to send now, or null: nothing new was said since the shown text, the same words are already out, or
     * another request is out (it is asked again when that one answers).
     */
    fun next(fresh: String, now: Long): String? {
        val words = fresh.trim()
        if (!hasVoice(words) || words == tidiedFrom || words == failedOn || asking != null) return null
        asking = words
        askedWords = count(words)
        askedAt = now
        return words
    }

    /**
     * While talking: how long until a round is due, 0 when it is due now, or null while fewer than [WORDS] new words
     * have been said or a request is out. A pause sends a round regardless.
     */
    fun waitMs(fresh: String, now: Long): Long? {
        if (asking != null || !hasVoice(fresh) || count(fresh) - askedWords < WORDS) return null
        return (askedAt + GAP_MS - now).coerceAtLeast(0L)
    }

    /** Enough words and enough time since the last round, and none is out: tidy now, without a pause. */
    fun due(fresh: String, now: Long): Boolean = waitMs(fresh, now) == 0L

    /** An answer came for [words]. Returns whether [shown] changed. */
    fun answered(words: String, result: TidyResult): Boolean {
        // A late answer to a dropped request, with a newer one out, is stale.
        if (asking != null && asking != words) return false
        asking = null
        if (!result.tidied || result.text.isBlank()) { failedOn = words; return false }
        tidiedFrom = words
        val changed = tidied != result.text.trim()
        tidied = result.text.trim()
        return changed
    }

    /** The request was dropped, such as when listening ended; the next pause may ask again. */
    fun dropped(words: String) { if (asking == words) asking = null }

    /** The tidied words for exactly [fresh], when the last answer already covers everything said. */
    fun ready(fresh: String): String? = tidied.takeIf { tidiedFrom != null && tidiedFrom == fresh.trim() }

    companion object {
        /**
         * New words that make a round worth asking for while the user keeps talking: about a sentence or two, so
         * the shown text keeps up without a request every few words.
         */
        const val WORDS = 15

        /** At least this long between rounds while talking, so a fast talker's sentence isn't cut up too often. */
        const val GAP_MS = 5_000L

        /** Actual words, not empty or punctuation-only recognizer output; only these are ever tidied. */
        fun hasVoice(text: String): Boolean = text.any { it.isLetterOrDigit() }

        fun count(text: String): Int = text.split(Regex("\\s+")).count { word -> word.any { it.isLetterOrDigit() } }

        /**
         * Quiet for this long counts as a pause worth tidying at. Shorter gaps fall between words and sentences;
         * two seconds is someone stopping to think.
         */
        const val PAUSE_MS = 2_000L
    }
}
