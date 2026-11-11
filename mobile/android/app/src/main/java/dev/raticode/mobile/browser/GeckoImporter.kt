package dev.raticode.mobile.browser

import android.app.Application
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.OpenableColumns
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import java.util.concurrent.Executors

/**
 * The import from Firefox while the user decides: what was found, what Mozilla's site has, and what is ticked. Nothing
 * changes until [run]. Files are read once into memory and never stored.
 */
internal class GeckoImporter(private val app: Application, private val gecko: GeckoBrowser) {
    var preview by mutableStateOf(GeckoImport.Preview()); private set
    /** What Mozilla's site offers, by extension ID. */
    var listings by mutableStateOf<Map<String, GeckoRules.Listing>>(emptyMap()); private set
    /** Extension IDs Mozilla's site has answered about, so "not found" isn't shown while still asking. */
    var checked by mutableStateOf<Set<String>>(emptySet()); private set
    var busy by mutableIntStateOf(0); private set
    var error by mutableStateOf<String?>(null)
    /** Extension IDs and setting keys the user unticked. */
    var unticked by mutableStateOf<Set<String>>(emptySet()); private set
    var favorites by mutableStateOf(true)
    /** Firefox installed on this phone, looked up each time the import opens. */
    var phoneFirefox by mutableStateOf<FirefoxApps.Found?>(null); private set
    /** The user started the handover from Firefox and it hasn't arrived. The clipboard is only read while this is true. */
    var waitingForFirefox by mutableStateOf(false); private set

    private var waitingSince by mutableLongStateOf(0L)
    /** When the open handover began, on the phone's uptime clock; 0 when none is open. All the screen's saved state keeps of it. */
    val handoverStartedAt get() = if (waitingForFirefox) waitingSince else 0L
    private var readingClipboard = false
    private val clipboard get() = app.getSystemService(ClipboardManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private val io = Executors.newCachedThreadPool { Thread(it, "gecko-import").apply { isDaemon = true } }

    val hasResults get() = preview.sources.isNotEmpty() || preview.extensions.isNotEmpty() || preview.notes.isNotEmpty()
    fun toggle(key: String) { unticked = if (key in unticked) unticked - key else unticked + key }
    fun reset() { preview = GeckoImport.Preview(); listings = emptyMap(); checked = emptySet(); unticked = emptySet(); favorites = true; error = null; waitingForFirefox = false }
    fun findFirefox() { phoneFirefox = FirefoxApps.find(app) }

    /** Whether an extension can be added: Mozilla's site has it and it isn't here already. */
    fun addable(id: String) = id in listings && gecko.extensions.none { it.id == id }

    fun addFiles(uris: List<Uri>) {
        if (uris.isEmpty()) return
        busy++
        io.execute {
            val results = uris.map { uri ->
                val name = displayName(uri)
                val why = GeckoImport.refused(name)
                when {
                    why != null -> GeckoImport.Preview(notes = listOf(GeckoImport.Note(name, why)))
                    else -> read(uri)?.let { GeckoImport.read(name, it, gecko.saved.settings, gecko.saved.currentProfile) }
                        ?: GeckoImport.Preview(notes = listOf(GeckoImport.Note(name, "It couldn't be read, or it is too large.")))
                }
            }
            main.post { busy--; results.forEach(::add) }
        }
    }

    private fun displayName(uri: Uri): String = runCatching {
        app.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { if (it.moveToFirst()) it.getString(0) else null }
    }.getOrNull() ?: uri.lastPathSegment?.substringAfterLast('/') ?: "File"

    private fun read(uri: Uri): String? = runCatching {
        app.contentResolver.openInputStream(uri)!!.use { input ->
            val out = java.io.ByteArrayOutputStream()
            val buffer = ByteArray(64 * 1024)
            while (out.size() <= GeckoImport.MAX_FILE) {
                val read = input.read(buffer)
                if (read < 0) return@use out.toByteArray().decodeToString()
                out.write(buffer, 0, read)
            }
            null // Larger than any Firefox file this import reads.
        }
    }.getOrNull()

    /** Text the user pasted: a link to a public collection on Mozilla's site, or Firefox's troubleshooting data. */
    fun addText(text: String) {
        val input = text.trim()
        if (input.isEmpty()) return
        if (input.startsWith("http", ignoreCase = true)) { addCollection(input); return }
        add(GeckoImport.read("Pasted text", input, gecko.saved.settings, gecko.saved.currentProfile))
    }

    /**
     * Starts the handover from Firefox on this phone: puts Firefox's troubleshooting address on the clipboard, for the
     * user to paste, and opens Firefox. Android gives Raticode no way to read Firefox's data itself.
     */
    fun startFromFirefox(context: Context) {
        val firefox = phoneFirefox?.takeIf { it.usable } ?: return
        error = null
        waitingSince = SystemClock.elapsedRealtime()
        // Firefox's list may be there already from a try that was cut off. Replacing it would mean doing the steps again.
        if (clipboardText()?.let(GeckoImport::isTroubleshootingData) == true) { waitingForFirefox = true; checkClipboard(); return }
        clipboard?.setPrimaryClip(ClipData.newPlainText("Firefox page", GeckoImport.SUPPORT_PAGE))
        waitingForFirefox = true
        if (!FirefoxApps.launch(context, firefox)) { waitingForFirefox = false; error = "Couldn't open Firefox." }
    }

    /** Carries on a handover after Android ended Raticode while Firefox had the screen. Returns whether it is still open. */
    fun restoreHandover(startedAt: Long): Boolean {
        if (waitingForFirefox) return true
        if (!GeckoImport.handoverOpen(startedAt, SystemClock.elapsedRealtime())) return false
        waitingSince = startedAt
        waitingForFirefox = true
        return true
    }

    fun openFirefoxAgain(context: Context) { phoneFirefox?.takeIf { it.usable }?.let { FirefoxApps.launch(context, it) } }
    fun stopWaiting() { waitingForFirefox = false; error = null }

    /**
     * Takes Firefox's troubleshooting data off the clipboard, if it is there yet. Called when Raticode gets the screen
     * back (Android only lets an app with focus read the clipboard) or when the user asks. Anything else on the
     * clipboard stays there, unread beyond telling that it isn't Firefox's.
     */
    fun checkClipboard(asked: Boolean = false) {
        if (!waitingForFirefox || readingClipboard) return
        if (!GeckoImport.handoverOpen(waitingSince, SystemClock.elapsedRealtime())) { waitingForFirefox = false; return }
        val text = clipboardText()
        if (text.isNullOrBlank() || text.trim() == GeckoImport.SUPPORT_PAGE) { if (asked) error = NOT_YET; return }
        readingClipboard = true
        busy++
        io.execute {
            val found = if (GeckoImport.isTroubleshootingData(text)) GeckoImport.read(GeckoImport.PHONE_SOURCE, text, gecko.saved.settings, gecko.saved.currentProfile) else null
            main.post {
                busy--
                readingClipboard = false
                when {
                    found == null -> { if (asked) error = NOT_YET }
                    else -> {
                        waitingForFirefox = false
                        error = null
                        clearClipboard()
                        add(found)
                        if (GeckoImport.isEmpty(found)) add(GeckoImport.Preview(notes = listOf(GeckoImport.Note("Extensions", "Firefox on this phone lists no extensions you added."))), lookUp = false)
                    }
                }
            }
        }
    }

    /** Plain text only, so a copied file or link is never opened to look at it. */
    private fun clipboardText() = runCatching { clipboard?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.text?.toString() }.getOrNull()

    /** The data holds details of the phone and of Firefox, so it doesn't stay on the clipboard once it has been read. */
    private fun clearClipboard() = runCatching {
        if (Build.VERSION.SDK_INT >= 28) clipboard?.clearPrimaryClip() else clipboard?.setPrimaryClip(ClipData.newPlainText("", ""))
    }

    private fun addCollection(link: String) {
        if (GeckoImport.collectionApi(link) == null) { error = "That isn't a link to a collection on addons.mozilla.org."; return }
        busy++
        io.execute {
            val found = ArrayList<GeckoRules.Listing>()
            var failed = false
            for (page in 1..5) {
                val text = gecko.fetch(GeckoImport.collectionApi(link, page)!!)
                if (text == null) { failed = page == 1; break }
                found += GeckoImport.parseCollection(text)
                if (!GeckoImport.hasNextPage(text)) break
            }
            main.post {
                busy--
                if (failed) { error = "Couldn't open that collection. Check the link and that the collection is public."; return@post }
                listings = listings + found.associateBy { it.id }
                checked = checked + found.map { it.id }
                add(GeckoImport.Preview(sources = listOf("Firefox Add-ons collection"), extensions = found.map { GeckoImport.FoundExtension(it.id, it.name, "", true) }), lookUp = false)
            }
        }
    }

    private fun add(part: GeckoImport.Preview, lookUp: Boolean = true) {
        preview = GeckoImport.merge(preview, part)
        unticked = unticked + part.extensions.filter { !it.active }.map { it.id } // Off in Firefox stays off unless the user ticks it.
        if (lookUp) lookUp(part.extensions.map { it.id }.filter { it !in checked })
    }

    private fun lookUp(ids: List<String>) {
        if (ids.isEmpty()) return
        busy++
        io.execute {
            var failed = false
            val found = GeckoImport.lookupUrls(ids).flatMap { url ->
                gecko.fetch(url)?.let(GeckoRules::parseSearch) ?: run { failed = true; emptyList() }
            }
            main.post {
                busy--
                listings = listings + found.associateBy { it.id }
                if (failed) error = "Couldn't reach Firefox Add-ons. Check the connection and try again." else checked = checked + ids
                preview = GeckoImport.withoutFirefoxParts(preview, listings.keys, checked)
            }
        }
    }

    /** Applies what is ticked. Each extension still shows Raticode's permission dialog before it is added. */
    fun run() {
        gecko.applyImportedSettings(preview.settings.filter { it.key !in unticked })
        val added = if (favorites) gecko.addFavorites(preview.bookmarks) else 0
        val extensions = preview.extensions.filter { it.id !in unticked && addable(it.id) }.mapNotNull { listings[it.id] }
        extensions.forEach { gecko.install(it.url) }
        gecko.notice = buildList {
            if (preview.settings.any { it.key !in unticked }) add("settings applied")
            if (added > 0) add("$added favorites added")
            if (extensions.isNotEmpty()) add("approve each extension next")
        }.joinToString(", ").replaceFirstChar { it.uppercase() }.ifBlank { "Nothing was selected." }
        reset()
    }

    private companion object {
        const val NOT_YET = "Firefox's data isn't on the clipboard yet. In Firefox, tap Copy raw data to clipboard."
    }
}
