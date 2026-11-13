package dev.raticode.mobile.data

import java.util.Locale

/**
 * Long text for Rem goes as a text file instead of in the message. A Rem message from the phone holds [LIMIT]
 * characters; pasting, dictating or speaking [FILE_AT] or more at once attaches the words as a `.txt` file, which the
 * desktop reads to Rem like any text attachment. Typed words stay in the message beside it. Pure, so it is unit tested.
 */
object LongText {
    /** Characters in one Rem message from the phone. */
    const val LIMIT = 4000

    /** Pasted, dictated or spoken text at least this long goes as a file, leaving room in the message to type. */
    const val FILE_AT = 2000

    /**
     * An addition this long that would take the message past [LIMIT] also goes as a file. Shorter ones are typing
     * or a keyboard's dictated phrase, which stop at the limit with the counter showing.
     */
    const val PASTE_MIN = 200

    /** The message box counts characters from here on. */
    const val COUNTER_AT = 3500

    /**
     * The most text one file takes: at most 3 bytes per character in UTF-8, so well inside the 10 MB file limit, and
     * small enough to keep in memory for putting it back in the message.
     */
    const val MAX_CHARS = 1_000_000

    /** How much of a text file the message box and voice mode show. */
    const val PREVIEW_CHARS = 140

    /** Where the words came from; the file is named after it. */
    enum class Kind(val stem: String) { PASTED("pasted-text"), SPOKEN("spoken-text"), MESSAGE("long-message") }

    /** Whether [text] is long enough to go as a file. */
    fun asFile(text: String): Boolean = text.trim().length >= FILE_AT

    /**
     * The file's name, such as `pasted-text-meeting-notes-from-tuesday.txt`: where it came from and its first words.
     * Only letters, digits and hyphens, so the desktop keeps the name as it is.
     */
    fun name(kind: Kind, text: String): String {
        val words = text.lowercase(Locale.ROOT).split(Regex("[^a-z0-9]+")).filter { it.isNotEmpty() }
        val slug = StringBuilder()
        for (word in words.take(4)) {
            val next = if (slug.isEmpty()) word else "-$word"
            if (slug.length + next.length > 32) { if (slug.isEmpty()) slug.append(word.take(32)); break }
            slug.append(next)
        }
        return if (slug.isEmpty()) "${kind.stem}.txt" else "${kind.stem}-$slug.txt"
    }

    /** The first words of [text] on one line, cut at a word and marked with an ellipsis when there is more. */
    fun preview(text: String, max: Int = PREVIEW_CHARS): String {
        val flat = text.trim().replace(Regex("\\s+"), " ")
        if (flat.length <= max) return flat
        val cut = flat.take(max)
        val space = cut.lastIndexOf(' ')
        return (if (space > max / 2) cut.take(space) else cut).trimEnd(',', '.', ';', ':', ' ') + "…"
    }

    /** The message box's counter, such as "3,612 / 4,000", from [COUNTER_AT] on. */
    fun counter(length: Int): String? = if (length >= COUNTER_AT) String.format(Locale.US, "%,d / %,d", length, LIMIT) else null

    fun characters(count: Int): String = String.format(Locale.US, if (count == 1) "%,d character" else "%,d characters", count)

    /** What happened to the message box after an edit. */
    sealed interface Edit {
        /**
         * The box shows [text]; [cut] characters of the addition didn't fit, and then the cursor goes to [cursor].
         * A null [cursor] keeps the edit's own cursor and selection.
         */
        data class Keep(val text: String, val cursor: Int? = null, val cut: Int = 0) : Edit
        /** The addition [file] goes as a file; the box keeps the rest, [text], with the cursor where the addition was. */
        data class ToFile(val text: String, val cursor: Int, val file: String) : Edit
    }

    /** Where [new] differs from [old]: the start and end in [new] of what was added or replaced. */
    fun added(old: String, new: String): IntRange {
        var start = 0
        val shortest = minOf(old.length, new.length)
        while (start < shortest && old[start] == new[start]) start++
        var end = 0
        while (end < shortest - start && old[old.length - 1 - end] == new[new.length - 1 - end]) end++
        return start until new.length - end
    }

    /**
     * Decides an edit of the message box from [old] to [new]. A long addition goes as a file when [canFile];
     * otherwise, or for typing, the box stops at [LIMIT] and says how much didn't fit.
     */
    fun edit(old: String, new: String, canFile: Boolean): Edit {
        if (new.length <= old.length) return if (new.length <= LIMIT) Edit.Keep(new) else Edit.Keep(new.take(LIMIT), LIMIT, new.length - LIMIT)
        val range = added(old, new)
        val addition = new.substring(range.first, range.last + 1)
        val rest = new.removeRange(range.first, range.last + 1)
        val size = addition.trim().length
        val long = size >= FILE_AT || (new.length > LIMIT && size >= PASTE_MIN)
        if (long && canFile) return Edit.ToFile(rest, range.first, addition.trim().take(MAX_CHARS))
        if (new.length <= LIMIT) return Edit.Keep(new)
        val kept = addition.take((LIMIT - rest.length).coerceAtLeast(0))
        return Edit.Keep(rest.take(range.first) + kept + rest.drop(range.first), range.first + kept.length, addition.length - kept.length)
    }

    /** [text] put back in the message box after [draft], or null when it wouldn't fit. */
    fun putBack(draft: String, text: String): String? {
        val joined = listOf(draft.trimEnd(), text.trim()).filter { it.isNotEmpty() }.joinToString("\n\n")
        return joined.takeIf { it.length <= LIMIT }
    }

    /** Why long text can't go as a file right now, or null when it can. */
    fun blocked(connected: Boolean, filesThread: Boolean, room: Int): String? = when {
        !filesThread -> "Update Raticode on your computer to send long text as a file."
        !connected -> "Connect to your computer to send long text as a file."
        room <= 0 -> "This message already carries ${AttachPlan.MAX_FILES} files, so long text can't go as another."
        else -> null
    }

    /** The note when [cut] characters didn't fit in the message box. */
    fun cutNote(cut: Int, why: String?): String =
        "Rem takes up to ${characters(LIMIT)} in a message, so the last ${characters(cut)} weren't added." + (why?.let { " $it" } ?: "")

    const val FILE_FAILED = "Couldn't prepare the text as a file, so it stayed in your message. Free some phone storage and try again."
}
