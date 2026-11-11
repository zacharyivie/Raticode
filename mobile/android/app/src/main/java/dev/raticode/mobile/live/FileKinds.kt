package dev.raticode.mobile.live

/** What kind of file an attachment is, for its tile and its preview. Matches the desktop's fileKinds.js groups. */
enum class FileKind { IMAGE, VIDEO, AUDIO, PDF, SHEET, CODE, TEXT, ARCHIVE, DOCUMENT, FILE }

/** How tapping an attachment shows it: as a picture, a video, text, or by opening it in another app. */
enum class PreviewMode { IMAGE, VIDEO, TEXT, OTHER }

/** The start of a text file and whether there is more after it. */
data class TextPreview(val text: String, val truncated: Boolean)

object FileKinds {
    const val TEXT_PREVIEW_BYTES = 256 * 1024
    private val GROUPS = listOf(
        FileKind.IMAGE to setOf("png", "jpg", "jpeg", "gif", "webp", "bmp", "avif", "ico", "svg", "heic", "heif", "tif", "tiff"),
        FileKind.VIDEO to setOf("mp4", "m4v", "mov", "webm", "ogv", "mkv", "avi", "3gp"),
        FileKind.AUDIO to setOf("mp3", "m4a", "aac", "wav", "ogg", "oga", "opus", "flac", "aif", "aiff"),
        FileKind.PDF to setOf("pdf"),
        FileKind.SHEET to setOf("csv", "tsv", "xls", "xlsx", "ods", "numbers"),
        FileKind.CODE to setOf("js", "jsx", "mjs", "cjs", "ts", "tsx", "py", "rb", "go", "rs", "java", "kt", "kts", "swift", "c", "cc", "cpp",
            "h", "hpp", "cs", "sh", "bash", "zsh", "fish", "sql", "css", "scss", "html", "htm", "vue", "php", "lua", "rattish", "json", "jsonl",
            "yaml", "yml", "toml", "xml", "diff", "patch", "ini", "cfg", "conf", "env"),
        FileKind.TEXT to setOf("txt", "md", "markdown", "log", "rst"),
        FileKind.ARCHIVE to setOf("zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar", "zst", "jar", "apk"),
        FileKind.DOCUMENT to setOf("doc", "docx", "odt", "rtf", "ppt", "pptx", "odp", "key", "pages", "epub"),
    )
    private val TEXT_SHEETS = setOf("csv", "tsv")

    fun extension(name: String): String {
        val base = name.substringAfterLast('/').substringAfterLast('\\')
        val dot = base.lastIndexOf('.')
        return if (dot > 0) base.substring(dot + 1).lowercase() else ""
    }

    fun kind(name: String): FileKind = extension(name).let { ext -> GROUPS.firstOrNull { ext in it.second }?.first ?: FileKind.FILE }

    /** The short type on a tile, such as PDF or ZIP. */
    fun label(name: String): String = extension(name).takeIf { it.matches(Regex("[a-z0-9]{1,5}")) }?.uppercase() ?: "FILE"

    /**
     * Pictures and videos this phone can show itself (the same names Rem's media previews use); text by its
     * extension; everything else opens in another app.
     */
    fun previewMode(name: String): PreviewMode = when {
        ChatMedia.kind(name) == MediaKind.IMAGE -> PreviewMode.IMAGE
        ChatMedia.kind(name) == MediaKind.VIDEO -> PreviewMode.VIDEO
        kind(name) in setOf(FileKind.TEXT, FileKind.CODE) || extension(name) in TEXT_SHEETS -> PreviewMode.TEXT
        else -> PreviewMode.OTHER
    }

    /** The start of [bytes] as UTF-8 text, or null when it looks binary. */
    fun previewText(bytes: ByteArray, limit: Int = TEXT_PREVIEW_BYTES): TextPreview? {
        if (bytes.take(minOf(bytes.size, 8192)).any { it == 0.toByte() }) return null
        val decoder = Charsets.UTF_8.newDecoder()
            .onMalformedInput(java.nio.charset.CodingErrorAction.REPLACE).onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPLACE)
        val text = decoder.decode(java.nio.ByteBuffer.wrap(bytes, 0, minOf(bytes.size, limit))).toString()
        return TextPreview(text, bytes.size > limit)
    }
}
