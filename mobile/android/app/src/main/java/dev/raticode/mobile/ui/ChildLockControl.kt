package dev.raticode.mobile.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.provider.Settings
import androidx.annotation.RequiresApi
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.systemGestureExclusion
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.ui.input.pointer.util.addPointerInputChange
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalAccessibilityManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.role
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.childlock.ChildLock
import dev.raticode.mobile.childlock.ChildLockRuntime
import dev.raticode.mobile.childlock.EdgeHandle
import dev.raticode.mobile.childlock.UnlockOutcome
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.roundToInt

/** How long the explanation stays after a touch on a locked screen. */
private const val HINT_MS = 4_000L

/** Material's open padlock; the core icon set only has the closed one. */
private val OpenPadlock: ImageVector = ImageVector.Builder("OpenPadlock", 24.dp, 24.dp, 24f, 24f).addPath(
    addPathNodes("M12,17c1.1,0 2,-0.9 2,-2s-0.9,-2 -2,-2 -2,0.9 -2,2 0.9,2 2,2zM18,8h-1V6c0,-2.76 -2.24,-5 -5,-5S7,3.24 7,6h1.9c0,-1.71 1.39,-3.1 3.1,-3.1 1.71,0 3.1,1.39 3.1,3.1v2H6c-1.1,0 -2,0.9 -2,2v10c0,1.1 0.9,2 2,2h12c1.1,0 2,-0.9 2,-2V10c0,-1.1 -0.9,-2 -2,-2zM18,20H6V10h12v10z"),
    fill = SolidColor(Color.Black)).build()

/**
 * The child lock's handle on the right edge of every Raticode page, while the lock is off. Once the lock is on, the lock
 * screen shows the same handle in the same place with a closed padlock (see [ChildLockScreen]).
 */
@OptIn(ExperimentalLayoutApi::class)
@RequiresApi(29)
@Composable
internal fun ChildLockControl(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val lock = remember { ChildLockRuntime.get(context) }
    if (lock.locked || WindowInsets.isImeVisible) return
    ChildLockHandle(locked = false, onAction = { ChildLockRuntime.engage(context) }, modifier = modifier)
}

/**
 * A tab flush with the right edge holding the padlock. It starts tucked away, leaving a thin mark and a small tap target
 * on the edge; a tap there (or a pull out from the edge) slides it in, and a swipe right, or a few seconds untouched,
 * slides it away again. Unlocked, one tap opens it and a second tap calls [onAction], so a stray touch doesn't lock the
 * app. Locked, only holding it for [EdgeHandle.HOLD_MS] calls [onAction]. TalkBack gets both as single actions, from the
 * handle and from its edge mark.
 */
@Composable
internal fun ChildLockHandle(locked: Boolean, onAction: () -> Unit, modifier: Modifier = Modifier, asking: Boolean = false) {
    val shown = EdgeHandle.shown
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    val latest by rememberUpdatedState(onAction)
    var armed by remember { mutableStateOf(false) }
    var holding by remember { mutableStateOf(false) }
    val ring = remember { Animatable(0f) }
    // 0 is fully out, 1 is fully past the edge.
    val away = remember { Animatable(if (shown) 0f else 1f) }
    var width by remember { mutableIntStateOf(0) }
    val shadowRoom = with(density) { 12.dp.roundToPx() }
    var releaseVelocity by remember { mutableFloatStateOf(0f) }
    var settles by remember { mutableIntStateOf(0) }
    // Compose scales this spring by Android's animation setting, so with animations off the handle just appears.
    LaunchedEffect(shown, settles) {
        if (!shown) armed = false
        val velocity = releaseVelocity
        releaseVelocity = 0f
        away.animateTo(if (shown) 0f else 1f, spring(Spring.DampingRatioNoBouncy, Spring.StiffnessMediumLow), velocity)
    }
    LaunchedEffect(armed) { if (armed) { delay(EdgeHandle.ARMED_MS); armed = false } }
    val accessibility = LocalAccessibilityManager.current
    val idle = remember(accessibility) {
        accessibility?.calculateRecommendedTimeoutMillis(EdgeHandle.IDLE_MS, containsIcons = true, containsText = true, containsControls = true) ?: EdgeHandle.IDLE_MS
    }
    LaunchedEffect(shown, EdgeHandle.touches, armed, holding, asking) {
        if (shown && EdgeHandle.mayIdleHide(armed, holding, asking)) { delay(idle); EdgeHandle.hide() }
    }
    // Locked from the start shows a closed padlock; just locked, it opens on the lock screen and closes.
    var closed by remember { mutableStateOf(locked && !EdgeHandle.justLocked) }
    LaunchedEffect(locked) {
        if (locked && !closed) delay(180)
        closed = locked
        if (locked) EdgeHandle.justLocked = false
    }
    val mainLabel = if (locked) "Unlock" else "Turn on child lock"

    Box(modifier.safeDrawingPadding(), contentAlignment = Alignment.CenterEnd) {
        val markAlpha by animateFloatAsState(if (shown) 0f else 1f, label = "edge mark")
        // What stays on the edge while the handle is away: a thin mark and a slightly wider target to tap.
        Box(Modifier.size(24.dp, 80.dp).testTag("childLockReveal").then(if (shown) Modifier.clearAndSetSemantics { } else Modifier
            .pointerInput(Unit) {
                awaitEachGesture {
                    val down = awaitFirstDown()
                    var dx = 0f
                    var dy = 0f
                    while (true) {
                        val change = awaitPointerEvent().changes.firstOrNull { it.id == down.id } ?: return@awaitEachGesture
                        if (!change.pressed) {
                            if (!change.isConsumed) { change.consume(); EdgeHandle.show() }
                            return@awaitEachGesture
                        }
                        val delta = change.positionChange()
                        dx += delta.x
                        dy += delta.y
                        // Pulled out from the edge.
                        if (dx < -viewConfiguration.touchSlop) { change.consume(); EdgeHandle.show(); return@awaitEachGesture }
                        // A scroll or a swipe towards the edge isn't a request for the lock.
                        if (abs(dy) > viewConfiguration.touchSlop || dx > viewConfiguration.touchSlop) return@awaitEachGesture
                    }
                }
            }
            .clearAndSetSemantics {
                contentDescription = if (locked) "Child lock is on. Show the lock" else "Show child lock"
                role = Role.Button
                onClick(if (locked) "Show the lock" else "Show child lock") { EdgeHandle.show(); true }
                customActions = listOf(CustomAccessibilityAction(mainLabel) { latest(); true })
            }), contentAlignment = Alignment.CenterEnd) {
            Box(Modifier.padding(end = 3.dp).size(4.dp, 36.dp).alpha(markAlpha)
                .background(MaterialTheme.colorScheme.onSurface.copy(alpha = 0.45f), RoundedCornerShape(2.dp)))
        }

        Surface(shape = RoundedCornerShape(topStart = 28.dp, bottomStart = 28.dp),
            color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = if (locked || armed) 1f else 0.9f),
            contentColor = MaterialTheme.colorScheme.onSurfaceVariant, shadowElevation = 3.dp,
            modifier = Modifier.onSizeChanged { width = it.width }
                .offset { IntOffset(((width + shadowRoom) * away.value).roundToInt(), 0) }
                .then(if (shown) Modifier.systemGestureExclusion() else Modifier)
                .testTag(if (locked) "childLockUnlock" else "childLockButton")
                .pointerInput(locked) {
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        EdgeHandle.touched()
                        val tracker = VelocityTracker().apply { addPointerInputChange(down) }
                        val hold = if (!locked) null else scope.launch {
                            holding = true
                            launch { ring.animateTo(1f, tween(EdgeHandle.HOLD_MS.toInt(), easing = LinearEasing)) }
                            // A real wait, not the ring: with animations off the ring fills at once, the hold doesn't.
                            delay(EdgeHandle.HOLD_MS)
                            latest()
                        }
                        var moved = 0f
                        // Tracked here, not read back from the animation, whose updates land a frame later.
                        var share = away.value
                        var dragging = false
                        var tapped = false
                        while (true) {
                            val change = awaitPointerEvent().changes.firstOrNull { it.id == down.id } ?: break
                            if (!change.pressed) { tapped = !dragging && !change.isConsumed; break }
                            tracker.addPointerInputChange(change)
                            val dx = change.positionChange().x
                            moved += dx
                            // The first step of a drag includes the slop, so the handle stays under the finger.
                            var step = dx
                            if (!dragging && abs(moved) > viewConfiguration.touchSlop) { dragging = true; step = moved; hold?.cancel() }
                            if (dragging) {
                                change.consume()
                                val span = (width + shadowRoom).coerceAtLeast(1)
                                share = (share + step / span).coerceIn(0f, 1f)
                                val to = share
                                scope.launch { away.snapTo(to) }
                            }
                        }
                        hold?.cancel()
                        holding = false
                        scope.launch { ring.snapTo(0f) }
                        if (dragging) {
                            val span = (width + shadowRoom).coerceAtLeast(1)
                            val velocity = tracker.calculateVelocity().x
                            val hides = EdgeHandle.hidesAfterDrag(share, velocity / density.density)
                            releaseVelocity = velocity / span
                            if (hides) EdgeHandle.hide() else EdgeHandle.show()
                            settles++
                        } else if (tapped && !locked) {
                            if (armed) { armed = false; latest() } else armed = true
                        }
                    }
                }
                .clearAndSetSemantics {
                    if (!shown) return@clearAndSetSemantics
                    contentDescription = when { locked -> "Child lock is on. Press and hold to unlock"; armed -> "Child lock. Tap again to turn it on"; else -> "Child lock" }
                    role = Role.Button
                    // TalkBack can't hold a touch; its action goes to the same screen lock prompt.
                    onClick(mainLabel) { EdgeHandle.touched(); latest(); true }
                    customActions = listOf(CustomAccessibilityAction("Hide the lock") { EdgeHandle.hide(); true })
                }) {
            Row(Modifier.height(56.dp).padding(start = 14.dp, end = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(34.dp), contentAlignment = Alignment.Center) {
                    Crossfade(closed, label = "padlock") { Icon(if (it) Icons.Default.Lock else OpenPadlock, null, Modifier.size(24.dp)) }
                    if (locked && (ring.value > 0f || asking))
                        CircularProgressIndicator(progress = { if (asking) 1f else ring.value }, Modifier.matchParentSize(), strokeWidth = 3.dp)
                }
                AnimatedVisibility(armed) { Text("Tap to lock", Modifier.padding(start = 6.dp), style = MaterialTheme.typography.labelLarge) }
            }
        }
    }
}

/** Explains why the lock wouldn't turn on. Lives at the root of the app so the More row and the button share it. */
@RequiresApi(29)
@Composable
internal fun ChildLockRefusal() {
    val context = LocalContext.current
    val lock = remember { ChildLockRuntime.get(context) }
    when (lock.refusal) {
        ChildLock.TurnOn.NEEDS_SCREEN_LOCK -> AlertDialog(onDismissRequest = lock::dismissRefusal, title = { Text("Set a screen lock first") },
            text = { Text("Child lock needs your phone's pattern, PIN or password to turn off. This phone doesn't have one yet, so the lock wasn't turned on.") },
            confirmButton = { TextButton(onClick = {
                lock.dismissRefusal()
                try { context.startActivity(Intent(Settings.ACTION_SECURITY_SETTINGS)) } catch (_: ActivityNotFoundException) { }
            }) { Text("Open Android settings") } },
            dismissButton = { TextButton(onClick = lock::dismissRefusal) { Text("Not now") } })
        ChildLock.TurnOn.NOT_SAVED -> AlertDialog(onDismissRequest = lock::dismissRefusal, title = { Text("Child lock is off") },
            text = { Text("Raticode couldn't save the lock on this phone, so it wasn't turned on. Free up some storage and try again.") },
            confirmButton = { TextButton(onClick = lock::dismissRefusal) { Text("OK") } })
        else -> Unit
    }
}

/**
 * What shows over Raticode while it is locked: only the edge handle, now with a closed padlock, so a video stays
 * watchable. Every other touch lands on this screen and goes no further; a touch brings up the explanation for a few
 * seconds.
 */
@RequiresApi(29)
@Composable
internal fun ChildLockScreen(notice: UnlockOutcome?, asking: Boolean, onUnlock: () -> Unit, onOpenSettings: () -> Unit) {
    var touches by remember { mutableIntStateOf(0) }
    var hint by remember { mutableStateOf(true) }
    LaunchedEffect(touches) { hint = true; delay(HINT_MS); hint = false }
    // A failed or cancelled prompt leaves the lock out a little longer to try again.
    LaunchedEffect(notice) { if (notice != null) EdgeHandle.touched() }
    Box(Modifier.fillMaxSize().testTag("childLockScreen").pointerInput(Unit) {
        awaitEachGesture { awaitFirstDown(requireUnconsumed = false); touches++ }
    }) {
        val message = notice?.notice ?: EdgeHandle.lockedHint(EdgeHandle.shown)
        AnimatedVisibility(hint || notice != null, Modifier.align(Alignment.TopEnd).safeDrawingPadding().padding(12.dp).widthIn(max = 300.dp)) {
            Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface, contentColor = MaterialTheme.colorScheme.onSurface, shadowElevation = 2.dp) {
                Column(Modifier.padding(12.dp).testTag("childLockNotice"), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(message, style = MaterialTheme.typography.bodyMedium)
                    if (notice?.offerSettings == true) TextButton(onClick = onOpenSettings) { Text("Open Android settings") }
                }
            }
        }
        ChildLockHandle(locked = true, onAction = onUnlock, asking = asking, modifier = Modifier.align(Alignment.CenterEnd))
    }
}
