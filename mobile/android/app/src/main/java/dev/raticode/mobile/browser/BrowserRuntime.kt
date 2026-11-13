package dev.raticode.mobile.browser

import android.annotation.SuppressLint
import android.app.Activity
import android.app.Application
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.MutableContextWrapper
import android.graphics.Bitmap
import android.net.Uri
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.Message
import androidx.core.net.toUri
import android.view.View
import android.view.ViewGroup
import android.print.PrintManager
import android.webkit.CookieManager
import android.webkit.GeolocationPermissions
import android.webkit.HttpAuthHandler
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.WebViewDatabase
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.webkit.ProfileStore
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import androidx.webkit.WebViewRenderProcess
import androidx.webkit.WebViewRenderProcessClient
import dev.raticode.mobile.live.BrowserAddress
import java.util.UUID

/**
 * The phone browser's tabs and their Android WebViews, one per process so tabs and their pages survive moving between
 * the app's screens. Composables read the state here and call these methods; no transport or desktop code lives here.
 *
 * Every page runs in the system WebView with JavaScript and site storage, and with: no file or content access, no
 * JavaScript bridge to the app, mixed content blocked, third-party cookies off, Safe Browsing on, no usage metrics
 * (manifest), and pop-ups only from a tap. Files a page builds itself (blob: and data: links) are read in the page,
 * without a bridge, by [PageFiles]. Camera, microphone and location
 * requests are refused, since the app holds those permissions for its own features and pages shouldn't inherit them.
 * At most [MAX_LIVE] tabs keep a live page; the rest sleep with their back/forward list saved in memory and reload
 * when shown, like the desktop's paused tabs.
 */
@SuppressLint("SetJavaScriptEnabled")
internal class BrowserRuntime private constructor(private val app: Application) {
    companion object {
        const val MAX_LIVE = 3
        /** When to ask a full-screen page for its video's size, after it went full screen. */
        private val SIZE_CHECKS_MS = longArrayOf(300L, 1500L)
        private const val PRIVATE_PROFILE_PREFIX = "raticode-private-"
        /** How often pages are asked whether they play sound while Raticode is away and a page plays. */
        const val SOUND_CHECK_MS = 4000L
        /** The same question while Raticode is in front, where the media bar shows the answer. */
        const val MEDIA_CHECK_MS = 2000L
        @Volatile private var instance: BrowserRuntime? = null
        fun get(context: Context): BrowserRuntime = instance ?: synchronized(this) {
            instance ?: BrowserRuntime(context.applicationContext as Application).also { instance = it; BrowserMedia.attach(context) }
        }
        /** For tests and reset: forgets the runtime without touching saved data. */
        internal fun peek(): BrowserRuntime? = instance
    }

    /** What a tab shows right now. Only the tab list and addresses are saved; the rest lasts while the app runs. */
    data class Page(
        val url: String = BrowserRules.HOME,
        val title: String = "",
        val progress: Int = 0,
        val loading: Boolean = false,
        val canBack: Boolean = false,
        val canForward: Boolean = false,
        val problem: Problem? = null,
        val desktop: Boolean = false,
        /** Counts automatic reloads after the page's process ended, so the page on screen draws the new one. */
        val revived: Int = 0,
    )

    /** A page that couldn't show: why, in plain words, and what the user can do. */
    data class Problem(val kind: Kind, val title: String, val detail: String, val url: String) {
        enum class Kind { OFFLINE, NOT_FOUND, TIMEOUT, REFUSED, CERTIFICATE, INSECURE, CRASHED, BLOCKED, OTHER }
    }

    /** One question a page or link asks the user. Only the tab on screen may ask; others are answered "no". */
    sealed interface Ask {
        val tab: String
        data class Alert(override val tab: String, val host: String, val message: String, val result: JsResult) : Ask
        data class Confirm(override val tab: String, val host: String, val message: String, val result: JsResult) : Ask
        data class Prompt(override val tab: String, val host: String, val message: String, val value: String, val result: JsPromptResult) : Ask
        data class Leave(override val tab: String, val host: String, val result: JsResult) : Ask
        data class SignIn(override val tab: String, val host: String, val realm: String, val handler: HttpAuthHandler) : Ask
        data class OpenApp(override val tab: String, val url: String) : Ask
        data class Media(override val tab: String, val host: String, val camera: Boolean, val microphone: Boolean, val request: PermissionRequest) : Ask
        /** [fromPage]: a file the page built itself (blob: or data:), already read and held until the user answers. */
        data class Download(override val tab: String, val url: String, val name: String, val mime: String, val size: Long,
                            val host: String, val risky: Boolean, val userAgent: String, val fromPage: Boolean = false) : Ask
    }

    data class LinkMenu(val tab: String, val link: String?, val image: String?)
    data class Chooser(val callback: ValueCallback<Array<Uri>>, val intent: Intent, val multiple: Boolean)
    /** Tab [tab]'s page is full screen, showing [view] in place of the page until [callback] hears it ended. */
    data class Fullscreen(val tab: String, val view: View, val callback: WebChromeClient.CustomViewCallback)
    data class Find(val query: String = "", val current: Int = 0, val total: Int = 0)

    var saved by mutableStateOf(BrowserStore.load(app)); private set
    val pages = mutableStateMapOf<String, Page>()
    var active by mutableStateOf(saved.active ?: saved.tabs.lastOrNull()?.id); private set
    var ask by mutableStateOf<Ask?>(null); private set
    var menu by mutableStateOf<LinkMenu?>(null)
    var chooser by mutableStateOf<Chooser?>(null); private set
    var fullscreen by mutableStateOf<Fullscreen?>(null); private set
    var find by mutableStateOf<Find?>(null); private set
    /** One short sentence for the snackbar: a refused permission, a blocked pop-up, a started download. */
    var notice by mutableStateOf<String?>(null)
    /** The computer's last answer to Open on your computer that the browser already showed. */
    var lastSent: Any? = null
    private val main = Handler(Looper.getMainLooper())
    private val views = LinkedHashMap<String, WebView>()
    /** The address each tab's main frame is loading, so a certificate error on a tapped link still shows. */
    private val navigating = HashMap<String, String>()
    private val sleeping = HashMap<String, Bundle>()
    private var shown = false
    private var privateProfileName: String? = null
    private var leftoversChecked = false
    /** Tabs whose page process ended. Each reloads by itself once on screen, not before its time (see [TabRecovery]). */
    private val reviveAt = HashMap<String, Long>()
    private val recovery = TabRecovery()
    /** The activity showing the browser, so page pop-ups such as `<select>` lists have a window. Never kept past detach. */
    private var host: java.lang.ref.WeakReference<Activity>? = null

    // Before init: opening the first tab there reads it.
    /** Tabs whose page is playing sound, as last checked. Their pages keep running when Raticode leaves the screen. */
    var playing by mutableStateOf<Set<String>>(emptySet()); private set

    init {
        saved.tabs.forEach { pages[it.id] = Page(url = it.url, title = it.title) }
        if (saved.tabs.isEmpty()) newTab()
    }

    val tabs get() = saved.tabs
    val page get() = active?.let { pages[it] } ?: Page()

    // Tabs ------------------------------------------------------------------------

    /** Opens a tab, showing it unless [background]. Returns its ID, or null when [BrowserRules.MAX_TABS] are open. */
    fun newTab(url: String? = null, background: Boolean = false, private: Boolean = false): String? {
        if (saved.tabs.size >= BrowserRules.MAX_TABS) {
            notice = "Close a tab to open another. Raticode keeps up to ${BrowserRules.MAX_TABS} tabs."
            return null
        }
        val id = UUID.randomUUID().toString()
        pages[id] = Page()
        update(saved.copy(tabs = saved.tabs + BrowserRules.SavedTab(id, BrowserRules.HOME, "", private)))
        if (!background || active == null) select(id)
        if (url != null) go(url, id)
        return id
    }

    /** Opens a private tab: its own cookies, storage and cache, gone once the last private tab closes. */
    fun newPrivateTab(): String? {
        if (!privateSupported) { notice = "This phone's Android System WebView is too old for private tabs. Update it in Google Play."; return null }
        return newTab(private = true)
    }

    /** Opens something a page offered in tab [from]; a private tab's links stay private. */
    fun openFrom(from: String, url: String, background: Boolean = false) = newTab(url, background, private = isPrivate(from))

    fun isPrivate(id: String?) = id != null && saved.tabs.any { it.id == id && it.private }
    val activeIsPrivate get() = isPrivate(active)
    private val privateSupported get() = runCatching { WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE) }.getOrDefault(false)

    fun select(id: String) {
        if (saved.tabs.none { it.id == id }) return
        val previous = active
        active = id
        // A tab playing sound goes on behind the others, as it does when Raticode leaves the screen.
        if (previous != id) { closeFind(); if (previous !in playing) views[previous]?.onPause() }
        // Only the tab in view can be full screen, so a page Rem or a link brings to the front ends it.
        if (VideoFullscreenRules.endsOnSelect(fullscreen?.let { VideoFullscreenRules.Video(BrowserMediaRules.Engine.CHROMIUM, it.tab) }, BrowserMediaRules.Engine.CHROMIUM, id)) exitFullscreen()
        update(saved.copy(active = id))
        if (shown) view(id)?.onResume()
    }

    fun close(id: String) {
        val index = saved.tabs.indexOfFirst { it.id == id }.takeIf { it >= 0 } ?: return
        val tab = saved.tabs[index]
        destroy(id)
        sleeping.remove(id); pages.remove(id); navigating.remove(id); reviveAt.remove(id); recovery.forget(id)
        if (ask?.tab == id) answerNo()
        val rest = saved.tabs - tab
        val closed = if (!tab.private && BrowserAddress.isWeb(tab.url)) (listOf(tab) + saved.closed).take(25) else saved.closed
        val next = if (active == id) rest.getOrNull(index.coerceAtMost(rest.size - 1))?.id else active
        update(saved.copy(tabs = rest, closed = closed, active = next))
        active = next
        if (rest.isEmpty()) newTab()
        if (tab.private && saved.tabs.none { it.private }) forgetPrivateData()
    }

    // Private tabs ----------------------------------------------------------------

    /** The WebView profile holding this private session's data. Each session gets a new one, so nothing carries over. */
    private fun privateProfile(): String = privateProfileName ?: (PRIVATE_PROFILE_PREFIX + UUID.randomUUID()).also {
        ProfileStore.getInstance().getOrCreateProfile(it)
        privateProfileName = it
    }

    private fun forgetPrivateData() {
        val name = privateProfileName ?: return
        privateProfileName = null
        deleteProfileSoon(name, 5)
    }

    /** WebView keeps a profile busy for a moment after its last page is destroyed. */
    private fun deleteProfileSoon(name: String, tries: Int) {
        main.postDelayed({
            val deleted = runCatching { ProfileStore.getInstance().deleteProfile(name) }.isSuccess
            if (!deleted && tries > 1) deleteProfileSoon(name, tries - 1)
        }, 1500)
    }

    /** Private data left behind when Android ended the app. Runs once, before the first page opens. */
    private fun removeLeftoverPrivateData() {
        if (leftoversChecked) return
        leftoversChecked = true
        if (!privateSupported) return
        runCatching {
            val store = ProfileStore.getInstance()
            store.allProfileNames.filter { it.startsWith(PRIVATE_PROFILE_PREFIX) }.forEach { name -> runCatching { store.deleteProfile(name) } }
        }
    }

    /** Sends the current page to Android's print screen, which also offers Save as PDF. */
    fun print(context: Context) {
        val view = active?.let { views[it] } ?: return
        val title = page.title.ifBlank { BrowserRules.host(page.url) }.ifBlank { "Page" }
        try {
            context.getSystemService(PrintManager::class.java).print(title, view.createPrintDocumentAdapter(title), null)
        } catch (_: RuntimeException) {
            notice = "Android couldn't print this page."
        }
    }

    fun reopenClosed(): Boolean {
        val tab = saved.closed.firstOrNull() ?: return false
        update(saved.copy(closed = saved.closed.drop(1)))
        return newTab(tab.url) != null
    }

    // Navigation --------------------------------------------------------------------

    /** The address bar: a page, a search, a secure-only notice, another app (after asking) or a refusal. */
    fun go(input: String, tab: String? = active) {
        val id = tab ?: newTab() ?: return
        when (val target = BrowserRules.resolve(input, BrowserRules.searchTemplate(saved))) {
            null -> return
            is BrowserRules.Target.Load -> load(id, target.url)
            is BrowserRules.Target.Insecure -> showInsecure(id, target.url)
            is BrowserRules.Target.App -> ask = BrowserRuntime.Ask.OpenApp(id, target.url)
            is BrowserRules.Target.Blocked -> notice = target.reason
        }
    }

    private fun load(id: String, url: String) {
        reviveAt.remove(id); recovery.forget(id)
        if (url == BrowserRules.HOME) { destroy(id); sleeping.remove(id); setPage(id) { Page() }; saveTab(id, url, ""); return }
        setPage(id) { it.copy(url = url, problem = null, loading = true, progress = 5) }
        saveTab(id, url, null)
        sleeping.remove(id)
        navigating[id] = url
        (views[id] ?: create(id)).loadUrl(url)
    }

    fun back() { val id = active ?: return; val v = views[id]; if (v?.canGoBack() == true) { setPage(id) { it.copy(problem = null) }; v.goBack() } }
    fun forward() { val id = active ?: return; val v = views[id]; if (v?.canGoForward() == true) { setPage(id) { it.copy(problem = null) }; v.goForward() } }

    /** Back and forward lists, newest last, for a long press on the arrows. */
    fun historyList(): Pair<List<Pair<String, String>>, Int> {
        val list = active?.let { views[it] }?.copyBackForwardList() ?: return emptyList<Pair<String, String>>() to -1
        return (0 until list.size).map { list.getItemAtIndex(it).let { item -> item.url to BrowserRules.title(item.title, item.url) } } to list.currentIndex
    }
    fun goBy(steps: Int) { active?.let { views[it] }?.let { if (it.canGoBackOrForward(steps)) it.goBackOrForward(steps) } }

    fun reload() {
        val id = active ?: return
        val current = pages[id] ?: return
        when (current.problem?.kind) {
            Problem.Kind.INSECURE -> setPage(id) { it.copy(problem = null) }
            // A page that kept stopping: try again from where it was, back/forward list included.
            Problem.Kind.CRASHED -> if (views[id] == null && sleeping.containsKey(id)) {
                recovery.forget(id); return setPage(id) { it.copy(problem = null, revived = it.revived + 1) }
            } else { recovery.forget(id); setPage(id) { it.copy(problem = null) }; return load(id, current.problem.url) }
            null -> {}
            else -> { setPage(id) { it.copy(problem = null) }; return load(id, current.problem.url) }
        }
        if (current.url == BrowserRules.HOME) return
        val view = views[id]
        if (view == null) load(id, current.url) else view.reload()
    }
    fun stop() { active?.let { views[it] }?.stopLoading(); active?.let { id -> setPage(id) { it.copy(loading = false) } } }

    /** A plain http address: explained on the tab, never loaded. */
    private fun showInsecure(id: String, url: String) = setPage(id) {
        it.copy(loading = false, progress = 0, problem = Problem(Problem.Kind.INSECURE, "This page isn't secure",
            "Raticode's browser opens secure (https) pages only, so nothing you type or read here can be seen on the network. " +
                "Try the secure address, or open the page in your phone's browser.", url))
    }

    fun toggleDesktopSite() {
        val id = active ?: return
        val desktop = !(pages[id]?.desktop ?: false)
        setPage(id) { it.copy(desktop = desktop) }
        views[id]?.let { view ->
            view.settings.userAgentString = if (desktop) BrowserRules.desktopUserAgent(WebSettings.getDefaultUserAgent(app)) else null
            view.reload()
        }
    }

    fun textZoom(step: Int) {
        val zoom = if (step == 0) 100 else BrowserRules.textZoom(saved.textZoom, step)
        update(saved.copy(textZoom = zoom))
        views.values.forEach { it.settings.textZoom = zoom }
    }

    // Find in page ------------------------------------------------------------------

    fun openFind() { find = Find() }
    fun findText(query: String) {
        find = Find(query)
        active?.let { views[it] }?.let { if (query.isEmpty()) it.clearMatches() else it.findAllAsync(query) }
    }
    fun findNext(forward: Boolean) { active?.let { views[it] }?.findNext(forward) }
    fun closeFind() { if (find != null) { active?.let { views[it] }?.clearMatches(); find = null } }

    // Questions -------------------------------------------------------------------------

    fun answerNo() {
        pageFile = null
        when (val current = ask) {
            is Ask.Alert -> current.result.confirm()
            is Ask.Confirm -> current.result.cancel()
            is Ask.Prompt -> current.result.cancel()
            is Ask.Leave -> current.result.cancel()
            is Ask.SignIn -> current.handler.cancel()
            is Ask.Media -> current.request.deny()
            else -> {}
        }
        ask = null
    }

    fun answerYes(value: String? = null, user: String = "", password: String = "") {
        when (val current = ask) {
            is Ask.Alert -> current.result.confirm()
            is Ask.Confirm -> current.result.confirm()
            is Ask.Prompt -> current.result.confirm(value ?: current.value)
            is Ask.Leave -> current.result.confirm()
            is Ask.SignIn -> current.handler.proceed(user, password)
            is Ask.Media -> current.request.grant(current.request.resources)
            else -> {}
        }
        ask = null
    }

    /** Opens an app link the user agreed to. Intent links lose any named component and permission grants first. */
    fun openApp(context: Context, url: String): Boolean {
        ask = null
        val intent = try {
            if (url.startsWith("intent:", ignoreCase = true)) Intent.parseUri(url, Intent.URI_INTENT_SCHEME).apply {
                component = null; selector = null
                flags = flags and (Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION or
                    Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION or Intent.FLAG_GRANT_PREFIX_URI_PERMISSION).inv()
            } else Intent(Intent.ACTION_VIEW, url.toUri())
        } catch (_: Exception) { notice = "This link to another app isn't valid."; return false }
        intent.addCategory(Intent.CATEGORY_BROWSABLE)
        if (context !is Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try { context.startActivity(intent); true } catch (_: ActivityNotFoundException) {
            val fallback = intent.getStringExtra("browser_fallback_url")?.takeIf { it.startsWith("https://") && BrowserAddress.isWeb(it) }
            if (fallback != null) { active?.let { load(it, fallback) }; true }
            else { notice = "No app on this phone opens this link."; false }
        } catch (_: SecurityException) { notice = "No app on this phone opens this link."; false }
    }

    /** Starts a download the user agreed to, through Android's download manager into Downloads. */
    fun download(context: Context) {
        val current = ask as? Ask.Download ?: return
        ask = null
        if (Build.VERSION.SDK_INT < 29) { pageFile = null; notice = "Downloads need Android 10. Open the page in your phone's browser to save it."; return }
        if (current.fromPage) { savePageFile(current); return }
        try {
            val request = DownloadManager.Request(current.url.toUri())
                .setTitle(current.name)
                .setMimeType(current.mime.ifBlank { null })
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, current.name)
                .addRequestHeader("User-Agent", current.userAgent)
            val cookies = if (isPrivate(current.tab)) privateProfileName?.let { ProfileStore.getInstance().getProfile(it)?.cookieManager } else CookieManager.getInstance()
            cookies?.getCookie(current.url)?.let { request.addRequestHeader("Cookie", it) }
            (context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
            notice = "Downloading ${current.name} to Downloads."
        } catch (_: Exception) {
            notice = "Android couldn't start this download. Open the page in your phone's browser to save it."
        }
    }

    fun chooserResult(uris: Array<Uri>?) { chooser?.callback?.onReceiveValue(uris); chooser = null }

    /** Ends full screen: the page gets its video back in place, still playing where it was, without a reload. */
    fun exitFullscreen() {
        val full = fullscreen ?: return
        fullscreen = null
        VideoFullscreen.leave(BrowserMediaRules.Engine.CHROMIUM, full.tab)
        full.callback.onCustomViewHidden()
    }

    /** The page's selected text, at most 2,000 characters, for Ask Rem. Read only when the user asks. */
    fun selection(done: (String) -> Unit) {
        val view = active?.let { views[it] } ?: return done("")
        view.evaluateJavascript("(function(){try{return String(window.getSelection()||'').slice(0,2000)}catch(e){return ''}})()") { raw ->
            done(runCatching { org.json.JSONTokener(raw).nextValue() as? String }.getOrNull().orEmpty().trim())
        }
    }

    /** Who the open page's certificate names, who issued it and until when, for the site sheet. */
    fun certificate(id: String): Triple<String, String, String>? {
        val cert = views[id]?.certificate ?: return null
        val until = cert.validNotAfterDate?.let { java.text.DateFormat.getDateInstance(java.text.DateFormat.MEDIUM).format(it) }.orEmpty()
        return Triple(cert.issuedTo?.cName.orEmpty().ifBlank { "unknown" },
            cert.issuedBy?.oName.orEmpty().ifBlank { cert.issuedBy?.cName.orEmpty() }.ifBlank { "unknown" }, until.ifBlank { "unknown" })
    }

    // Settings and data -----------------------------------------------------------------

    fun setKeepHistory(on: Boolean) = update(saved.copy(keepHistory = on, history = if (on) saved.history else emptyList()))
    fun setLinksInApp(on: Boolean) = update(saved.copy(linksInApp = on))
    fun setSearch(template: String) = update(saved.copy(search = template))
    /** [engine] becomes the default browser (for new tabs and chat links), and the Browser page shows it. */
    fun setEngine(engine: String) = update(BrowserRules.withEngine(saved, engine))
    /**
     * The Browser page shows built-in browser [engine] (Raticode (chromium) or Raticode (gecko)) without changing the default,
     * as when the user picks one of its tabs. The other browser's tabs stay open, and anything they play goes on.
     */
    fun show(engine: String) = update(BrowserRules.showing(saved, engine))
    fun setFirefoxMode(mode: String) = update(saved.copy(firefoxMode = if (mode == FirefoxApps.MODE_TAB) mode else FirefoxApps.MODE_APP))
    fun setEdgeMode(mode: String) = update(saved.copy(edgeMode = if (mode == EdgeApps.MODE_TAB) mode else EdgeApps.MODE_APP))
    fun computerSearch(template: String) { if (template != saved.computerSearch && template.isNotBlank()) update(saved.copy(computerSearch = template)) }
    fun setAppMode(engine: String, mode: String) {
        if (BrowserApps.byEngine(engine) != null) update(saved.copy(appModes = saved.appModes + (engine to BrowserApps.mode(mode))))
    }
    fun toggleBookmark() {
        val id = active ?: return
        val current = pages[id] ?: return
        update(saved.copy(bookmarks = BrowserRules.toggleBookmark(saved.bookmarks, current.url, current.title, System.currentTimeMillis())))
    }
    fun removeBookmark(url: String) = update(saved.copy(bookmarks = saved.bookmarks.filterNot { it.url == url }))
    fun removeVisit(visit: BrowserRules.Visit) = update(saved.copy(history = saved.history - visit))

    /** Clears history for [seconds] (all time when null), and when asked, every site's cookies and storage and the cache. */
    fun clear(seconds: Long?, history: Boolean, siteData: Boolean, cache: Boolean) {
        if (history) {
            update(saved.copy(history = BrowserRules.clearHistory(saved.history, System.currentTimeMillis(), seconds),
                closed = if (seconds == null) emptyList() else saved.closed))
            if (seconds == null) views.values.forEach { it.clearHistory() }
        }
        if (siteData) {
            // WebView clears cookies and site storage for all time only, as Electron does on the desktop.
            CookieManager.getInstance().removeAllCookies(null)
            CookieManager.getInstance().flush()
            WebStorage.getInstance().deleteAllData()
            WebViewDatabase.getInstance(app).clearHttpAuthUsernamePassword()
        }
        if (cache) (views.values.firstOrNull() ?: WebView(app).also { main.post { it.destroy() } }).clearCache(true)
        notice = "Cleared."
    }

    // Showing pages -----------------------------------------------------------------------

    /** The browser page is on screen ([on]) or not. Hidden pages stop their timers, as a phone browser in the background does. */
    fun visible(on: Boolean) {
        if (on == shown) return
        shown = on
        if (on) {
            views.values.firstOrNull()?.resumeTimers(); active?.let { views[it] }?.onResume(); checkSoundSoon()
            // A page Android ended while Raticode was away reloads now that it's back on screen.
            active?.takeIf { it in reviveAt }?.let { id -> setPage(id) { it.copy(revived = it.revived + 1) } }
        }
        // A full-screen video stays full screen, paused like the page, and is there again when Raticode comes back.
        else { views.values.forEach { it.onPause() }; views.values.firstOrNull()?.pauseTimers() }
    }

    // Sound -----------------------------------------------------------------------------

    private var inFront = true
    private var checking = false
    private val soundCheck = Runnable { checkSound() }

    /**
     * Raticode came to the front ([on]) or left the screen. Leaving it, pages pause as before unless one is playing
     * sound: then that page keeps playing, with [BrowserPlaybackService]'s notification, and the others pause.
     */
    fun appInFront(on: Boolean) {
        inFront = on
        if (on) { checkSoundSoon(); return }
        if (playing.isEmpty()) { visible(false); return }
        views.forEach { (id, view) -> if (id !in playing) view.onPause() }
        // Timers are shared by every page, so they keep running for the one playing; the others are paused.
        shown = false
    }

    /** The notification's Stop: every page's sound stops, and pages pause if Raticode isn't in front. */
    fun stopSound() {
        views.values.forEach { it.evaluateJavascript(BrowserMediaScript.PAUSE_ALL, null) }
        updatePlaying(emptySet())
        BrowserMedia.forget(BrowserMediaRules.Engine.CHROMIUM)
    }

    private fun mediaKey(id: String) = BrowserMediaRules.Key(BrowserMediaRules.Engine.CHROMIUM, id)

    /** Passes on what [view]'s page answered, naming its title and site from what the browser itself knows. */
    private fun report(id: String, view: WebView, answer: String?): BrowserMediaRules.Report? {
        if (views[id] !== view) return null
        val page = pages[id] ?: return null
        val report = (BrowserMediaRules.parse(answer) ?: BrowserMediaRules.Report(playing = false))
            .let { it.copy(title = BrowserMediaRules.title(it.title, page.title, page.url), site = BrowserMediaRules.site(page.url), private = isPrivate(id)) }
        BrowserMedia.report(mediaKey(id), report)
        return report
    }

    /** One of the media bar's buttons, for tab [id]. False when the tab has no live page any more. */
    fun mediaAction(id: String, action: BrowserMediaRules.Action): Boolean {
        val view = views[id] ?: return false
        // A page paused when Raticode last left the screen has to wake before it can play.
        if (action == BrowserMediaRules.Action.PLAY) { view.resumeTimers(); view.onResume() }
        view.evaluateJavascript(BrowserMediaScript.control(action)) { answer -> report(id, view, answer) }
        // play() and a page's own Next finish a moment later; look again rather than guess.
        main.postDelayed({ checkSoundSoon() }, 350)
        main.postDelayed({ checkSoundSoon() }, 1200)
        return true
    }

    private fun checkSoundSoon(delay: Long = 0) {
        main.removeCallbacks(soundCheck)
        main.postDelayed(soundCheck, delay)
    }

    private var round = 0

    private fun checkSound() {
        if (checking) return
        val live = views.toList()
        if (live.isEmpty()) { updatePlaying(emptySet()); return }
        checking = true
        val thisRound = ++round
        // A page that never answers (its process gone or stuck) mustn't stop the checks for good.
        main.postDelayed({ if (checking && round == thisRound) { checking = false; checkSoundSoon(SOUND_CHECK_MS) } }, SOUND_CHECK_MS * 3)
        val found = mutableSetOf<String>()
        var left = live.size
        live.forEach { (id, view) ->
            view.evaluateJavascript(BrowserMediaScript.STATE) { result ->
                if (round != thisRound) return@evaluateJavascript
                if (report(id, view, result)?.playing == true) found += id
                if (--left == 0) {
                    checking = false
                    updatePlaying(found)
                    // Ask again while Raticode is in front or something plays; otherwise every page is paused anyway.
                    if (views.isNotEmpty() && (inFront || playing.isNotEmpty())) checkSoundSoon(if (inFront) MEDIA_CHECK_MS else SOUND_CHECK_MS)
                }
            }
        }
    }

    private fun updatePlaying(now: Set<String>) {
        if (now == playing) return
        playing = now
        // While Raticode is away, a page that stopped playing pauses like the rest.
        if (!inFront) views.forEach { (id, view) -> if (id !in now) view.onPause() }
        // BrowserMedia, which hears from both browsers, starts and ends the playback service with its media controls.
        // Nothing plays any more while Raticode is away: pause everything, as before.
        if (now.isEmpty() && !inFront) { shown = true; visible(false) }
    }

    /** The live view for [id], waking it when it slept. Null for a blank tab, which shows the start page instead. */
    fun view(id: String): WebView? {
        views[id]?.let { touch(id); return it }
        val page = pages[id] ?: return null
        // Blank tabs show the start page; a problem shows until Reload or a new address wakes the tab.
        if (page.url == BrowserRules.HOME || page.problem != null) return null
        // A page whose process ended reloads only on screen, and a page that keeps stopping waits a little longer each time.
        reviveAt[id]?.let { at ->
            if (!shown || System.currentTimeMillis() < at) return null
            reviveAt.remove(id); recovery.restored(id)
        }
        val view = create(id)
        // Timers are shared by every page; ones paused while Raticode was away stay paused for a new page.
        if (shown) view.resumeTimers()
        val state = sleeping.remove(id)
        if (state == null || view.restoreState(state) == null) view.loadUrl(page.url)
        return view
    }

    /** Whether tab [id] has a live page now, without waking it. For tests. */
    internal fun live(id: String) = views.containsKey(id)

    /** Puts [view] into [container], showing it as part of [activity]. */
    fun attach(container: ViewGroup, view: WebView, activity: Activity?) {
        host = activity?.let { java.lang.ref.WeakReference(it) }
        (view.context as? MutableContextWrapper)?.baseContext = activity ?: app
        if (view.parent === container) return
        (view.parent as? ViewGroup)?.removeView(view)
        container.removeAllViews()
        container.addView(view, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        if (shown) view.onResume()
    }

    fun detach(container: ViewGroup) {
        for (i in 0 until container.childCount) (container.getChildAt(i) as? WebView)?.let { (it.context as? MutableContextWrapper)?.baseContext = app }
        container.removeAllViews()
        host = null
    }

    private fun touch(id: String) { views.remove(id)?.let { views[id] = it } }

    private fun create(id: String): WebView {
        removeLeftoverPrivateData()
        val view = WebView(MutableContextWrapper(host?.get() ?: app))
        if (isPrivate(id)) WebViewCompat.setProfile(view, privateProfile())
        configure(view, id)
        views[id] = view
        checkSoundSoon(SOUND_CHECK_MS)
        // Keep at most MAX_LIVE pages: the least recently shown other tab sleeps with its back/forward list.
        while (views.size > MAX_LIVE) {
            // A tab playing sound stays live, so music in a background tab doesn't stop when more tabs open.
            val oldest = views.keys.firstOrNull { it != id && it != active && it !in playing } ?: break
            views[oldest]?.let { sleeper -> Bundle().also { sleeper.saveState(it); sleeping[oldest] = it } }
            destroy(oldest)
        }
        return view
    }

    private fun destroy(id: String?) {
        val view = views.remove(id ?: return) ?: return
        if (fullscreen?.tab == id) exitFullscreen()
        pageGone(id)
        (view.parent as? ViewGroup)?.removeView(view)
        view.stopLoading()
        view.destroy()
    }

    /** Asks tab [id]'s page how big its full-screen video is, so an upright video can stay upright. */
    private fun measureFullscreen(id: String) {
        if (fullscreen?.tab != id) return
        views[id]?.evaluateJavascript(VideoFullscreenRules.SIZE_SCRIPT) { answer ->
            VideoFullscreenRules.parseSize(answer)?.let { (width, height) -> VideoFullscreen.size(BrowserMediaRules.Engine.CHROMIUM, id, width, height) }
        }
    }

    /** Told where the page in the active tab has scrolled to, in dp. */
    var scrollListener: ((Int) -> Unit)? = null

    /** A closed or crashed page never answers a sound check: end that round, and the tab no longer plays. */
    private fun pageGone(id: String) {
        BrowserMedia.forget(mediaKey(id))
        if (checking) { round++; checking = false; checkSoundSoon(SOUND_CHECK_MS) }
        if (id in playing) updatePlaying(playing - id)
    }

    private fun configure(view: WebView, id: String) {
        val density = view.resources.displayMetrics.density
        view.setOnScrollChangeListener { _, _, y, _, _ -> if (id == active) scrollListener?.invoke((y / density).toInt()) }
        view.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            setSupportMultipleWindows(true)
            javaScriptCanOpenWindowsAutomatically = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setGeolocationEnabled(false)
            builtInZoomControls = true
            displayZoomControls = false
            loadWithOverviewMode = true
            useWideViewPort = true
            mediaPlaybackRequiresUserGesture = true
            textZoom = saved.textZoom
            safeBrowsingEnabled = true
            if (pages[id]?.desktop == true) userAgentString = BrowserRules.desktopUserAgent(WebSettings.getDefaultUserAgent(app))
        }
        // Pages without a dark style are darkened when the app is dark (Android 13 and newer).
        if (Build.VERSION.SDK_INT >= 33) view.settings.isAlgorithmicDarkeningAllowed = true
        val cookies = if (isPrivate(id)) ProfileStore.getInstance().getProfile(privateProfile())!!.cookieManager else CookieManager.getInstance()
        cookies.setAcceptCookie(true)
        cookies.setAcceptThirdPartyCookies(view, false)
        view.webViewClient = PageClient(id)
        view.webChromeClient = ChromeClient(id)
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_VIEW_RENDERER_CLIENT_BASIC_USAGE)) {
            runCatching { WebViewCompat.setWebViewRenderProcessClient(view, HangClient(id)) }
        }
        view.setDownloadListener { url, agent, disposition, mime, length -> offerDownload(id, url, agent, disposition, mime, length) }
        // Keeps a page's own files readable for a minute after it revokes their addresses (see PageFiles).
        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            runCatching { WebViewCompat.addDocumentStartJavaScript(view, PageFiles.START_SCRIPT, setOf("*")) }
        }
        view.setFindListener { current, total, _ -> find?.let { find = it.copy(current = if (total == 0) 0 else current + 1, total = total) } }
        view.setOnLongClickListener { longPress(id, view) }
    }

    private fun longPress(id: String, view: WebView): Boolean {
        val hit = view.hitTestResult
        return when (hit.type) {
            WebView.HitTestResult.SRC_ANCHOR_TYPE -> { menu = LinkMenu(id, hit.extra, null); true }
            WebView.HitTestResult.IMAGE_TYPE -> { menu = LinkMenu(id, null, hit.extra); true }
            WebView.HitTestResult.SRC_IMAGE_ANCHOR_TYPE -> {
                val image = hit.extra
                val reply = Handler(Looper.getMainLooper()) { message ->
                    menu = LinkMenu(id, message.data.getString("url"), image); true
                }
                view.requestFocusNodeHref(reply.obtainMessage())
                true
            }
            else -> false
        }
    }

    private fun offerDownload(id: String, url: String, agent: String, disposition: String?, mime: String?, length: Long) {
        if (id != active) return
        if (PageFiles.isPageFile(url)) { offerPageFile(id, url, disposition, mime); return }
        if (!url.startsWith("https://") || !BrowserRules.pageAddress(url)) {
            notice = "Raticode's browser saves files only from secure (https) pages. Open the page in your phone's browser to save it."
            return
        }
        val name = BrowserRules.safeName(URLUtil.guessFileName(url, disposition, mime))
        offer(Ask.Download(id, url, name, mime.orEmpty(), length, BrowserRules.host(url), BrowserRules.risky(name, mime), agent))
    }

    /** Shows [next] unless another question is open; a question is never replaced while the user may be tapping it. */
    private fun offer(next: Ask): Boolean {
        val shown = BrowserRules.nextQuestion(ask, next)
        if (shown == null) { notice = "Answer the open question first, then try again."; return false }
        ask = shown
        return true
    }

    // Files a page builds itself (see PageFiles) ---------------------------------

    /** The file the page built, held in memory until the user answers its question; never written anywhere else. */
    private var pageFile: Pair<String, ByteArray>? = null
    private var pageFileToken: String? = null

    private fun offerPageFile(id: String, url: String, disposition: String?, mime: String?) {
        val view = views[id] ?: return
        val pageUrl = view.url ?: pages[id]?.url.orEmpty()
        val host = PageFiles.host(url, pageUrl)
        if (!PageFiles.allowed(url, pageUrl)) { notice = "This page's file can't be saved here. Open the page in your phone's browser to save it."; return }
        if (url.startsWith("data:", ignoreCase = true)) {
            val file = PageFiles.parseDataUrl(url)
            if (file == null) { notice = "This file is damaged or larger than ${BrowserRules.size(PageFiles.MAX_BYTES.toLong())}, so it can't be saved."; return }
            view.evaluateJavascript(PageFiles.nameScript(url)) { raw ->
                offerPageBytes(id, PageFiles.fileName(jsText(raw), disposition, url, file.mime), file.mime, file.bytes, host)
            }
            return
        }
        val token = UUID.randomUUID().toString()
        pageFileToken = token
        view.evaluateJavascript(PageFiles.readScript(token, url)) { started ->
            if (jsText(started) == "started") pollPageFile(id, view, token, disposition, mime, host, System.currentTimeMillis() + PageFiles.READ_TIMEOUT_MS)
            else pageFileFailed(id, token)
        }
    }

    private fun pollPageFile(id: String, view: WebView, token: String, disposition: String?, mime: String?, host: String, deadline: Long) {
        if (pageFileToken != token || views[id] !== view) return
        view.evaluateJavascript(PageFiles.pollScript(token)) { raw ->
            val report = runCatching { org.json.JSONObject(raw) }.getOrNull()
            when (report?.optString("state")) {
                "reading" -> if (System.currentTimeMillis() < deadline) main.postDelayed({ pollPageFile(id, view, token, disposition, mime, host, deadline) }, 100)
                    else pageFileFailed(id, token)
                "big" -> { view.evaluateJavascript(PageFiles.forgetScript(token), null); pageFileToken = null
                    notice = "This file is larger than ${BrowserRules.size(PageFiles.MAX_BYTES.toLong())}. Open the page in your phone's browser to save it." }
                "ready" -> {
                    val ready = PageFiles.ready("ready", report.optString("type"), report.optLong("size", -1), report.optString("name"), report.optLong("length", -1))
                    if (ready == null) pageFileFailed(id, token) else readPageChunks(id, view, token, ready, ArrayList(), disposition, mime, host)
                }
                else -> pageFileFailed(id, token)
            }
        }
    }

    private fun readPageChunks(id: String, view: WebView, token: String, ready: PageFiles.Ready, chunks: ArrayList<String>,
                               disposition: String?, mime: String?, host: String) {
        if (pageFileToken != token || views[id] !== view) return
        val from = chunks.sumOf { it.length }
        if (from >= ready.length) {
            view.evaluateJavascript(PageFiles.forgetScript(token), null)
            pageFileToken = null
            val bytes = PageFiles.decode(ready, chunks) ?: return pageFileFailed(id, null)
            val type = ready.type.substringBefore(';').trim().ifBlank { mime.orEmpty() }
            offerPageBytes(id, PageFiles.fileName(ready.name, disposition, "", type), type, bytes, host)
            return
        }
        val length = minOf(PageFiles.CHUNK, ready.length - from)
        view.evaluateJavascript(PageFiles.chunkScript(token, from, length)) { raw ->
            val chunk = jsText(raw)
            if (chunk == null || chunk.length != length) pageFileFailed(id, token)
            else readPageChunks(id, view, token, ready, chunks.apply { add(chunk) }, disposition, mime, host)
        }
    }

    private fun pageFileFailed(id: String, token: String?) {
        if (token != null) { views[id]?.evaluateJavascript(PageFiles.forgetScript(token), null); if (pageFileToken == token) pageFileToken = null }
        notice = "The page's file couldn't be read. Try the page's download button again, or open the page in your phone's browser."
    }

    private fun offerPageBytes(id: String, name: String, mime: String, bytes: ByteArray, host: String) {
        if (id != active || saved.tabs.none { it.id == id }) return
        if (offer(Ask.Download(id, "", name, mime, bytes.size.toLong(), host, BrowserRules.risky(name, mime), "", fromPage = true))) pageFile = id to bytes
    }

    private fun savePageFile(current: Ask.Download) {
        val bytes = pageFile?.takeIf { it.first == current.tab }?.second
        pageFile = null
        if (bytes == null || Build.VERSION.SDK_INT < 29) { notice = "${current.name} couldn't be saved."; return }
        val name = current.name
        Thread {
            val saved = Build.VERSION.SDK_INT >= 29 && runCatching {
                val values = android.content.ContentValues().apply {
                    put(android.provider.MediaStore.Downloads.DISPLAY_NAME, name)
                    if (current.mime.isNotEmpty()) put(android.provider.MediaStore.Downloads.MIME_TYPE, current.mime)
                }
                val uri = app.contentResolver.insert(android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)!!
                app.contentResolver.openOutputStream(uri)!!.use { it.write(bytes) }
            }.isSuccess
            main.post { notice = if (saved) "Saved $name to Downloads." else "$name couldn't be saved." }
        }.start()
    }

    /** A string evaluateJavascript returned, or null for anything else. */
    private fun jsText(raw: String?): String? = runCatching { org.json.JSONTokener(raw ?: "null").nextValue() as? String }.getOrNull()

    private fun setPage(id: String, change: (Page) -> Page) { pages[id] = change(pages[id] ?: Page()) }

    private fun saveTab(id: String, url: String, title: String?) {
        // The page itself keeps its full address; the saved list keeps a short one (see BrowserRules.storable).
        val stored = BrowserRules.storable(url)
        val tabs = saved.tabs.map { if (it.id == id) it.copy(url = stored, title = title ?: it.title) else it }
        if (tabs != saved.tabs) update(saved.copy(tabs = tabs))
    }

    private fun update(next: BrowserRules.Saved) {
        if (next == saved) return
        saved = next
        BrowserStore.save(app, next)
    }

    private fun refreshNav(id: String, view: WebView) = setPage(id) { it.copy(canBack = view.canGoBack(), canForward = view.canGoForward()) }

    private fun visited(id: String, url: String, title: String?) {
        if (url == BrowserRules.HOME || url.startsWith("data:")) return
        saveTab(id, url, title)
        if (saved.keepHistory && !isPrivate(id)) update(saved.copy(history = BrowserRules.record(saved.history, url, title, System.currentTimeMillis())))
    }

    /**
     * Tab [id]'s page process ended: Android freed its memory while Raticode was away, it crashed, or [HangClient] ended
     * it. Nothing is shown. The back/forward list lives outside the page's process, so it's kept, and the tab reloads by
     * itself once it's on screen. A page that keeps stopping gets a quiet Reload button instead (see [TabRecovery]).
     */
    private fun pageDied(id: String, view: WebView) {
        val url = pages[id]?.url ?: BrowserRules.HOME
        val state = runCatching { Bundle().takeIf { view.saveState(it) != null } }.getOrNull()
        if (views[id] === view) views.remove(id)
        if (fullscreen?.tab == id) runCatching { exitFullscreen() }
        pageGone(id)
        (view.parent as? ViewGroup)?.removeView(view)
        view.destroy()
        if (state != null) sleeping[id] = state
        when (val decision = recovery.gone(id)) {
            is TabRecovery.Decision.Restore -> {
                reviveAt[id] = System.currentTimeMillis() + decision.delayMs
                setPage(id) { it.copy(loading = false, progress = 0, problem = null, revived = it.revived + 1) }
                if (decision.delayMs > 0) main.postDelayed({ if (id in reviveAt) setPage(id) { it.copy(revived = it.revived + 1) } }, decision.delayMs)
            }
            TabRecovery.Decision.GiveUp -> {
                reviveAt.remove(id)
                setPage(id) { it.copy(loading = false, problem = Problem(Problem.Kind.CRASHED, "This page keeps stopping",
                    "Raticode reloaded it ${TabRecovery.MAX_RESTORES} times. Reload to try again.", url)) }
            }
        }
    }

    /**
     * A page that stops answering taps. Pages that aren't on screen are left alone, with nothing shown; the one on screen
     * gets [TabRecovery.HANG_GRACE_MS] and is then ended, which reloads it (see [pageDied]). Every Raticode (chromium) tab
     * shares one page process, so nothing is ended while a page plays sound or a page file is being read.
     */
    private inner class HangClient(private val id: String) : WebViewRenderProcessClient() {
        override fun onRenderProcessUnresponsive(view: WebView, renderer: WebViewRenderProcess?) {
            val inView = id == active && shown && inFront
            if (recovery.unresponsive(id, inView, busy = playing.isNotEmpty() || pageFileToken != null) == TabRecovery.Hang.END) renderer?.terminate()
        }

        override fun onRenderProcessResponsive(view: WebView, renderer: WebViewRenderProcess?) { recovery.responsive(id) }
    }

    private inner class PageClient(private val id: String) : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            if (!request.isForMainFrame) return false
            return when (val target = BrowserRules.classify(request.url.toString())) {
                is BrowserRules.Target.Load -> { navigating[id] = target.url; false }
                is BrowserRules.Target.Insecure -> { showInsecure(id, target.url); true }
                is BrowserRules.Target.App -> {
                    // A page can only offer another app after a tap, and the user still has to agree.
                    if (request.hasGesture() && id == active) offer(Ask.OpenApp(id, target.url))
                    else if (id == active) notice = "Raticode's browser stopped this page from opening another app."
                    true
                }
                is BrowserRules.Target.Blocked -> { if (id == active) notice = target.reason; true }
            }
        }

        override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
            setPage(id) { it.copy(url = url, loading = true, problem = null, progress = maxOf(it.progress, 5)) }
            refreshNav(id, view)
            // A new document: whatever was playing is gone, and its track buttons need noting from the start.
            BrowserMedia.forget(mediaKey(id))
            view.evaluateJavascript(BrowserMediaScript.HOOK, null)
        }

        override fun onPageFinished(view: WebView, url: String) {
            setPage(id) { it.copy(url = url, loading = false, progress = 100, title = view.title.orEmpty()) }
            refreshNav(id, view)
            visited(id, url, view.title)
        }

        override fun doUpdateVisitedHistory(view: WebView, url: String, isReload: Boolean) {
            setPage(id) { it.copy(url = url) }
            refreshNav(id, view)
            saveTab(id, url, null)
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            val url = request.url.toString()
            val text = error.description?.toString().orEmpty()
            val problem = when {
                "CLEARTEXT" in text -> return showInsecure(id, url)
                "INTERNET_DISCONNECTED" in text || "NETWORK_CHANGED" in text ->
                    Problem(Problem.Kind.OFFLINE, "You're offline", "Connect to the internet, then try again.", url)
                error.errorCode == WebViewClient.ERROR_HOST_LOOKUP || "NAME_NOT_RESOLVED" in text ->
                    Problem(Problem.Kind.NOT_FOUND, "This site can't be found", "Check the address, or your phone's connection, and try again.", url)
                error.errorCode == WebViewClient.ERROR_TIMEOUT || "TIMED_OUT" in text ->
                    Problem(Problem.Kind.TIMEOUT, "The site took too long to answer", "It may be busy or down. Try again in a moment.", url)
                error.errorCode == WebViewClient.ERROR_CONNECT || "REFUSED" in text ->
                    Problem(Problem.Kind.REFUSED, "The site refused to connect", "It may be down, or block this network.", url)
                "BLOCKED" in text -> Problem(Problem.Kind.BLOCKED, "This page was blocked", "Android's WebView refused to show it.", url)
                else -> Problem(Problem.Kind.OTHER, "This page didn't load", text.removePrefix("net::").ifBlank { "Try again in a moment." }, url)
            }
            setPage(id) { it.copy(loading = false, problem = problem) }
        }

        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            // Never continue past a certificate problem: the page could be someone pretending to be the site.
            handler.cancel()
            if (error.url != navigating[id] && error.url != pages[id]?.url) return
            val why = when (error.primaryError) {
                SslError.SSL_EXPIRED -> "Its certificate has expired."
                SslError.SSL_NOTYETVALID -> "Its certificate isn't valid yet. Check that the phone's date and time are right."
                SslError.SSL_IDMISMATCH -> "Its certificate is for a different site."
                SslError.SSL_UNTRUSTED -> "Its certificate isn't from an authority this phone trusts."
                SslError.SSL_DATE_INVALID -> "Its certificate's dates are invalid."
                else -> "Its certificate isn't valid."
            }
            setPage(id) { it.copy(loading = false, problem = Problem(Problem.Kind.CERTIFICATE, "Your connection to this site isn't private",
                "$why Raticode's browser won't open it, because someone could be pretending to be the site.", error.url)) }
        }

        override fun onReceivedHttpAuthRequest(view: WebView, handler: HttpAuthHandler, host: String, realm: String) {
            if (id != active || ask != null) return handler.cancel()
            ask = Ask.SignIn(id, host, realm, handler)
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            // Android ends the whole app unless the dead page is let go here.
            pageDied(id, view)
            return true
        }
    }

    private inner class ChromeClient(private val id: String) : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) = setPage(id) { it.copy(progress = newProgress) }
        override fun onReceivedTitle(view: WebView, title: String?) {
            setPage(id) { it.copy(title = title.orEmpty()) }
            saveTab(id, view.url ?: return, title)
        }

        override fun onCreateWindow(view: WebView, isDialog: Boolean, isUserGesture: Boolean, resultMsg: Message): Boolean {
            if (!isUserGesture) { if (id == active) notice = "Raticode's browser blocked a pop-up this page opened by itself."; return false }
            val tab = newTab(private = isPrivate(id)) ?: return false
            val child = create(tab)
            (resultMsg.obj as? WebView.WebViewTransport)?.webView = child
            resultMsg.sendToTarget()
            return true
        }

        override fun onCloseWindow(window: WebView) { views.entries.firstOrNull { it.value === window }?.key?.let(::close) }

        override fun onJsAlert(view: WebView, url: String, message: String, result: JsResult): Boolean =
            asked(result) { Ask.Alert(id, BrowserRules.host(url), message.take(2000), result) }
        override fun onJsConfirm(view: WebView, url: String, message: String, result: JsResult): Boolean =
            asked(result) { Ask.Confirm(id, BrowserRules.host(url), message.take(2000), result) }
        override fun onJsPrompt(view: WebView, url: String, message: String, defaultValue: String?, result: JsPromptResult): Boolean =
            asked(result) { Ask.Prompt(id, BrowserRules.host(url), message.take(2000), defaultValue.orEmpty().take(2000), result) }
        override fun onJsBeforeUnload(view: WebView, url: String, message: String, result: JsResult): Boolean =
            asked(result) { Ask.Leave(id, BrowserRules.host(url), result) }

        private fun asked(result: JsResult, make: () -> Ask): Boolean {
            // Only the page on screen may ask, one question at a time.
            if (id != active || ask != null || !shown) result.cancel() else ask = make()
            return true
        }

        override fun onPermissionRequest(request: PermissionRequest) {
            val camera = PermissionRequest.RESOURCE_VIDEO_CAPTURE in request.resources
            val microphone = PermissionRequest.RESOURCE_AUDIO_CAPTURE in request.resources
            // Asked each time and never remembered, and only for the page on screen.
            if ((camera || microphone) && request.resources.all { it == PermissionRequest.RESOURCE_VIDEO_CAPTURE || it == PermissionRequest.RESOURCE_AUDIO_CAPTURE } &&
                id == active && ask == null && shown && request.origin.scheme == "https") {
                ask = Ask.Media(id, request.origin.host.orEmpty(), camera, microphone, request)
                return
            }
            request.deny()
            val wanted = request.resources.mapNotNull {
                when (it) {
                    PermissionRequest.RESOURCE_VIDEO_CAPTURE -> "camera"
                    PermissionRequest.RESOURCE_AUDIO_CAPTURE -> "microphone"
                    PermissionRequest.RESOURCE_PROTECTED_MEDIA_ID -> "protected video"
                    PermissionRequest.RESOURCE_MIDI_SYSEX -> "MIDI devices"
                    else -> null
                }
            }.distinct()
            if (id == active && wanted.isNotEmpty()) notice = "This site asked for your ${wanted.joinToString(" and ")}. " +
                "Raticode's browser doesn't share ${if (wanted.size == 1) "it" else "them"}; open the page in your phone's browser to allow it."
        }

        override fun onPermissionRequestCanceled(request: PermissionRequest) { if ((ask as? Ask.Media)?.request === request) ask = null }

        override fun onGeolocationPermissionsShowPrompt(origin: String, callback: GeolocationPermissions.Callback) {
            callback.invoke(origin, false, false)
            if (id == active) notice = "This site asked for your location. Raticode's browser doesn't share it; open the page in your phone's browser to allow it."
        }

        override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
            if (id != active || chooser != null) { callback.onReceiveValue(null); return true }
            val intent = try { params.createIntent() } catch (_: Exception) { callback.onReceiveValue(null); return true }
            chooser = Chooser(callback, intent, params.mode == FileChooserParams.MODE_OPEN_MULTIPLE)
            return true
        }

        override fun onShowCustomView(view: View, callback: CustomViewCallback) {
            if (id != active) return callback.onCustomViewHidden()
            fullscreen?.takeIf { it.callback !== callback }?.callback?.onCustomViewHidden()
            fullscreen = Fullscreen(id, view, callback)
            VideoFullscreen.enter(BrowserMediaRules.Engine.CHROMIUM, id)
            // The page marks its full-screen element a moment after this, and a video may still be loading its size.
            for (delay in SIZE_CHECKS_MS) main.postDelayed({ measureFullscreen(id) }, delay)
        }

        override fun onHideCustomView() {
            if (fullscreen?.tab != id) return
            fullscreen = null
            VideoFullscreen.leave(BrowserMediaRules.Engine.CHROMIUM, id)
        }
    }
}
