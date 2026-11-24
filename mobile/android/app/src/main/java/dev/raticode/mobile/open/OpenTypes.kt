package dev.raticode.mobile.open

import dev.raticode.mobile.live.FileExplorer
import dev.raticode.mobile.live.FileViewer

/**
 * What other apps can open with Raticode: web links, as the phone's browser ([OpenLinkActivity]), and the files Raticode
 * already shows in its own viewers ([OpenFileActivity]). The manifest's filter for OpenFileActivity lists exactly
 * [mimeTypes]; OpenTypesTest checks the two agree. Files arrive as `content://` addresses with Android's one-time read
 * grant, so Raticode needs no storage permission. `file://` addresses are refused, since reading one would.
 */
object OpenTypes {
    /** One kind of file in Settings › Open with Raticode, with the viewer that shows it. */
    data class Kind(val label: String, val viewer: FileViewer, val mimeTypes: List<String>, val detail: String)

    val kinds = listOf(
        Kind("Web pages (HTML)", FileViewer.HTML, listOf("text/html", "application/xhtml+xml"),
            "Shown without running the page's scripts, loading anything from the internet or reading other files"),
        Kind("PDF documents", FileViewer.PDF, listOf("application/pdf"), "Every page, with pinch to zoom"),
        Kind("Pictures", FileViewer.IMAGE, listOf("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/x-ms-bmp",
            "image/heic", "image/heif", "image/avif", "image/x-icon", "image/vnd.microsoft.icon"),
            "PNG, JPEG, GIF, WebP, BMP, HEIC, AVIF and icons, with zoom"),
        Kind("SVG drawings", FileViewer.SVG, listOf("image/svg+xml"), "Drawn without running scripts in the file"),
        Kind("Videos", FileViewer.VIDEO, listOf("video/mp4", "video/webm", "video/3gpp", "video/quicktime", "video/x-matroska", "video/ogg"),
            "MP4, WebM, 3GP, MOV, MKV and Ogg video"),
        Kind("Sound", FileViewer.AUDIO, listOf("audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/wav", "audio/x-wav", "audio/ogg",
            "audio/opus", "audio/flac", "audio/x-flac", "audio/aiff", "audio/x-aiff"),
            "MP3, M4A, AAC, WAV, Ogg, Opus, FLAC and AIFF"),
        Kind("Markdown", FileViewer.MARKDOWN, listOf("text/markdown", "text/x-markdown"), "Formatted, or as source"),
        Kind("Text and code", FileViewer.TEXT, listOf("text/plain", "text/csv", "text/css", "text/xml", "application/xml", "application/json",
            "text/javascript", "application/javascript", "text/x-python", "text/x-java", "text/x-kotlin", "text/x-c", "text/x-shellscript",
            "application/x-sh", "text/yaml", "text/x-yaml", "application/x-yaml", "application/toml"),
            "With line numbers and highlighting"),
    )

    val mimeTypes: List<String> = kinds.flatMap { it.mimeTypes }

    /** The most Raticode reads from one file. Bigger files are refused with a message rather than read in part. */
    const val MAX_BYTES = 50L * 1024 * 1024
    /** Text is shown up to this much; the rest is left out and the screen says so. */
    const val MAX_TEXT_BYTES = 2 * 1024 * 1024
    const val MAX_URL_LENGTH = 8192

    /**
     * The viewer for a file another app opened with Raticode, from the type it named and the file's name, or null when
     * Raticode can't show it. The named type decides, except that a Markdown file sent as plain text shows as Markdown,
     * and a generic type such as `application/octet-stream` falls back to the name.
     */
    fun viewer(mime: String?, name: String?): FileViewer? {
        val type = mime.orEmpty().substringBefore(';').trim().lowercase()
        val byType = kinds.firstOrNull { type in it.mimeTypes }?.viewer
        val byName = name?.takeIf { FileExplorer.extension(it).isNotEmpty() }?.let(FileExplorer::viewer)
        return when {
            byType == FileViewer.TEXT && byName == FileViewer.MARKDOWN -> FileViewer.MARKDOWN
            byType != null -> byType
            type.startsWith("text/") -> FileViewer.TEXT
            type.isEmpty() || type == "application/octet-stream" -> byName?.takeIf { it != FileViewer.OTHER }
            else -> null
        }
    }

    /**
     * A web address another app asked Raticode to open as the phone's browser, or null. Only http and https addresses
     * with a host, without spaces or control characters, up to [MAX_URL_LENGTH].
     */
    fun webLink(url: String?): String? {
        val text = url?.trim().orEmpty()
        if (text.isEmpty() || text.length > MAX_URL_LENGTH || text.any { it.isWhitespace() || it.isISOControl() }) return null
        val lower = text.lowercase()
        val rest = when {
            lower.startsWith("https://") -> text.substring(8)
            lower.startsWith("http://") -> text.substring(7)
            else -> return null
        }
        val host = rest.substringBefore('/').substringBefore('?').substringBefore('#').substringAfterLast('@')
        return text.takeIf { host.isNotEmpty() && !host.startsWith(":") }
    }

    /** Only `content://` files: they come with a read grant. A `file://` path would need a storage permission. */
    fun readableScheme(scheme: String?) = scheme.equals("content", ignoreCase = true)

    /** A file another app shares: content only, and never one of Raticode's own providers, whose
     *  cache (opened files, camera shots, Developer log hand-offs) this lock-exempt screen must not show. */
    fun readableFrom(scheme: String?, authority: String?, ownPackage: String) =
        // Android opens `content://0@x/...` from provider x: it drops everything up to the last '@'
        // (ContentProvider.getAuthorityWithoutUserId), so the check must compare what Android will open.
        readableScheme(scheme) && authority?.substringAfterLast('@')?.startsWith(ownPackage) != true

    /** Which of Raticode's own browsers takes links from other apps. Raticode (chromium) unless the user picked Raticode (gecko). */
    fun linkEngine(value: String?): String =
        if (value == dev.raticode.mobile.browser.BrowserRules.ENGINE_GECKO) dev.raticode.mobile.browser.BrowserRules.ENGINE_GECKO
        else dev.raticode.mobile.browser.BrowserRules.ENGINE_RATICODE
}
