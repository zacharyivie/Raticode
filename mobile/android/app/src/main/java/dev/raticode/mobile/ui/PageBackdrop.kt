package dev.raticode.mobile.ui

/**
 * The base color a web page asks for, so the preview's web view can sit on it.
 *
 * Android's web view doesn't paint a page background that is only a gradient or image (on html or body): it shows the
 * web view's own color there, white by default. A dark report with light text, such as one of Rem's themed reports,
 * then turned into light text on white. The preview runs no JavaScript, so the color is read from the page's own CSS:
 * the html, body or :root rule's background, using its first color and following var(--name) to the custom property's
 * value. A page whose base color is a plain color paints it itself, so the color found here is hidden behind it.
 *
 * The page comes from a file the user opens, so every scan here takes time in step with the page's length: style tags,
 * comments and custom properties are found by plain scans, not by patterns that look ahead to the end of the page from
 * every unclosed tag or every dash. A page of thousands of unclosed style tags once held the phone for most of a minute.
 */
internal object PageBackdrop {
    private val background = Regex("""(?:^|;)\s*background(?:-color)?\s*:\s*([^;]+)""", RegexOption.IGNORE_CASE)
    // The parentheses are bounded so an unclosed var( or rgb( doesn't read on to the end of the value from each one.
    private val token = Regex("""var\(\s*+(--[\w-]++)[^)]{0,$PAREN_MAX}+\)|#[0-9a-fA-F]{3,8}\b|rgba?\([^)]{0,$PAREN_MAX}+\)|\b[a-zA-Z]+\b""")
    private val named = mapOf("black" to 0xFF000000.toInt(), "white" to 0xFFFFFFFF.toInt())

    /** ARGB of the page's base color, or null when its CSS doesn't name one Raticode can read. */
    fun color(html: String): Int? {
        val css = withoutComments(styleBlocks(html.take(MAX_SCAN)).joinToString("\n"))
        if (css.isBlank()) return null
        val rules = topLevelRules(css)
        val properties = rules.flatMap { (_, body) -> customProperties(body) }.toMap()
        // Later rules win, as in CSS; html before body, since a page's own html color is what Android shows.
        val declared = listOf("html", "body", ":root").firstNotNullOfOrNull { wanted ->
            rules.lastOrNull { (selectors, body) ->
                selectors.split(',').any { it.trim().lowercase() == wanted } && background.containsMatchIn(body)
            }?.let { (_, body) -> background.findAll(body).last().groupValues[1] }
        } ?: return null
        return firstColor(declared, properties, depth = 0, Budget())
    }

    /**
     * What each `<style ...>` element holds, up to its `</style>` (any case, spaces allowed before the `>`). A tag that
     * never closes ends the search: no later tag could close either.
     */
    private fun styleBlocks(html: String): List<String> {
        val blocks = mutableListOf<String>()
        var from = 0
        while (true) {
            var open = html.indexOf("<style", from, ignoreCase = true)
            // <styles> and the like aren't style tags.
            while (open >= 0 && open + 6 < html.length && isWordChar(html[open + 6])) open = html.indexOf("<style", open + 6, ignoreCase = true)
            if (open < 0) return blocks
            val tagEnd = html.indexOf('>', open + 6)
            if (tagEnd < 0) return blocks
            var close = html.indexOf("</style", tagEnd + 1, ignoreCase = true)
            var closeEnd = -1
            while (close >= 0) {
                var at = close + 7
                while (at < html.length && isSpace(html[at])) at++
                if (at < html.length && html[at] == '>') { closeEnd = at; break }
                close = html.indexOf("</style", close + 7, ignoreCase = true)
            }
            if (closeEnd < 0) return blocks
            blocks += html.substring(tagEnd + 1, close)
            from = closeEnd + 1
        }
    }

    /** [css] without its `/* ... */` comments. An unclosed one is left in, and so is everything after it. */
    private fun withoutComments(css: String): String {
        val out = StringBuilder(css.length)
        var from = 0
        while (true) {
            val open = css.indexOf("/*", from)
            val close = if (open < 0) -1 else css.indexOf("*/", open + 2)
            if (close < 0) return out.append(css, from, css.length).toString()
            out.append(css, from, open)
            from = close + 2
        }
    }

    /**
     * Each `--name: value` in one rule's declarations, in order. The name runs over letters, digits, `_` and `-`; the
     * value runs to the next `;` or `}`. A dash run with no colon after it is passed over in one step.
     */
    private fun customProperties(body: String): List<Pair<String, String>> {
        val found = mutableListOf<Pair<String, String>>()
        var start = body.indexOf("--")
        while (start >= 0) {
            var nameEnd = start + 2
            while (nameEnd < body.length && (isWordChar(body[nameEnd]) || body[nameEnd] == '-')) nameEnd++
            // Any other "--" inside the same name reaches the same end, so it can't start a property either.
            var next = nameEnd
            var colon = nameEnd
            while (colon < body.length && isSpace(body[colon])) colon++
            if (nameEnd > start + 2 && colon < body.length && body[colon] == ':') {
                val valueEnd = body.indexOfAny(charArrayOf(';', '}'), colon + 1).let { if (it < 0) body.length else it }
                if (valueEnd > colon + 1) {
                    found += body.substring(start, nameEnd) to body.substring(colon + 1, valueEnd).trim()
                    next = valueEnd
                }
            }
            start = body.indexOf("--", next)
        }
        return found
    }

    private fun isWordChar(char: Char) = char.isLetterOrDigit() || char == '_'

    private fun isSpace(char: Char) = char == ' ' || char in '\t'..'\r'

    /**
     * Selector and declarations of each rule outside any at-rule. Rules inside @media, such as a print style's white
     * page or a dark-mode override, don't say what the page shows on this screen, so they are left out.
     */
    internal fun topLevelRules(css: String): List<Pair<String, String>> {
        val rules = mutableListOf<Pair<String, String>>()
        var depth = 0
        var start = 0
        var selector = ""
        var skipping = false
        for ((index, char) in css.withIndex()) {
            when (char) {
                '{' -> {
                    if (depth == 0) {
                        selector = css.substring(start, index).trim()
                        skipping = selector.startsWith("@")
                        start = index + 1
                    }
                    depth++
                }
                '}' -> {
                    if (depth == 0) { start = index + 1; continue }
                    depth--
                    if (depth == 0) {
                        if (!skipping) rules += selector to css.substring(start, index)
                        start = index + 1
                    }
                }
                ';' -> if (depth == 0) start = index + 1
            }
        }
        return rules
    }

    /** How many value tokens one page may read: custom properties that name each other many times multiply them. */
    private class Budget(var left: Int = TOKENS_MAX)

    private fun firstColor(value: String, properties: Map<String, String>, depth: Int, budget: Budget): Int? {
        if (depth > 4) return null
        for (match in token.findAll(value)) {
            if (--budget.left < 0) return null
            val text = match.value
            val found = when {
                match.groupValues[1].isNotEmpty() -> properties[match.groupValues[1]]?.let { firstColor(it, properties, depth + 1, budget) }
                text.startsWith("#") -> hex(text)
                text.startsWith("rgb", ignoreCase = true) -> rgb(text)
                else -> named[text.lowercase()]
            }
            if (found != null) return found
        }
        return null
    }

    private fun hex(text: String): Int? {
        val digits = text.removePrefix("#")
        val full = when (digits.length) {
            3, 4 -> digits.map { "$it$it" }.joinToString("")
            6, 8 -> digits
            else -> return null
        }
        val rgb = full.take(6).toLong(16).toInt()
        val alpha = if (full.length == 8) full.substring(6).toInt(16) else 255
        // A see-through base color can't be shown as the web view's own color; leave white underneath.
        return if (alpha < 255) null else (0xFF shl 24) or rgb
    }

    private fun rgb(text: String): Int? {
        val parts = text.substringAfter('(').substringBefore(')').split(',', ' ', '/').filter { it.isNotBlank() }
        if (parts.size < 3) return null
        val channels = parts.take(3).map { part ->
            val number = part.trim().removeSuffix("%").toFloatOrNull() ?: return null
            (if (part.trim().endsWith("%")) number * 2.55f else number).toInt().coerceIn(0, 255)
        }
        val alpha = parts.getOrNull(3)?.trim()?.let { if (it.endsWith("%")) it.removeSuffix("%").toFloatOrNull()?.div(100) else it.toFloatOrNull() }
        if (alpha != null && alpha < 1f) return null
        return (0xFF shl 24) or (channels[0] shl 16) or (channels[1] shl 8) or channels[2]
    }

    /** Only the start of a page is read: styles sit in its head. */
    private const val MAX_SCAN = 256 * 1024

    /** The longest var(...) or rgb(...) read; real ones are a few dozen characters. */
    private const val PAREN_MAX = 256

    /** Real pages read a handful of tokens to find their base color. */
    private const val TOKENS_MAX = 10_000
}
