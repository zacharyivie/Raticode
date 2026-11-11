package dev.raticode.mobile.live

import org.json.JSONObject

/** How the phone shows a file from the explorer, following what the desktop app opens each type with. */
enum class FileViewer { IMAGE, SVG, VIDEO, AUDIO, PDF, HTML, MARKDOWN, TEXT, OTHER }

/** One open desktop project the explorer can browse. */
data class ExplorerProject(val id: String, val name: String)
data class ExplorerProjects(val projects: List<ExplorerProject>, val more: Int)

/** A folder or file in a listing. [type] is `folder`, `file`, `link` or `other`; [locked] files may hold secrets. */
data class ExplorerEntry(val name: String, val type: String, val size: Long?, val modified: String, val locked: Boolean) {
    val folder get() = type == "folder"
}

data class ExplorerFolder(val projectId: String, val project: String, val path: String, val entries: List<ExplorerEntry>,
                          val more: Int, val hidden: Int)

/** The start of a text file the desktop read for the phone. [binary] means it isn't text. */
data class ExplorerText(val path: String, val name: String, val size: Long, val modified: String, val text: String,
                        val truncated: Boolean, val binary: Boolean)

/**
 * The phone's file explorer: read-only browsing of the desktop's open projects (`files.list`), the start of text
 * files (`files.text`), and whole files up to 10 MiB through the normal end-to-end file offer (`files.open`). The
 * phone names a project by its catalog ID and a file by its path inside it; the desktop decides what may be read.
 */
object FileExplorer {
    const val LIST = "files.list"
    const val TEXT = "files.text"
    const val OPEN = "files.open"
    val READS = setOf(LIST, TEXT)
    const val KEY = "$OPEN:"
    /** Downloads holding explorer files: `project\npath` → {file, n, size, modified}. */
    const val STATE = "explorer_files"
    private const val REMEMBERED = 200
    const val UPDATE_DESKTOP = "Update Raticode on your computer to browse its files from this phone."
    const val TOO_LARGE = "This file is larger than the 10 MB a phone can download. Open it on your computer."
    const val OFF = "To browse your computer's files, open Paired devices on your computer and turn on Use Raticode from this phone."
    const val OFFLINE = "Connect to your computer to open this file. Files you opened before still open offline."
    const val NO_ROOM = "Raticode's file space on this phone is full. Remove downloaded or sent files you no longer need, then try again."

    fun isOp(op: String) = op == LIST || op == TEXT || op == OPEN

    fun listKey(projectId: String?, path: String = "") = if (projectId == null) LIST else "$LIST:$projectId:$path"
    fun textKey(projectId: String, path: String) = "$TEXT:$projectId:$path"

    fun viewKey(op: String, params: JSONObject): String {
        val project = params.optString("project_id").ifBlank { null }
        val path = params.optString("path")
        return when (op) {
            TEXT -> textKey(project.orEmpty(), path)
            else -> listKey(project, path)
        }
    }

    fun openParams(fetch: Fetch): JSONObject = JSONObject().put("project_id", fetch.projectId).put("path", fetch.path)

    fun listParams(projectId: String?, path: String = ""): JSONObject =
        if (projectId == null) JSONObject() else JSONObject().put("project_id", projectId).put("path", path)

    fun parseView(key: String, json: JSONObject): Any = when {
        key.startsWith("$TEXT:") -> ExplorerText(json.optString("path").take(1024), json.optString("name").take(255),
            json.optLong("size").coerceAtLeast(0), json.optString("modified").take(40), json.optString("text").take(600_000),
            json.optBoolean("truncated"), json.optBoolean("binary"))
        json.has("projects") -> ExplorerProjects(json.objects("projects").take(100).map {
            ExplorerProject(it.optString("project_id").take(128), it.optString("name").take(160))
        }.filter { it.id.isNotBlank() }, json.optInt("more").coerceAtLeast(0))
        else -> ExplorerFolder(json.optString("project_id").take(128), json.optString("project").take(160), json.optString("path").take(1024),
            json.objects("entries").take(1000).mapNotNull(::entry), json.optInt("more").coerceAtLeast(0), json.optInt("hidden").coerceAtLeast(0))
    }

    private fun entry(json: JSONObject): ExplorerEntry? {
        val name = json.optString("name").take(255)
        if (name.isBlank() || '/' in name || name == "." || name == "..") return null
        val size = if (json.has("size")) json.optLong("size", -1).takeIf { it >= 0 } else null
        return ExplorerEntry(name, json.optString("type").take(10), size, json.optString("modified").take(40), json.optBoolean("locked"))
    }

    private fun JSONObject.objects(key: String): List<JSONObject> {
        val array = optJSONArray(key) ?: return emptyList()
        return (0 until array.length()).mapNotNull { array.optJSONObject(it) }
    }

    // Paths --------------------------------------------------------------------------------------------------------

    fun child(path: String, name: String) = if (path.isEmpty()) name else "$path/$name"
    fun parent(path: String) = path.substringBeforeLast('/', "")
    fun name(path: String) = path.substringAfterLast('/')

    /** The folders above and including [path], for the breadcrumb: ("", project) first. */
    fun crumbs(path: String, project: String): List<Pair<String, String>> {
        val result = mutableListOf("" to project)
        if (path.isEmpty()) return result
        var at = ""
        for (part in path.split('/')) { at = child(at, part); result += at to part }
        return result
    }

    /** Entries whose name holds every word of [query], ignoring case; folders stay first. */
    fun filter(entries: List<ExplorerEntry>, query: String): List<ExplorerEntry> {
        val words = query.trim().lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (words.isEmpty()) return entries
        return entries.filter { e -> words.all { it in e.name.lowercase() } }
    }

    // File types ---------------------------------------------------------------------------------------------------

    // The desktop's mediaKind.js lists, plus HEIC/HEIF, which phones take pictures in.
    private val IMAGES = setOf("avif", "bmp", "gif", "ico", "jpeg", "jpg", "png", "webp", "heic", "heif")
    private val VIDEOS = setOf("mp4", "m4v", "mov", "webm", "ogv", "mkv", "avi", "3gp")
    private val AUDIO = setOf("mp3", "m4a", "aac", "wav", "ogg", "oga", "opus", "flac", "aif", "aiff")
    private val MARKDOWN = setOf("md", "markdown", "mdown", "mkd")
    // Formats that aren't text, so the phone doesn't ask for their text first.
    private val BINARY = setOf("zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar", "zst", "jar", "apk", "aab", "doc", "docx", "odt", "rtf",
        "ppt", "pptx", "odp", "key", "pages", "epub", "xls", "xlsx", "ods", "numbers", "ttf", "otf", "woff", "woff2", "eot", "exe", "dll", "so",
        "dylib", "class", "o", "a", "pyc", "wasm", "sqlite", "sqlite3", "db", "bin", "iso", "dmg", "psd", "ai", "tif", "tiff", "heics", "raw",
        "cr2", "nef", "dng", "mid", "midi", "parquet", "npy", "pkl", "pt", "onnx", "safetensors", "gguf", "deb", "rpm", "msi", "keystore", "jks")

    fun extension(name: String) = FileKinds.extension(name)

    fun viewer(name: String): FileViewer = when (val ext = extension(name)) {
        in IMAGES -> FileViewer.IMAGE
        "svg" -> FileViewer.SVG
        in VIDEOS -> FileViewer.VIDEO
        in AUDIO -> FileViewer.AUDIO
        "pdf" -> FileViewer.PDF
        "html", "htm" -> FileViewer.HTML
        in MARKDOWN -> FileViewer.MARKDOWN
        in BINARY -> FileViewer.OTHER
        // Everything else opens as text, as the desktop's editor does; a file that turns out binary says so.
        else -> FileViewer.TEXT
    }

    /**
     * Whether a downloaded file opens in the app's own viewer before asking other apps: text and code with no type of
     * their own (`application/octet-stream`, which unrelated apps claim), such as `.kt`, `.py` or `.rs` files.
     */
    fun inAppFirst(name: String, mime: String) = viewer(name) == FileViewer.TEXT && mime == "application/octet-stream"

    /** Whether the phone reads the file as text from the desktop instead of downloading it. */
    fun readsText(viewer: FileViewer) = viewer in setOf(FileViewer.TEXT, FileViewer.MARKDOWN, FileViewer.HTML, FileViewer.SVG)

    /** The highlighting language for a file name, following the desktop editor's map; null for plain text. */
    fun language(name: String): String? {
        val lower = name.lowercase()
        if (lower == "dockerfile" || lower.startsWith("dockerfile.") || lower == "makefile") return "sh"
        return when (extension(name)) {
            "js", "jsx", "mjs", "cjs" -> "js"
            "ts", "tsx", "mts", "cts" -> "ts"
            "py", "pyi" -> "py"
            "kt", "kts" -> "kt"
            "java" -> "java"
            "go" -> "go"
            "rs" -> "rust"
            "sh", "bash", "zsh", "fish", "ps1" -> "sh"
            "sql" -> "sql"
            "css", "scss", "less" -> "css"
            "json", "jsonc", "jsonl", "ipynb" -> "json"
            "yaml", "yml", "toml", "ini", "cfg", "conf", "rattish", "rad" -> "yaml"
            "html", "htm", "xml", "svg", "vue" -> "html"
            "c", "h", "cc", "cpp", "hpp", "cxx", "m", "mm" -> "c"
            "cs" -> "cs"
            "rb" -> "rb"
            "php" -> "php"
            "swift" -> "swift"
            else -> null
        }
    }

    /** Short words for a size, like 4.2 MB. */
    fun size(bytes: Long): String = when {
        bytes < 1024 -> "$bytes B"
        bytes < 1024 * 1024 -> "${(bytes + 512) / 1024} KB"
        bytes < 1024L * 1024 * 1024 -> String.format(java.util.Locale.US, "%.1f MB", bytes / 1048576.0)
        else -> String.format(java.util.Locale.US, "%.1f GB", bytes / 1073741824.0)
    }

    /** "Today 14:05", "Yesterday", "Nov 3" or "Nov 3, 2025", in the phone's time zone. */
    fun modified(iso: String, now: Long = System.currentTimeMillis(), zone: java.util.TimeZone = java.util.TimeZone.getDefault()): String {
        val at = runCatching { java.time.Instant.parse(iso).toEpochMilli() }.getOrNull() ?: return ""
        val local = java.time.ZoneId.of(zone.id)
        val day = java.time.Instant.ofEpochMilli(at).atZone(local)
        val today = java.time.Instant.ofEpochMilli(now).atZone(local).toLocalDate()
        val date = day.toLocalDate()
        val us = java.util.Locale.US
        return when {
            date == today -> "Today " + day.format(java.time.format.DateTimeFormatter.ofPattern("HH:mm", us))
            date == today.minusDays(1) -> "Yesterday"
            date.year == today.year -> day.format(java.time.format.DateTimeFormatter.ofPattern("MMM d", us))
            else -> day.format(java.time.format.DateTimeFormatter.ofPattern("MMM d, yyyy", us))
        }
    }

    // Downloaded copies --------------------------------------------------------------------------------------------

    private fun cacheKey(projectId: String, path: String) = "$projectId\n$path"

    /** A finished or running download of this file as it is now (same size and time), so opening it again costs nothing. */
    fun copy(state: JSONObject, projectId: String, path: String, size: Long?, modified: String): String? {
        val entry = state.optJSONObject(STATE)?.optJSONObject(cacheKey(projectId, path)) ?: return null
        if ((size != null && entry.optLong("size", -1) != size) || (modified.isNotEmpty() && entry.optString("modified") != modified)) return null
        val id = entry.optString("file").ifBlank { return null }
        val file = state.optJSONObject("files")?.optJSONObject(id) ?: return null
        return id.takeIf { file.optString("direction") == "download" && file.optString("state") in setOf("offered", "receiving", "available") }
    }

    /** Records that [file] holds this explorer file, newest last; entries whose download is gone are dropped. */
    fun remember(state: JSONObject, projectId: String, path: String, file: String, size: Long, modified: String) {
        val map = state.optJSONObject(STATE) ?: JSONObject().also { state.put(STATE, it) }
        val files = state.optJSONObject("files")
        for (key in map.keys().asSequence().toList()) if (files?.has(map.getJSONObject(key).optString("file")) != true) map.remove(key)
        val order = map.keys().asSequence().maxOfOrNull { map.getJSONObject(it).optLong("n") } ?: 0L
        map.put(cacheKey(projectId, path), JSONObject().put("file", file).put("n", order + 1).put("size", size).put("modified", modified))
        while (map.length() > REMEMBERED) map.keys().asSequence().minByOrNull { map.getJSONObject(it).optLong("n") }?.let { map.remove(it) } ?: break
    }

    /** The download IDs explorer copies use, oldest first. */
    fun files(state: JSONObject): List<String> {
        val map = state.optJSONObject(STATE) ?: return emptyList()
        return map.keys().asSequence().map { map.getJSONObject(it) }.sortedBy { it.optLong("n") }.map { it.optString("file") }
            .filter { it.isNotBlank() }.toList()
    }

    fun forget(state: JSONObject, removed: Collection<String>) {
        val map = state.optJSONObject(STATE) ?: return
        for (key in map.keys().asSequence().toList()) if (map.getJSONObject(key).optString("file") in removed) map.remove(key)
    }

    /**
     * Drops queued explorer requests before connecting: the desktop may be older and not know them, and the open
     * screen asks again. Returns whether any were dropped.
     */
    fun dropQueued(state: JSONObject): Boolean {
        val outbox = state.optJSONArray("outbox") ?: return false
        val kept = org.json.JSONArray()
        for (i in 0 until outbox.length()) {
            val event = JSONObject(outbox.getString(i))
            val op = event.optJSONObject("payload")?.optString("op").orEmpty()
            if (event.optString("type") !in setOf("desktop.read", "desktop.action") || !isOp(op)) kept.put(outbox.getString(i))
        }
        if (kept.length() == outbox.length()) return false
        state.put("outbox", kept)
        return true
    }

    /** What opening a file is doing: waiting for the desktop, then the download it offered, or why it can't. */
    data class Fetch(val projectId: String, val path: String, val name: String, val fileId: String? = null, val error: String? = null) {
        val key get() = "$projectId\n$path"
    }
}
