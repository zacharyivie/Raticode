package dev.raticode.mobile.browser

import android.Manifest
import android.app.Application
import android.content.ContentValues
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.util.Base64
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import dev.raticode.mobile.live.BrowserAddress
import org.mozilla.geckoview.AllowOrDeny
import org.mozilla.geckoview.ContentBlocking
import org.mozilla.geckoview.GeckoResult
import org.mozilla.geckoview.GeckoRuntime
import org.mozilla.geckoview.GeckoRuntimeSettings
import org.mozilla.geckoview.GeckoSession
import org.mozilla.geckoview.GeckoSessionSettings
import org.mozilla.geckoview.MediaSession
import org.mozilla.geckoview.StorageController
import org.mozilla.geckoview.WebExtension
import org.mozilla.geckoview.WebExtensionController
import org.mozilla.geckoview.WebRequestError
import org.mozilla.geckoview.WebResponse
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import java.util.concurrent.Executors

/**
 * Raticode's second browser: Mozilla's Gecko engine (GeckoView), inside Raticode's own window, beside the Chromium-based
 * [BrowserRuntime]. Its tabs, favorites, history and sign-ins are separate from that browser's and from the Firefox app's.
 * It enforces the same rules as the other browser: https only, certificate errors never bypassed, no access to the phone's
 * camera, microphone or location. Extensions come from Mozilla's add-ons site, and Gecko refuses any Mozilla hasn't signed.
 */
internal class GeckoBrowser private constructor(private val app: Application) {
    companion object {
        /** Sessions kept open at once; other tabs close with their history saved and reopen when shown. */
        const val MAX_LIVE = 5
        @Volatile private var instance: GeckoBrowser? = null
        fun get(context: Context): GeckoBrowser = instance ?: synchronized(this) {
            instance ?: GeckoBrowser(context.applicationContext as Application).also { instance = it; BrowserMedia.attach(context) }
        }
        internal fun peek(): GeckoBrowser? = instance
        private val store = EncryptedJsonFile("raticode.gecko.v1", "gecko-v1", "gecko-v1.bin")
    }

    data class Page(val url: String = BrowserRules.HOME, val title: String = "", val progress: Int = 0, val loading: Boolean = false,
                    val canBack: Boolean = false, val canForward: Boolean = false, val secure: Boolean = false)
    data class Extension(val id: String, val name: String, val version: String, val enabled: Boolean, val summary: String, val options: String?,
                         val action: WebExtension.Action?, val native: WebExtension)
    data class LinkMenu(val tab: String, val link: String?, val image: String?)
    data class Popup(val extension: String, val session: GeckoSession)
    data class Found(val current: Int, val total: Int)

    /** Something only the user can answer. Shown one at a time; every [Ask] must be answered so Gecko isn't left waiting. */
    sealed interface Ask {
        data class Confirm(val title: String, val message: String, val yes: String, val no: String?, val answer: (Boolean) -> Unit) : Ask
        data class Text(val title: String, val message: String, val value: String, val answer: (String?) -> Unit) : Ask
        data class Login(val site: String, val message: String, val username: Boolean, val answer: (String?, String?) -> Unit) : Ask
        data class Choose(val title: String, val items: List<Pair<String, String>>, val multiple: Boolean, val answer: (List<String>?) -> Unit) : Ask
        data class Files(val mime: List<String>, val multiple: Boolean, val answer: (List<Uri>?) -> Unit) : Ask
        data class Media(val site: String, val camera: Boolean, val microphone: Boolean, val answer: (Boolean) -> Unit) : Ask
        data class AppPermissions(val permissions: List<String>, val answer: (Boolean) -> Unit) : Ask
        data class Install(val name: String, val permissions: List<String>, val origins: List<String>, val data: List<String>, val answer: (Boolean) -> Unit) : Ask
    }

    var saved by mutableStateOf(store.load(app)?.let(GeckoRules::decode) ?: GeckoRules.Saved()); private set
    val pages = mutableStateMapOf<String, Page>()
    var active by mutableStateOf(saved.active); private set
    var extensions by mutableStateOf<List<Extension>>(emptyList()); private set
    var installing by mutableStateOf<Set<String>>(emptySet()); private set
    val asks = mutableStateListOf<Ask>()
    var notice by mutableStateOf<String?>(null)
    var menu by mutableStateOf<LinkMenu?>(null)
    var popup by mutableStateOf<Popup?>(null); private set
    var fullscreen by mutableStateOf(false); private set
    var found by mutableStateOf<Found?>(null); private set
    var playing by mutableStateOf<Set<String>>(emptySet()); private set

    private val main = Handler(Looper.getMainLooper())
    private val io = Executors.newCachedThreadPool { Thread(it, "gecko-browser").apply { isDaemon = true } }
    private val sessions = LinkedHashMap<String, GeckoSession>()
    private val states = HashMap<String, GeckoSession.SessionState>()
    private val media = HashMap<String, MediaSession>()
    private val mediaDetails = HashMap<String, MediaDetails>()
    private val documents = GeckoDocuments()
    private val actions = HashMap<String, WebExtension.Action>()
    private val crashed = HashSet<String>()
    private var inFront = true

    val tabs get() = saved.tabs.filter { it.profile == saved.currentProfile }
    val page get() = active?.let { pages[it] } ?: Page()
    val session get() = active?.let { sessions[it] }
    val profile get() = saved.profiles.first { it.id == saved.currentProfile }
    val bookmarks get() = saved.bookmarks.filter { it.profile == saved.currentProfile }
    val history get() = saved.history.filter { it.profile == saved.currentProfile }

    val runtime: GeckoRuntime by lazy {
        val blocking = ContentBlocking.Settings.Builder()
            .enhancedTrackingProtectionLevel(etp(saved.settings.tracking))
            .safeBrowsing(ContentBlocking.SafeBrowsing.DEFAULT)
            .cookieBehavior(ContentBlocking.CookieBehavior.ACCEPT_FIRST_PARTY_AND_ISOLATE_OTHERS)
            .build()
        val settings = GeckoRuntimeSettings.Builder()
            .allowInsecureConnections(GeckoRuntimeSettings.HTTPS_ONLY)
            .aboutConfigEnabled(false).remoteDebuggingEnabled(false).consoleOutput(false)
            .extensionsProcessEnabled(true)
            .contentBlocking(blocking)
            .preferredColorScheme(colors(saved.settings.colors))
            .automaticFontSizeAdjustment(saved.settings.textZoom == 100)
            .also { if (saved.settings.textZoom != 100) it.fontSizeFactor(fontFactor(saved.settings.textZoom)) }
            .build()
        GeckoRuntime.create(app, settings).also { runtime ->
            runtime.webExtensionController.promptDelegate = extensionPrompts
            main.post { refreshExtensions() }
        }
    }

    init {
        saved.tabs.forEach { pages[it.id] = Page(url = it.url, title = it.title) }
        if (tabs.isEmpty()) newTab()
        else if (active == null || tabs.none { it.id == active }) active = tabs.last().id
    }

    private fun fontFactor(zoom: Int) = zoom / 100f * app.resources.configuration.fontScale

    fun textZoom(step: Int) {
        val zoom = if (step == 0) 100 else BrowserRules.textZoom(saved.settings.textZoom, step)
        update(saved.copy(settings = saved.settings.copy(textZoom = zoom)))
        runtime.settings.setAutomaticFontSizeAdjustment(zoom == 100)
        if (zoom != 100) runtime.settings.setFontSizeFactor(fontFactor(zoom))
    }

    private fun etp(level: String) = if (level == GeckoRules.TRACKING_STRICT) ContentBlocking.EtpLevel.STRICT else ContentBlocking.EtpLevel.DEFAULT
    private fun colors(value: String) = when (value) {
        GeckoRules.COLORS_LIGHT -> GeckoRuntimeSettings.COLOR_SCHEME_LIGHT
        GeckoRules.COLORS_DARK -> GeckoRuntimeSettings.COLOR_SCHEME_DARK
        else -> GeckoRuntimeSettings.COLOR_SCHEME_SYSTEM
    }

    private fun update(next: GeckoRules.Saved) { saved = next; store.save(app, GeckoRules.encode(next)) }
    private fun setPage(id: String, change: (Page) -> Page) { pages[id]?.let { pages[id] = change(it) } }
    private fun site(id: String?) = id?.let { pages[it]?.url }?.let(BrowserRules::host).orEmpty().ifBlank { "This page" }
    fun answered(ask: Ask) { asks.remove(ask) }

    // Tabs ------------------------------------------------------------------------------------------------------------

    fun newTab(url: String? = null, background: Boolean = false, private: Boolean = false): String? {
        if (saved.tabs.size >= BrowserRules.MAX_TABS) { notice = "Close a tab to open another. Raticode keeps up to ${BrowserRules.MAX_TABS} tabs."; return null }
        val id = UUID.randomUUID().toString()
        pages[id] = Page()
        update(saved.copy(tabs = saved.tabs + GeckoRules.Tab(id, profile = saved.currentProfile, private = private)))
        if (!background || active == null) select(id)
        if (url != null) go(url, id)
        return id
    }

    /** Opens something a page offered in tab [from]; a private tab's links stay private. */
    fun openFrom(from: String, url: String, background: Boolean = false) = newTab(url, background, private = isPrivate(from))

    fun isPrivate(id: String?) = id != null && saved.tabs.any { it.id == id && it.private }
    val activeIsPrivate get() = isPrivate(active)

    fun select(id: String) {
        if (saved.tabs.none { it.id == id }) return
        val previous = active
        active = id
        crashed.remove(id)
        update(saved.copy(active = id))
        if (previous != id) { found = null; previous?.let { sessions[it] }?.let { if (previous !in playing) it.setActive(false); it.setFocused(false) } }
        if (pages[id]?.url != BrowserRules.HOME) sessionFor(id)?.let { it.setActive(inFront); it.setFocused(inFront) }
    }

    fun close(id: String) {
        val remaining = tabs.filter { it.id != id }
        val tab = saved.tabs.firstOrNull { it.id == id }
        closeSession(id); states.remove(id); pages.remove(id); crashed.remove(id)
        val closed = if (tab != null && !tab.private && BrowserAddress.isWeb(tab.url)) (listOf(tab) + saved.closed).take(GeckoRules.MAX_CLOSED) else saved.closed
        update(saved.copy(tabs = saved.tabs.filter { it.id != id }, closed = closed))
        if (active == id) {
            active = null
            if (remaining.isEmpty()) newTab() else select(remaining.last().id)
        }
    }

    val canReopen get() = saved.closed.any { it.profile == saved.currentProfile }

    fun reopenClosed(): Boolean {
        val tab = saved.closed.firstOrNull { it.profile == saved.currentProfile } ?: return false
        update(saved.copy(closed = saved.closed - tab))
        return newTab(tab.url) != null
    }

    private fun closeSession(id: String) {
        sessions.remove(id)?.close()
        mediaGone(id)
    }

    /** The tab's page is gone for good, so the media bar and full-screen state let go of it now. */
    private fun mediaGone(id: String) {
        media.remove(id); mediaDetails.remove(id); documents.loadStopped(id)
        BrowserMedia.forget(mediaKey(id))
        if (id == active && fullscreen) { fullscreen = false; BrowserMedia.fullscreen = false }
        if (id in playing) changePlaying(playing - id)
    }

    /**
     * The session the page should show, opening it when the active tab has a page but no session, as after Android ended
     * the app and it came back with its saved tabs. A tab whose page crashed stays closed until Reload or a tap on it,
     * so a page that crashes on load can't reopen itself over and over.
     */
    fun activeSession(): GeckoSession? {
        val id = active ?: return null
        sessions[id]?.let { return it }
        if (id in crashed || pages[id]?.url == BrowserRules.HOME) return null
        return sessionFor(id)?.also { it.setActive(inFront); it.setFocused(inFront) }
    }

    /** The open session for [id], reopening a tab that was closed to save memory. */
    private fun sessionFor(id: String): GeckoSession? {
        sessions[id]?.let { sessions.remove(id); sessions[id] = it; return it }
        val tab = saved.tabs.firstOrNull { it.id == id } ?: return null
        val session = newSession(id, tab.profile, tab.private)
        val state = states.remove(id)
        when {
            state != null -> session.restoreState(state)
            (pages[id]?.url ?: BrowserRules.HOME) != BrowserRules.HOME -> session.loadUri(pages.getValue(id).url)
        }
        return session
    }

    private fun sessionSettings(profile: String, private: Boolean = false) = GeckoSessionSettings.Builder().contextId(GeckoRules.contextId(profile)).useTrackingProtection(true)
        .usePrivateMode(private)
        .suspendMediaWhenInactive(false)
        .userAgentMode(if (saved.settings.desktopSites) GeckoSessionSettings.USER_AGENT_MODE_DESKTOP else GeckoSessionSettings.USER_AGENT_MODE_MOBILE)
        .viewportMode(if (saved.settings.desktopSites) GeckoSessionSettings.VIEWPORT_MODE_DESKTOP else GeckoSessionSettings.VIEWPORT_MODE_MOBILE)
        .build()

    private fun newSession(id: String, profile: String, private: Boolean = false): GeckoSession {
        val session = GeckoSession(sessionSettings(profile, private))
        wire(id, session)
        session.open(runtime)
        sessions[id] = session
        extensions.forEach { session.webExtensionController.setActionDelegate(it.native, sessionActions) }
        while (sessions.size > MAX_LIVE) {
            // A private tab's session stays open: Gecko forgets private data when the last one closes.
            val oldest = sessions.keys.firstOrNull { it != id && it != active && it !in playing && !isPrivate(it) } ?: break
            closeSession(oldest)
        }
        return session
    }

    // Navigation ------------------------------------------------------------------------------------------------------

    fun go(input: String, tab: String? = active) {
        val id = tab ?: newTab() ?: return
        when (val target = BrowserRules.resolve(input, saved.settings.search)) {
            null -> return
            is BrowserRules.Target.Load -> load(id, target.url)
            // Gecko's HTTPS-only mode upgrades it, and says so if the site has no secure version.
            is BrowserRules.Target.Insecure -> load(id, target.url)
            is BrowserRules.Target.App -> notice = "Raticode (gecko) opens web pages only."
            is BrowserRules.Target.Blocked -> notice = target.reason
        }
    }

    private fun load(id: String, url: String) {
        crashed.remove(id)
        setPage(id) { it.copy(url = url, loading = true, progress = 5) }
        (sessions[id] ?: sessionFor(id))?.let { session ->
            session.loadUri(url)
            if (id == active) { session.setActive(inFront); session.setFocused(inFront) }
        }
    }

    fun back() { session?.goBack() }
    fun forward() { session?.goForward() }
    fun reload() { active?.let(crashed::remove); if (session == null) activeSession() else session?.reload() }
    fun stop() { session?.stop() }
    fun find(text: String, backwards: Boolean = false) {
        val finder = session?.finder ?: return
        if (text.isEmpty()) { finder.clear(); found = null; return }
        finder.find(text, if (backwards) GeckoSession.FINDER_FIND_BACKWARDS else 0).accept { result -> found = result?.let { Found(it.current, it.total) } }
    }
    fun endFind() { session?.finder?.clear(); found = null }
    fun exitFullscreen() { session?.exitFullScreen() }

    private fun wire(id: String, session: GeckoSession) {
        session.navigationDelegate = navigation(id)
        session.progressDelegate = progress(id)
        session.contentDelegate = content(id)
        session.permissionDelegate = permissions
        session.promptDelegate = Prompts(id)
        session.mediaSessionDelegate = sound(id)
        session.scrollDelegate = object : GeckoSession.ScrollDelegate {
            override fun onScrollChanged(session: GeckoSession, scrollX: Int, scrollY: Int) { if (id == active) scrollListener?.invoke(scrollY) }
        }
    }

    /** Told where the page in the active tab has scrolled to, in CSS pixels, which match dp on a phone-sized page. */
    var scrollListener: ((Int) -> Unit)? = null

    private fun navigation(id: String) = object : GeckoSession.NavigationDelegate {
        override fun onLocationChange(session: GeckoSession, url: String?, perms: MutableList<GeckoSession.PermissionDelegate.ContentPermission>, hasUserGesture: Boolean) {
            val address = url ?: return
            if (id.isEmpty()) return // An extension's popup, which has no tab.
            if (documents.addressChanged(id)) newDocument(id)
            if (address == "about:blank" && pages[id]?.url != BrowserRules.HOME) return
            setPage(id) { it.copy(url = address) }
            val saveAs = if (BrowserAddress.isWeb(address)) BrowserRules.storable(address) else BrowserRules.HOME
            update(saved.copy(tabs = saved.tabs.map { if (it.id == id) it.copy(url = saveAs) else it }))
        }
        override fun onCanGoBack(session: GeckoSession, canGoBack: Boolean) { setPage(id) { it.copy(canBack = canGoBack) } }
        override fun onCanGoForward(session: GeckoSession, canGoForward: Boolean) { setPage(id) { it.copy(canForward = canGoForward) } }

        override fun onLoadRequest(session: GeckoSession, request: GeckoSession.NavigationDelegate.LoadRequest): GeckoResult<AllowOrDeny> {
            val uri = request.uri
            // A page's own blob: or data: file, such as a chat Open WebUI exports, comes through here before it downloads.
            if (PageFiles.geckoMayLoad(uri, request.triggerUri)) return GeckoResult.allow()
            if (GeckoRules.looksLikeExtension(uri)) { offerInstall(uri); return GeckoResult.deny() }
            if (uri.startsWith("moz-extension://")) return if (extensions.any { uri.startsWith(it.native.metaData.baseUrl) }) GeckoResult.allow() else GeckoResult.deny()
            return when (val target = BrowserRules.classify(uri)) {
                is BrowserRules.Target.Load, is BrowserRules.Target.Insecure -> GeckoResult.allow()
                is BrowserRules.Target.App -> { notice = "Raticode (gecko) doesn't open other apps from pages."; GeckoResult.deny() }
                is BrowserRules.Target.Blocked -> { notice = target.reason; GeckoResult.deny() }
            }
        }

        // target=_blank and window.open become tabs. Gecko has already applied its pop-up rule (see onPopupPrompt).
        override fun onNewSession(session: GeckoSession, uri: String): GeckoResult<GeckoSession>? {
            if (GeckoRules.looksLikeExtension(uri)) { offerInstall(uri); return null }
            val allowed = uri.startsWith("moz-extension://") || BrowserRules.classify(uri).let { it is BrowserRules.Target.Load || it is BrowserRules.Target.Insecure }
            if (!allowed) return null
            val profile = saved.tabs.firstOrNull { it.id == id }?.profile ?: saved.currentProfile
            return openedTab(uri, profile, isPrivate(id))?.let { GeckoResult.fromValue(it) }
        }

        // Gecko's own error pages can offer a way past a certificate error; this one never does.
        override fun onLoadError(session: GeckoSession, uri: String?, error: WebRequestError): GeckoResult<String> {
            setPage(id) { it.copy(loading = false) }
            return GeckoResult.fromValue(errorPage(uri, error))
        }
    }

    /** A new tab for a page or extension that asked for one, or null at the tab limit. Gecko loads the page into the session returned. */
    internal fun openedTab(url: String, profile: String, private: Boolean = false): GeckoSession? {
        if (saved.tabs.size >= BrowserRules.MAX_TABS) { notice = "Close a tab to open another. Raticode keeps up to ${BrowserRules.MAX_TABS} tabs."; return null }
        val id = UUID.randomUUID().toString()
        pages[id] = Page(url = url, loading = true)
        update(saved.copy(tabs = saved.tabs + GeckoRules.Tab(id, if (BrowserAddress.isWeb(url)) BrowserRules.storable(url) else BrowserRules.HOME, profile = profile, private = private)))
        val session = GeckoSession(sessionSettings(profile, private))
        wire(id, session)
        sessions[id] = session
        main.post { select(id) }
        return session
    }

    private fun errorPage(uri: String?, error: WebRequestError): String {
        val site = uri?.let(BrowserRules::host).orEmpty().ifBlank { "this site" }
        val (title, detail) = when {
            error.category == WebRequestError.ERROR_CATEGORY_SECURITY -> "This connection isn't private" to
                "The security certificate for $site isn't valid, so Raticode won't open the page. This can't be skipped."
            error.code == WebRequestError.ERROR_HTTPS_ONLY -> "This site has no secure version" to
                "Raticode only opens secure (https) pages, and $site didn't answer securely."
            error.category == WebRequestError.ERROR_CATEGORY_SAFEBROWSING -> "Reported as dangerous" to
                "$site has been reported for phishing or malware, so Raticode won't open it."
            error.category == WebRequestError.ERROR_CATEGORY_NETWORK || error.code == WebRequestError.ERROR_UNKNOWN_HOST -> "Can't reach $site" to
                "Check the connection, or try the address again."
            else -> "This page didn't open" to "Something went wrong opening $site."
        }
        val html = "<!doctype html><meta name=viewport content='width=device-width'><title>${escape(title)}</title>" +
            "<body style='font:16px sans-serif;margin:32px 24px;color-scheme:light dark'><h2>${escape(title)}</h2><p>${escape(detail)}</p>"
        return "data:text/html;base64," + Base64.encodeToString(html.toByteArray(), Base64.NO_WRAP)
    }

    private fun escape(text: String) = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace("\"", "&quot;")

    private fun progress(id: String) = object : GeckoSession.ProgressDelegate {
        override fun onPageStart(session: GeckoSession, url: String) { documents.loadStarted(id); setPage(id) { it.copy(loading = true, progress = 5) } }
        override fun onPageStop(session: GeckoSession, success: Boolean) {
            documents.loadStopped(id)
            setPage(id) { it.copy(loading = false, progress = 100) }
            val page = pages[id] ?: return
            val tab = saved.tabs.firstOrNull { it.id == id } ?: return
            if (success && saved.settings.keepHistory && !tab.private) update(saved.copy(history = GeckoRules.remember(saved.history, BrowserRules.storable(page.url), page.title, tab.profile)))
        }
        override fun onProgressChange(session: GeckoSession, progress: Int) { setPage(id) { it.copy(progress = progress) } }
        override fun onSecurityChange(session: GeckoSession, securityInfo: GeckoSession.ProgressDelegate.SecurityInformation) { setPage(id) { it.copy(secure = securityInfo.isSecure) } }
        override fun onSessionStateChange(session: GeckoSession, sessionState: GeckoSession.SessionState) { states[id] = sessionState }
    }

    private fun content(id: String) = object : GeckoSession.ContentDelegate {
        override fun onTitleChange(session: GeckoSession, title: String?) {
            val text = title.orEmpty().take(BrowserRules.MAX_TITLE)
            setPage(id) { it.copy(title = text) }
            update(saved.copy(tabs = saved.tabs.map { if (it.id == id) it.copy(title = text) else it }))
            publishMedia(id)
        }
        override fun onFullScreen(session: GeckoSession, fullScreen: Boolean) { if (id == active) { fullscreen = fullScreen; BrowserMedia.fullscreen = fullScreen } }
        override fun onCloseRequest(session: GeckoSession) { close(id) }
        override fun onCrash(session: GeckoSession) { sessionLost(id, session) }
        override fun onKill(session: GeckoSession) { sessionLost(id, session) }
        override fun onContextMenu(session: GeckoSession, screenX: Int, screenY: Int, element: GeckoSession.ContentDelegate.ContextElement) {
            val link = element.linkUri?.takeIf { BrowserAddress.isWeb(it) }
            val image = element.srcUri?.takeIf { element.type == GeckoSession.ContentDelegate.ContextElement.TYPE_IMAGE && BrowserAddress.isWeb(it) }
            if (link != null || image != null) menu = LinkMenu(id, link, image)
        }
        override fun onExternalResponse(session: GeckoSession, response: WebResponse) { download(response) }
    }

    private fun sessionLost(id: String, session: GeckoSession) {
        if (sessions[id] === session) { sessions.remove(id); session.close(); crashed.add(id) }
        mediaGone(id)
        setPage(id) { it.copy(loading = false) }
        notice = "That page stopped working. Reload to try again."
    }

    // Pages get the camera or microphone only by asking each time, and never location or notifications.
    private val permissions = object : GeckoSession.PermissionDelegate {
        override fun onAndroidPermissionsRequest(session: GeckoSession, permissions: Array<out String>?, callback: GeckoSession.PermissionDelegate.Callback) {
            val wanted = permissions.orEmpty().toList()
            if (wanted.isEmpty() || wanted.any { it != Manifest.permission.CAMERA && it != Manifest.permission.RECORD_AUDIO }) { callback.reject(); return }
            if (wanted.all { ContextCompat.checkSelfPermission(app, it) == PackageManager.PERMISSION_GRANTED }) { callback.grant(); return }
            asks.add(Ask.AppPermissions(wanted) { granted -> if (granted) callback.grant() else callback.reject() })
        }
        override fun onContentPermissionRequest(session: GeckoSession, perm: GeckoSession.PermissionDelegate.ContentPermission): GeckoResult<Int> {
            val allowed = perm.permission == GeckoSession.PermissionDelegate.PERMISSION_AUTOPLAY_INAUDIBLE ||
                perm.permission == GeckoSession.PermissionDelegate.PERMISSION_PERSISTENT_STORAGE
            return GeckoResult.fromValue(if (allowed) GeckoSession.PermissionDelegate.ContentPermission.VALUE_ALLOW else GeckoSession.PermissionDelegate.ContentPermission.VALUE_DENY)
        }
        override fun onMediaPermissionRequest(session: GeckoSession, uri: String, video: Array<out GeckoSession.PermissionDelegate.MediaSource>?, audio: Array<out GeckoSession.PermissionDelegate.MediaSource>?, callback: GeckoSession.PermissionDelegate.MediaCallback) {
            val camera = video?.firstOrNull { it.source == GeckoSession.PermissionDelegate.MediaSource.SOURCE_CAMERA }
            val microphone = audio?.firstOrNull { it.source == GeckoSession.PermissionDelegate.MediaSource.SOURCE_MICROPHONE }
            if ((camera == null && microphone == null) || !uri.startsWith("https://") || session !== this@GeckoBrowser.session) { callback.reject(); return }
            asks.add(Ask.Media(BrowserRules.host(uri), camera != null, microphone != null) { allowed ->
                if (allowed) callback.grant(camera, microphone)
                else { callback.reject(); notice = "The page wasn't given your ${BrowserRules.mediaAsk(camera != null, microphone != null)}." } })
        }
    }

    private inner class Prompts(val id: String) : GeckoSession.PromptDelegate {
        private fun <T> asked(build: (GeckoResult<GeckoSession.PromptDelegate.PromptResponse>) -> Ask): GeckoResult<GeckoSession.PromptDelegate.PromptResponse> =
            GeckoResult<GeckoSession.PromptDelegate.PromptResponse>().also { asks.add(build(it)) }

        override fun onAlertPrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.AlertPrompt) = asked<Unit> { r ->
            Ask.Confirm(site(id), prompt.message.orEmpty(), "OK", null) { r.complete(prompt.dismiss()) } }
        override fun onButtonPrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.ButtonPrompt) = asked<Unit> { r ->
            Ask.Confirm(site(id), prompt.message.orEmpty(), "OK", "Cancel") { yes ->
                r.complete(prompt.confirm(if (yes) GeckoSession.PromptDelegate.ButtonPrompt.Type.POSITIVE else GeckoSession.PromptDelegate.ButtonPrompt.Type.NEGATIVE)) } }
        override fun onTextPrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.TextPrompt) = asked<Unit> { r ->
            Ask.Text(site(id), prompt.message.orEmpty(), prompt.defaultValue.orEmpty()) { text -> r.complete(if (text == null) prompt.dismiss() else prompt.confirm(text)) } }
        override fun onBeforeUnloadPrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.BeforeUnloadPrompt) = asked<Unit> { r ->
            Ask.Confirm(site(id), "Leave this page? Changes you made may not be saved.", "Leave", "Stay") { yes -> r.complete(prompt.confirm(if (yes) AllowOrDeny.ALLOW else AllowOrDeny.DENY)) } }
        override fun onRepostConfirmPrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.RepostConfirmPrompt) = asked<Unit> { r ->
            Ask.Confirm(site(id), "Send this form again?", "Send", "Cancel") { yes -> r.complete(prompt.confirm(if (yes) AllowOrDeny.ALLOW else AllowOrDeny.DENY)) } }
        override fun onAuthPrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.AuthPrompt) = asked<Unit> { r ->
            val passwordOnly = prompt.authOptions.flags and GeckoSession.PromptDelegate.AuthPrompt.AuthOptions.Flags.ONLY_PASSWORD != 0
            Ask.Login(site(id), prompt.message.orEmpty(), !passwordOnly) { user, password ->
                r.complete(when { password == null -> prompt.dismiss(); passwordOnly -> prompt.confirm(password); else -> prompt.confirm(user.orEmpty(), password) }) } }
        override fun onChoicePrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.ChoicePrompt) = asked<Unit> { r ->
            val flat = ArrayList<GeckoSession.PromptDelegate.ChoicePrompt.Choice>()
            fun add(list: Array<GeckoSession.PromptDelegate.ChoicePrompt.Choice>) { list.forEach { if (it.items != null) add(it.items!!) else if (!it.disabled && !it.separator) flat.add(it) } }
            add(prompt.choices)
            Ask.Choose(prompt.title ?: site(id), flat.map { it.id to it.label }, prompt.type == GeckoSession.PromptDelegate.ChoicePrompt.Type.MULTIPLE) { picked ->
                r.complete(if (picked == null) prompt.dismiss() else prompt.confirm(picked.toTypedArray())) } }
        override fun onFilePrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.FilePrompt) = asked<Unit> { r ->
            Ask.Files(prompt.mimeTypes?.toList().orEmpty(), prompt.type == GeckoSession.PromptDelegate.FilePrompt.Type.MULTIPLE) { uris ->
                r.complete(if (uris.isNullOrEmpty()) prompt.dismiss() else prompt.confirm(app, uris.toTypedArray())) } }
        // Without these two, date, time and color fields do nothing when tapped.
        override fun onDateTimePrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.DateTimePrompt) = asked<Unit> { r ->
            val kind = when (prompt.type) {
                GeckoSession.PromptDelegate.DateTimePrompt.Type.MONTH -> GeckoRules.FORM_MONTH
                GeckoSession.PromptDelegate.DateTimePrompt.Type.WEEK -> GeckoRules.FORM_WEEK
                GeckoSession.PromptDelegate.DateTimePrompt.Type.TIME -> GeckoRules.FORM_TIME
                GeckoSession.PromptDelegate.DateTimePrompt.Type.DATETIME_LOCAL -> GeckoRules.FORM_DATETIME
                else -> GeckoRules.FORM_DATE
            }
            Ask.Text(prompt.title ?: site(id), GeckoRules.formHint(kind), prompt.defaultValue.orEmpty()) { text ->
                val value = text?.let { GeckoRules.formValue(kind, it, prompt.minValue, prompt.maxValue) }
                if (text != null && value == null) notice = "That isn't a $kind this field accepts."
                r.complete(if (value == null) prompt.dismiss() else prompt.confirm(value)) } }
        override fun onColorPrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.ColorPrompt) = asked<Unit> { r ->
            Ask.Text(prompt.title ?: site(id), GeckoRules.formHint(GeckoRules.FORM_COLOR), prompt.defaultValue.orEmpty()) { text ->
                val value = text?.let { GeckoRules.formValue(GeckoRules.FORM_COLOR, it) }?.takeIf { it.isNotEmpty() }
                if (text != null && value == null) notice = "That isn't a color this field accepts."
                r.complete(if (value == null) prompt.dismiss() else prompt.confirm(value)) } }
        // Gecko only asks about pop-ups a page opened without a tap.
        override fun onPopupPrompt(session: GeckoSession, prompt: GeckoSession.PromptDelegate.PopupPrompt): GeckoResult<GeckoSession.PromptDelegate.PromptResponse> {
            notice = "Blocked a pop-up from ${site(id)}."
            return GeckoResult.fromValue(prompt.confirm(AllowOrDeny.DENY))
        }
    }

    // Saving files ----------------------------------------------------------------------------------------------------

    private fun download(response: WebResponse) {
        val body = response.body
        val type = response.headers["content-type"]?.substringBefore(';')?.trim().orEmpty()
        if (type == "application/x-xpinstall" || GeckoRules.looksLikeExtension(response.uri)) { runCatching { body?.close() }; offerInstall(response.uri); return }
        if (body == null) { notice = "That download had nothing to save."; return }
        // blob: and data: files, which a page builds itself, have no address to name them: the name comes from the
        // Content-Disposition Gecko passes on, else from the file's type.
        val name = PageFiles.fileName(null, response.headers["content-disposition"], response.uri, type)
        if (Build.VERSION.SDK_INT < 29) { runCatching { body.close() }; notice = "Saving downloads from this browser needs Android 10 or newer."; return }
        val risky = BrowserRules.risky(name, type)
        val size = response.headers["content-length"]?.toLongOrNull()?.let { " (${it / 1024} KB)" }.orEmpty()
        val message = "$name$size from ${BrowserRules.host(response.uri)}" + if (risky) "\n\nThis kind of file can harm your phone. Only keep it if you trust the site." else ""
        asks.add(Ask.Confirm("Save to Downloads?", message, if (risky) "Save anyway" else "Save", "Cancel") { yes ->
            if (!yes) { io.execute { runCatching { body.close() } }; return@Confirm }
            saveToDownloads(name, type, body)
        })
    }

    private fun saveToDownloads(name: String, type: String, body: java.io.InputStream) {
        io.execute {
            val saved = Build.VERSION.SDK_INT >= 29 && runCatching {
                val values = ContentValues().apply { put(MediaStore.Downloads.DISPLAY_NAME, name); if (type.isNotEmpty()) put(MediaStore.Downloads.MIME_TYPE, type) }
                val uri = app.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)!!
                body.use { input -> app.contentResolver.openOutputStream(uri)!!.use { input.copyTo(it) } }
            }.isSuccess
            main.post { notice = if (saved) "Saved $name to Downloads." else "$name couldn't be saved." }
        }
    }

    /** Saves the current page to Downloads as a PDF. */
    fun saveAsPdf() {
        val current = session ?: return
        if (Build.VERSION.SDK_INT < 29) { notice = "Saving from this browser needs Android 10 or newer."; return }
        val name = BrowserRules.safeName(page.title.ifBlank { BrowserRules.host(page.url) }.ifBlank { "page" }.take(100) + ".pdf")
        current.saveAsPdf().accept({ stream -> if (stream == null) notice = "This page couldn't be saved as a PDF." else saveToDownloads(name, "application/pdf", stream) },
            { notice = "This page couldn't be saved as a PDF." })
    }

    // Sound -----------------------------------------------------------------------------------------------------------

    private fun sound(id: String) = object : MediaSession.Delegate {
        override fun onActivated(session: GeckoSession, mediaSession: MediaSession) { media[id] = mediaSession; mediaDetails.remove(id); publishMedia(id) }
        override fun onDeactivated(session: GeckoSession, mediaSession: MediaSession) { media.remove(id); changePlaying(playing - id); publishMedia(id) }
        override fun onPlay(session: GeckoSession, mediaSession: MediaSession) { changePlaying(playing + id); publishMedia(id) }
        override fun onPause(session: GeckoSession, mediaSession: MediaSession) { changePlaying(playing - id); publishMedia(id) }
        override fun onStop(session: GeckoSession, mediaSession: MediaSession) { changePlaying(playing - id); publishMedia(id) }
        override fun onFeatures(session: GeckoSession, mediaSession: MediaSession, features: Long) {
            mediaDetails.getOrPut(id) { MediaDetails() }.features = features
            publishMedia(id)
        }
        override fun onMetadata(session: GeckoSession, mediaSession: MediaSession, meta: MediaSession.Metadata) {
            val details = mediaDetails.getOrPut(id) { MediaDetails() }
            details.title = meta.title; details.artist = meta.artist; details.artwork = null
            val generation = ++details.generation
            meta.artwork?.getBitmap(BrowserMediaRules.ARTWORK_PX)?.accept({ bitmap ->
                // Only the latest track's picture counts, and only while the page that sent it is still there.
                if (bitmap != null && mediaDetails[id] === details && details.generation == generation) {
                    val image = "gecko:$id:$generation"
                    BrowserMedia.putImage(image, bitmap)
                    details.artwork = image
                    publishMedia(id)
                }
            }, { })
            publishMedia(id)
        }
    }

    /** What a tab's media session has said so far: its media title, buttons and picture. Features of 0 means none heard yet. */
    private class MediaDetails(var features: Long = 0, var title: String? = null, var artist: String? = null, var artwork: String? = null, var generation: Int = 0)

    private fun mediaKey(id: String) = BrowserMediaRules.Key(BrowserMediaRules.Engine.GECKO, id)

    /** Another page replaced the tab's: the old track's name, picture and buttons go with it, at once. */
    private fun newDocument(id: String) {
        mediaDetails.remove(id)
        if (id in playing) changePlaying(playing - id)
        BrowserMedia.forget(mediaKey(id))
    }

    private fun publishMedia(id: String) {
        val page = pages[id] ?: return
        val details = mediaDetails[id]
        val live = media[id] != null
        val features = details?.features ?: 0L
        val isPlaying = id in playing
        val toggle = if (isPlaying) MediaSession.Feature.PAUSE else MediaSession.Feature.PLAY
        BrowserMedia.report(mediaKey(id), BrowserMediaRules.Report(isPlaying,
            title = BrowserMediaRules.title(details?.title, page.title, page.url), artist = BrowserMediaRules.clean(details?.artist), site = BrowserMediaRules.site(page.url),
            canToggle = live && (features == 0L || features and toggle != 0L),
            canPrevious = live && features and MediaSession.Feature.PREVIOUS_TRACK != 0L,
            canNext = live && features and MediaSession.Feature.NEXT_TRACK != 0L,
            artwork = details?.artwork, private = isPrivate(id)))
    }

    /** One of the media bar's buttons, for tab [id]. False when the tab's media session has ended. */
    fun mediaAction(id: String, action: BrowserMediaRules.Action): Boolean {
        val session = media[id] ?: return false
        when (action) {
            BrowserMediaRules.Action.PLAY -> { sessions[id]?.setActive(true); session.play() }
            BrowserMediaRules.Action.PAUSE -> session.pause()
            BrowserMediaRules.Action.PREVIOUS -> session.previousTrack()
            BrowserMediaRules.Action.NEXT -> session.nextTrack()
        }
        return true
    }

    /** Shows tab [id], moving to its profile first when it belongs to another. */
    fun reveal(id: String) {
        val tab = saved.tabs.firstOrNull { it.id == id } ?: return
        switchProfile(tab.profile)
        select(id)
    }

    private fun changePlaying(now: Set<String>) {
        if (now == playing) return
        val stopped = playing - now
        playing = now
        if (!inFront) stopped.forEach { sessions[it]?.setActive(false) }
        // BrowserMedia hears the same change through publishMedia, and starts or ends the playback service.
    }

    fun stopSound() { media.values.forEach { it.pause() }; if (playing.isNotEmpty()) changePlaying(emptySet()); BrowserMedia.forget(BrowserMediaRules.Engine.GECKO) }

    /** Raticode came to the front ([on]) or left the screen. A tab playing sound stays active, as a music app would. */
    fun appInFront(on: Boolean) {
        inFront = on
        sessions.forEach { (id, session) -> if (id == active) { session.setActive(on || id in playing); session.setFocused(on) } else if (id in playing) session.setActive(true) }
    }

    // Favorites, profiles and settings --------------------------------------------------------------------------------

    fun isBookmarked(url: String) = bookmarks.any { it.url == url }
    fun toggleBookmark() {
        val page = page
        if (!BrowserAddress.isWeb(page.url)) return
        update(saved.copy(bookmarks = if (isBookmarked(page.url)) saved.bookmarks.filterNot { it.url == page.url && it.profile == saved.currentProfile }
            else (listOf(GeckoRules.Link(page.url, page.title, saved.currentProfile)) + saved.bookmarks).take(GeckoRules.MAX_BOOKMARKS)))
    }
    fun removeBookmark(link: GeckoRules.Link) = update(saved.copy(bookmarks = saved.bookmarks - link))
    /** Adds [links] to the current profile's favorites, keeping every favorite already there. Returns how many were new. */
    fun addFavorites(links: List<GeckoRules.Link>): Int {
        val fresh = links.filterNot { isBookmarked(it.url) }.map { it.copy(profile = saved.currentProfile) }
        update(saved.copy(bookmarks = (saved.bookmarks + fresh).take(GeckoRules.MAX_BOOKMARKS)))
        return fresh.size
    }

    /** Applies settings chosen from a Firefox import. Turning off history here keeps the history already saved. */
    fun applyImportedSettings(changes: List<GeckoImport.SettingChange>) {
        if (changes.isEmpty()) return
        val next = changes.fold(saved.settings, GeckoImport::apply)
        update(saved.copy(settings = next))
        runtime.settings.contentBlocking.setEnhancedTrackingProtectionLevel(etp(next.tracking))
        runtime.settings.preferredColorScheme = colors(next.colors)
    }

    val importer by lazy { GeckoImporter(app, this) }

    fun addProfile(name: String): Boolean {
        val clean = GeckoRules.cleanProfileName(name)
        if (clean.isEmpty() || saved.profiles.size >= GeckoRules.MAX_PROFILES) return false
        val id = UUID.randomUUID().toString()
        update(saved.copy(profiles = saved.profiles + GeckoRules.Profile(id, clean)))
        switchProfile(id)
        return true
    }

    fun switchProfile(id: String) {
        if (saved.profiles.none { it.id == id } || id == saved.currentProfile) return
        active?.let { sessions[it] }?.let { if (active !in playing) it.setActive(false); it.setFocused(false) }
        update(saved.copy(currentProfile = id))
        active = null
        val mine = tabs
        if (mine.isEmpty()) newTab() else select(mine.last().id)
    }

    /** Removes a profile, its tabs, favorites and history, and its cookies and site data. */
    fun deleteProfile(id: String) {
        if (id == GeckoRules.DEFAULT_PROFILE) return
        if (saved.currentProfile == id) switchProfile(GeckoRules.DEFAULT_PROFILE)
        saved.tabs.filter { it.profile == id }.forEach { closeSession(it.id); states.remove(it.id); pages.remove(it.id) }
        update(saved.copy(tabs = saved.tabs.filter { it.profile != id }, profiles = saved.profiles.filter { it.id != id },
            bookmarks = saved.bookmarks.filter { it.profile != id }, history = saved.history.filter { it.profile != id }, closed = saved.closed.filter { it.profile != id }))
        runtime.storageController.clearDataForSessionContext(GeckoRules.contextId(id))
    }

    /** Clears this profile's cookies and site data (and, with [history], its visits). Other profiles are untouched. */
    fun clearData(history: Boolean) {
        runtime.storageController.clearDataForSessionContext(GeckoRules.contextId(saved.currentProfile))
        runtime.storageController.clearData(StorageController.ClearFlags.ALL_CACHES)
        if (history) update(saved.copy(history = saved.history.filter { it.profile != saved.currentProfile }, closed = saved.closed.filter { it.profile != saved.currentProfile }))
        notice = "Cleared."
    }

    fun setTracking(level: String) {
        update(saved.copy(settings = saved.settings.copy(tracking = if (level == GeckoRules.TRACKING_STRICT) level else GeckoRules.TRACKING_STANDARD)))
        runtime.settings.contentBlocking.setEnhancedTrackingProtectionLevel(etp(saved.settings.tracking))
    }
    fun setColors(value: String) { update(saved.copy(settings = saved.settings.copy(colors = value))); runtime.settings.preferredColorScheme = colors(value) }
    fun setKeepHistory(on: Boolean) = update(saved.copy(settings = saved.settings.copy(keepHistory = on), history = if (on) saved.history else emptyList()))
    fun setSearch(template: String) = update(saved.copy(settings = saved.settings.copy(search = template)))
    fun setDesktopSites(on: Boolean) {
        update(saved.copy(settings = saved.settings.copy(desktopSites = on)))
        sessions.values.forEach { session ->
            session.settings.userAgentMode = if (on) GeckoSessionSettings.USER_AGENT_MODE_DESKTOP else GeckoSessionSettings.USER_AGENT_MODE_MOBILE
            session.settings.viewportMode = if (on) GeckoSessionSettings.VIEWPORT_MODE_DESKTOP else GeckoSessionSettings.VIEWPORT_MODE_MOBILE
            session.reload()
        }
    }

    // Extensions ------------------------------------------------------------------------------------------------------

    private val extensionPrompts = object : WebExtensionController.PromptDelegate {
        override fun onInstallPromptRequest(extension: WebExtension, permissions: Array<out String>, origins: Array<out String>, dataCollectionPermissions: Array<out String>): GeckoResult<WebExtension.PermissionPromptResponse> {
            val result = GeckoResult<WebExtension.PermissionPromptResponse>()
            asks.add(Ask.Install(extension.metaData.name ?: extension.id, permissions.toList(), origins.toList(), dataCollectionPermissions.toList()) { yes ->
                result.complete(WebExtension.PermissionPromptResponse(yes, false, false))
            })
            return result
        }
        override fun onUpdatePrompt(extension: WebExtension, newPermissions: Array<out String>, newOrigins: Array<out String>, newDataCollectionPermissions: Array<out String>): GeckoResult<AllowOrDeny> {
            val result = GeckoResult<AllowOrDeny>()
            asks.add(Ask.Install(extension.metaData.name ?: extension.id, newPermissions.toList(), newOrigins.toList(), newDataCollectionPermissions.toList()) { yes ->
                result.complete(if (yes) AllowOrDeny.ALLOW else AllowOrDeny.DENY)
            })
            return result
        }
    }

    private fun openPopup(extension: WebExtension): GeckoResult<GeckoSession> {
        popup?.session?.close()
        val session = GeckoSession(GeckoSessionSettings.Builder().useTrackingProtection(true).build())
        session.promptDelegate = Prompts(active ?: "")
        session.navigationDelegate = navigation("")
        session.contentDelegate = object : GeckoSession.ContentDelegate { override fun onCloseRequest(session: GeckoSession) { closePopup() } }
        session.open(runtime)
        popup = Popup(extension.id, session)
        return GeckoResult.fromValue(session)
    }
    fun closePopup() { popup?.session?.close(); popup = null }

    private fun remember(extension: WebExtension, action: WebExtension.Action) { actions[extension.id] = action; extensions = extensions.map { if (it.id == extension.id) it.copy(action = action) else it } }

    private val sessionActions = object : WebExtension.ActionDelegate {
        override fun onBrowserAction(extension: WebExtension, session: GeckoSession?, action: WebExtension.Action) { if (session == null || session === this@GeckoBrowser.session) remember(extension, action) }
        override fun onPageAction(extension: WebExtension, session: GeckoSession?, action: WebExtension.Action) { if (session == null || session === this@GeckoBrowser.session) remember(extension, action) }
        override fun onTogglePopup(extension: WebExtension, action: WebExtension.Action) = openPopup(extension)
        override fun onOpenPopup(extension: WebExtension, action: WebExtension.Action) = openPopup(extension)
    }

    private val extensionTabs = object : WebExtension.TabDelegate {
        override fun onNewTab(source: WebExtension, createDetails: WebExtension.CreateTabDetails): GeckoResult<GeckoSession>? =
            openedTab(createDetails.url ?: BrowserRules.HOME, saved.currentProfile)?.let { GeckoResult.fromValue(it) }
        override fun onOpenOptionsPage(source: WebExtension) { source.metaData.optionsPageUrl?.let { openOptions(it) } }
    }

    fun openOptions(url: String) { openedTab(url, saved.currentProfile)?.let { session -> session.open(runtime); session.loadUri(url) } }

    private fun listed(native: WebExtension) = Extension(native.id, native.metaData.name ?: native.id, native.metaData.version, native.metaData.enabled,
        native.metaData.description.orEmpty(), native.metaData.optionsPageUrl, actions[native.id], native)

    fun refreshExtensions() {
        runtime.webExtensionController.list().accept({ list ->
            val mine = list.orEmpty().filter { !it.isBuiltIn }
            mine.forEach { wireExtension(it) }
            extensions = mine.map(::listed).sortedBy { it.name.lowercase() }
        }, { notice = "Extensions couldn't be listed." })
    }

    private fun wireExtension(extension: WebExtension) {
        extension.setActionDelegate(sessionActions)
        extension.tabDelegate = extensionTabs
        sessions.values.forEach { it.webExtensionController.setActionDelegate(extension, sessionActions) }
    }

    /** A page offered an extension file. Only Mozilla's add-ons site can install one. */
    private fun offerInstall(url: String) {
        if (GeckoRules.installable(url)) install(url) else notice = "Raticode only installs extensions from addons.mozilla.org."
    }

    fun install(url: String) {
        if (!GeckoRules.installable(url) || url in installing) return
        installing = installing + url
        runtime.webExtensionController.install(url, WebExtensionController.INSTALLATION_METHOD_MANAGER).accept({ extension ->
            installing = installing - url
            if (extension != null) { notice = "Added ${extension.metaData.name ?: "the extension"}."; refreshExtensions() }
        }, { error ->
            installing = installing - url
            notice = (error as? WebExtension.InstallException)?.let { GeckoRules.installError(it.code) } ?: "The extension couldn't be installed."
        })
    }

    fun uninstall(extension: Extension) {
        runtime.webExtensionController.uninstall(extension.native).accept({ actions.remove(extension.id); notice = "Removed ${extension.name}."; refreshExtensions() }, { notice = "${extension.name} couldn't be removed." })
    }
    fun setEnabled(extension: Extension, on: Boolean) {
        val controller = runtime.webExtensionController
        (if (on) controller.enable(extension.native, WebExtensionController.EnableSource.USER) else controller.disable(extension.native, WebExtensionController.EnableSource.USER))
            .accept({ refreshExtensions() }, { notice = "${extension.name} couldn't be changed." })
    }
    fun updateExtensions() {
        extensions.forEach { item -> runtime.webExtensionController.update(item.native).accept({ refreshExtensions() }, { }) }
        notice = "Checked for updates."
    }

    /** Asks Mozilla's catalogue on a background thread. Only the words searched for leave the phone. */
    fun search(query: String, done: (List<GeckoRules.Listing>?) -> Unit) {
        io.execute {
            val found = fetch(GeckoRules.searchUrl(query))?.let(GeckoRules::parseSearch)
            main.post { done(found) }
        }
    }

    /** The body of an https answer from Mozilla's add-ons site, or null when it failed. Call off the main thread. */
    internal fun fetch(url: String): String? = runCatching {
        require(url.startsWith("https://addons.mozilla.org/"))
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.connectTimeout = 15000; connection.readTimeout = 15000
        try { if (connection.responseCode == 200) connection.inputStream.bufferedReader().use { it.readText() } else null } finally { connection.disconnect() }
    }.getOrNull()
}
