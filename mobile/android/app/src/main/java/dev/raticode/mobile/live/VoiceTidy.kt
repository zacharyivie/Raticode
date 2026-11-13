package dev.raticode.mobile.live

import org.json.JSONObject

/** Dictated words after the desktop tidied them, or the words as heard with [reason] saying why not. */
data class TidyResult(val text: String, val tidied: Boolean, val reason: String = "")

/**
 * Voice tidying, which the user turns on in desktop Settings. The desktop sends the heard words to a fast model and
 * answers `rem.tidy` twice: `pending` at once, then the tidied words. Nothing is stored on either device, and every
 * failure gives back the words as heard. Pure, so it is unit tested.
 */
object VoiceTidy {
    const val KEY = "rem.tidy"
    /** The desktop gives up on its model after 15 seconds; this leaves room for the trip there and back. */
    const val WAIT_MS = 25_000L
    const val OFF = "Tidying is off on your desktop."
    const val OFFLINE = "Your desktop isn't connected."
    const val SLOW = "Tidying took too long."
    const val BUSY = "Raticode is busy. Try again in a moment."
    /** The most the desktop tidies at once. Longer dictation keeps its words as heard; nothing is cut. */
    const val LIMIT = 4000
    const val TOO_LONG = "It's longer than 4,000 characters, the most your desktop tidies at once."

    /** The desktop's answer. Only a tidied answer replaces what was heard; the phone keeps its own copy otherwise. */
    fun parse(heard: String, body: JSONObject): TidyResult {
        val text = body.optString("text").trim().take(LIMIT)
        return if (body.optBoolean("tidied") && text.isNotEmpty()) TidyResult(text, true)
        else TidyResult(heard, false, body.optString("reason").trim().take(300).ifBlank { "Tidying didn't work." })
    }

    /** Words too long for the desktop to tidy come back whole and untidied, never cut; null when they can be tidied. */
    fun tooLong(heard: String): TidyResult? = if (heard.length > LIMIT) TidyResult(heard, false, TOO_LONG) else null

    /** Shown above the words when they weren't tidied. */
    fun notice(result: TidyResult): String = if (result.tidied) "" else "Not tidied: ${result.reason}"

    /**
     * The words said after [keep], or null when they don't follow it. Only these are tidied: earlier words were
     * already in the box, where the user may have edited them.
     */
    fun added(keep: String, all: String): String? {
        val kept = keep.trim()
        val words = all.trim()
        return if (kept.isEmpty()) words else words.takeIf { it.startsWith(kept) }?.removePrefix(kept)?.trim()
    }
}
