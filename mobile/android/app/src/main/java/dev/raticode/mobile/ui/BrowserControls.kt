package dev.raticode.mobile.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.SystemClock
import android.view.accessibility.AccessibilityManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalAccessibilityManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import dev.raticode.mobile.browser.BrowserControlsMotion
import dev.raticode.mobile.browser.BrowserRules
import kotlinx.coroutines.delay

/** Compose's view of [BrowserControlsMotion], fed by the engine's scroll reports on the main thread. */
@Stable
internal class BrowserControlsState {
    private val motion = BrowserControlsMotion()
    private fun now() = SystemClock.uptimeMillis()
    var expanded by mutableStateOf(true)
        private set
    private fun sync() { expanded = motion.expanded }

    fun scrolledTo(y: Int) { motion.scrolled(y, now()); sync() }
    fun reveal() { motion.reveal(now()); sync() }
    fun pageChanged() { motion.pageChanged(now()); sync() }
    fun touched() = motion.touched(now())
    fun hold(on: Boolean) { motion.hold(on, now()); sync() }
    fun idleRemaining(timeout: Long) = motion.idleRemaining(now(), timeout)
    fun idled() { motion.idled(now()); sync() }
}

/**
 * Runs the rules around the toolbar and returns whether it is open. [held] keeps it open; so does a screen reader, which
 * finds controls by exploring the screen and can't scroll a page to bring them back. The idle wait follows the user's
 * Android "time to take action" setting. [revealRequests] counts taps on the bottom bar's Browser item.
 */
@Composable
internal fun browserControlsOpen(controls: BrowserControlsState, held: Boolean, loading: Boolean, tab: Any?, revealRequests: Int): Boolean {
    val context = LocalContext.current
    val manager = remember(context) { context.getSystemService(AccessibilityManager::class.java) }
    var exploring by remember(manager) { mutableStateOf(manager.isTouchExplorationEnabled) }
    DisposableEffect(manager) {
        val listener = AccessibilityManager.TouchExplorationStateChangeListener { exploring = it }
        manager.addTouchExplorationStateChangeListener(listener)
        exploring = manager.isTouchExplorationEnabled
        onDispose { manager.removeTouchExplorationStateChangeListener(listener) }
    }
    val keepOpen = held || exploring
    LaunchedEffect(keepOpen) { controls.hold(keepOpen) }
    LaunchedEffect(tab) { controls.pageChanged() }
    LaunchedEffect(loading) { if (loading) controls.pageChanged() }
    LaunchedEffect(revealRequests) { if (revealRequests > 0) controls.reveal() }

    val open = controls.expanded
    val accessibility = LocalAccessibilityManager.current
    LaunchedEffect(open, keepOpen, loading) {
        if (!open || keepOpen || loading) return@LaunchedEffect
        val base = BrowserControlsMotion.idleMillis
        if (base == Long.MAX_VALUE) return@LaunchedEffect
        val timeout = accessibility?.calculateRecommendedTimeoutMillis(base, containsIcons = true, containsText = true, containsControls = true) ?: base
        controls.touched()
        while (true) {
            val wait = controls.idleRemaining(timeout)
            if (wait <= 0) { controls.idled(); break }
            delay(wait)
        }
    }
    return open || keepOpen
}

/**
 * The browser's top controls: one 48 dp row, or a 28 dp strip with the site's name while the user reads. Both of
 * Raticode's browsers draw this. A crossfade rather than a slide, so the page below is resized once and not on every
 * frame, which would make a page's layout and Gecko's surface flicker.
 */
@Composable
internal fun BrowserToolbar(
    controls: BrowserControlsState, expanded: Boolean, prefix: String, url: String, desktop: Boolean, loading: Boolean, progress: Int,
    canBack: Boolean, canForward: Boolean, canReload: Boolean, tabCount: Int, showForward: Boolean,
    onPageBack: (() -> Unit)?, onEdit: () -> Unit, onBack: () -> Unit, onForward: () -> Unit, onStop: () -> Unit, onReload: () -> Unit, onTabs: () -> Unit, onMenu: () -> Unit,
    onBackLong: (() -> Unit)? = null, onForwardLong: (() -> Unit)? = null, privateTab: Boolean = false, editor: (@Composable RowScope.() -> Unit)? = null,
) {
    val host = BrowserRules.host(url).ifBlank { url }
    Surface(color = MaterialTheme.colorScheme.surface, shadowElevation = 1.dp) {
        Box(Modifier.fillMaxWidth()) {
            Crossfade(expanded || editor != null, animationSpec = tween(120), label = "browser controls") { open ->
                if (!open) Row(Modifier.fillMaxWidth().heightIn(min = 28.dp).clickable(role = Role.Button, onClickLabel = "Show browser controls", onClick = controls::reveal)
                    .testTag("${prefix}ControlsStrip").semantics { contentDescription = "Browser controls hidden. ${if (url == BrowserRules.HOME) "New tab" else url}. Double tap to show" }
                    .padding(horizontal = 16.dp), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
                    if (privateTab) PrivateMark(Modifier.padding(end = 6.dp))
                    SecurityMark(url, 12)
                    Spacer(Modifier.width(4.dp))
                    SiteName(host, MaterialTheme.typography.labelSmall)
                } else Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(horizontal = 4.dp).pointerInput(Unit) {
                    awaitPointerEventScope { while (true) { awaitPointerEvent(PointerEventPass.Initial); controls.touched() } }
                }, verticalAlignment = Alignment.CenterVertically) {
                    if (editor != null) editor()
                    else {
                        if (onPageBack != null) IconButton(onClick = onPageBack, modifier = Modifier.testTag("moreBack")) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back to More") }
                        BrowserBarButton(ThreadIcons.PageBack, "Back", canBack, "${prefix}Back", onBackLong, onBack)
                        if (showForward && canForward) BrowserBarButton(ThreadIcons.PageForward, "Forward", true, "${prefix}Forward", onForwardLong, onForward)
                        Box(Modifier.weight(1f).heightIn(min = 48.dp).clickable(role = Role.Button, onClickLabel = "Edit address", onClick = onEdit).testTag("${prefix}Address")
                            .semantics { contentDescription = "Address. ${if (url == BrowserRules.HOME) "New tab" else url}${if (desktop) ". Desktop site" else ""}${if (privateTab) ". Private tab" else ""}. Double tap to edit" },
                            contentAlignment = Alignment.Center) {
                            Surface(shape = RoundedCornerShape(18.dp), color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxWidth().padding(horizontal = 2.dp)) {
                                Row(Modifier.heightIn(min = 36.dp).padding(horizontal = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                                    if (privateTab) PrivateMark(Modifier.padding(end = 6.dp))
                                    SecurityMark(url, 14)
                                    Spacer(Modifier.width(6.dp))
                                    if (url == BrowserRules.HOME) Text("Search or type an address", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    else SiteName(host, MaterialTheme.typography.bodySmall)
                                }
                            }
                        }
                        if (loading) BrowserBarButton(Icons.Filled.Close, "Stop loading", true, "${prefix}Stop", onClick = onStop)
                        else BrowserBarButton(ThreadIcons.Reload, "Reload", canReload, "${prefix}Reload", onClick = onReload)
                        TabCountButton(tabCount, "${prefix}Tabs", onTabs)
                        BrowserBarButton(Icons.Filled.MoreVert, "Browser menu", true, "${prefix}Menu", onClick = onMenu)
                    }
                }
            }
            if (loading) LinearProgressIndicator({ progress.coerceIn(5, 100) / 100f }, Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(2.dp))
        }
    }
}

@Composable
private fun PrivateMark(modifier: Modifier = Modifier) =
    Text("Private", modifier, color = MaterialTheme.colorScheme.tertiary, style = MaterialTheme.typography.labelSmall, maxLines = 1)

/** Asks whether [site] may use the camera or microphone, then asks Android too if the app doesn't hold that permission yet. */
@Composable
internal fun PageMediaDialog(site: String, camera: Boolean, microphone: Boolean, onAnswer: (Boolean) -> Unit) {
    val context = LocalContext.current
    val needed = listOfNotNull(Manifest.permission.CAMERA.takeIf { camera }, Manifest.permission.RECORD_AUDIO.takeIf { microphone }).toTypedArray()
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { results -> onAnswer(results.values.all { it }) }
    AlertDialog(onDismissRequest = { onAnswer(false) }, title = { Text("Let ${site.ifBlank { "this page" }} use your ${BrowserRules.mediaAsk(camera, microphone)}?") },
        text = { Text("Only for this request. Raticode doesn't remember your answer.") },
        confirmButton = { TextButton(onClick = {
            if (needed.all { ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED }) onAnswer(true) else launcher.launch(needed)
        }, Modifier.testTag("browserMediaAllow")) { Text("Allow") } },
        dismissButton = { TextButton(onClick = { onAnswer(false) }, Modifier.testTag("browserMediaBlock")) { Text("Block") } })
}

/** What a private tab does and doesn't hide, at the top of its start page. */
@Composable
internal fun PrivateNote(detail: String) {
    Surface(color = MaterialTheme.colorScheme.tertiaryContainer, shape = RoundedCornerShape(12.dp), modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp).fillMaxWidth().testTag("browserPrivateNote")) {
        Column(Modifier.padding(12.dp)) {
            Text("Private tab", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onTertiaryContainer)
            Text("Raticode doesn't keep this tab or its history, and its cookies and site data are removed when you close your last private tab. ${if (detail.isEmpty()) "" else "$detail "}" +
                "Downloads and bookmarks stay. Websites, your network and your account on a site can still see what you do.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onTertiaryContainer)
        }
    }
}

/** The end of a long name stays visible, because that is the part that says whose site it is. */
@Composable
private fun SiteName(host: String, style: androidx.compose.ui.text.TextStyle) =
    Text(host, style = style, maxLines = 1, softWrap = false, overflow = TextOverflow.StartEllipsis)

@Composable
private fun SecurityMark(url: String, size: Int) {
    val icon = when (BrowserRules.security(url)) {
        BrowserRules.Security.SECURE -> ThreadIcons.Lock
        BrowserRules.Security.BLANK -> ThreadIcons.Browser
        BrowserRules.Security.OTHER -> Icons.Filled.Info
    }
    Icon(icon, null, Modifier.size(size.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun BrowserBarButton(icon: ImageVector, label: String, enabled: Boolean, tag: String, onLong: (() -> Unit)? = null, onClick: () -> Unit) {
    val tint = if (enabled) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
    Box(Modifier.size(48.dp).testTag(tag).combinedClickable(enabled = enabled, role = Role.Button, onClickLabel = label,
        onLongClickLabel = onLong?.let { "Show history" }, onLongClick = onLong, onClick = onClick).semantics { contentDescription = label }, contentAlignment = Alignment.Center) {
        Icon(icon, null, Modifier.size(22.dp), tint = tint)
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun TabCountButton(count: Int, tag: String, onClick: () -> Unit) {
    Box(Modifier.size(48.dp).testTag(tag).combinedClickable(onClick = onClick, role = Role.Button).semantics { contentDescription = "Tabs, $count open" }, contentAlignment = Alignment.Center) {
        Surface(shape = RoundedCornerShape(5.dp), border = BorderStroke(1.5.dp, MaterialTheme.colorScheme.onSurface), color = MaterialTheme.colorScheme.surface, modifier = Modifier.size(20.dp)) {
            Box(contentAlignment = Alignment.Center) { Text("$count", style = MaterialTheme.typography.labelSmall) }
        }
    }
}
