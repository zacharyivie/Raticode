package dev.raticode.mobile.voice

import android.text.InputType

/**
 * Text Android read from the app on screen when Rem was opened over it. It lives in memory only and goes with the
 * next message, then is forgotten. [at] uses the voice controller's clock.
 */
data class ScreenContext(val app: String, val text: String, val at: Long)

/** What voice mode knows about the screen for the current turn. */
sealed interface ScreenShare {
    data object None : ScreenShare
    data class Ready(val screen: ScreenContext) : ScreenShare
    data class Unavailable(val reason: String) : ScreenShare
}

/** Rules for screen text, kept apart from Android's types so they are unit tested. */
internal object ScreenText {
    /** Enough for a page of text; the desktop accepts up to 8,000. */
    const val MAX_CHARS = 6000
    /** Screen text older than this isn't sent: the user has likely moved on. */
    const val MAX_AGE_MS = 5 * 60_000L
    const val LOCKED = "Screen not shared: the phone was locked. Unlock, then open Rem over the app."
    const val BLOCKED = "Screen not shared: this app keeps its screen private, or Android's “Use text from screen” is off."
    const val EMPTY = "Screen not shared: no text was found on it."
    const val DESKTOP_OLD = "Screen not shared: your desktop needs an update to read it."
    const val NOT_OVER_APP = "No screen to share: open Rem over an app with the power button or assist gesture."

    private val HIDDEN = Regex("[\\u0000-\\u0008\\u000B-\\u001F\\u007F\\u200B-\\u200F\\u202A-\\u202E\\u2066-\\u2069\\uFEFF]")
    private val SPACES = Regex("[ \\t]+")

    /** Password and PIN fields: their text is never read, even when the app shows it. */
    fun secret(inputType: Int): Boolean {
        val variation = inputType and InputType.TYPE_MASK_VARIATION
        return when (inputType and InputType.TYPE_MASK_CLASS) {
            InputType.TYPE_CLASS_TEXT -> variation == InputType.TYPE_TEXT_VARIATION_PASSWORD ||
                variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD || variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD
            InputType.TYPE_CLASS_NUMBER -> variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD
            else -> false
        }
    }

    /** One tidy block: hidden characters removed, blank and repeated lines dropped, cut at [max] on a line break. */
    fun clean(lines: List<String>, max: Int = MAX_CHARS): String {
        val seen = HashSet<String>()
        val out = StringBuilder()
        for (raw in lines) {
            for (piece in raw.split('\n')) {
                val line = HIDDEN.replace(piece, "").replace(SPACES, " ").trim()
                if (line.isEmpty() || !seen.add(line)) continue
                if (out.length + line.length + 1 > max) {
                    if (out.isEmpty()) out.append(line.take(max - 1))
                    return out.append('…').toString()
                }
                if (out.isNotEmpty()) out.append('\n')
                out.append(line)
            }
        }
        return out.toString()
    }

    fun fresh(screen: ScreenContext, now: Long) = now - screen.at in 0..MAX_AGE_MS
}
