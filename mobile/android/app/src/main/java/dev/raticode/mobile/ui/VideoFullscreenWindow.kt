package dev.raticode.mobile.ui

import android.os.Build
import android.view.WindowManager
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import dev.raticode.mobile.browser.VideoFullscreenRules

/**
 * Gives a full-screen video the phone's whole screen while [on]: Android's status and navigation bars hide until the user
 * swipes from an edge (they then hide again by themselves), the page may draw under a camera cutout, and the screen turns
 * to [orientation]. Off, or when this leaves the screen, the bars, cutout and orientation go back, except while Android is
 * rebuilding the screen, where the new one takes over without the screen turning in between.
 */
@Composable
internal fun VideoFullscreenWindow(on: Boolean, orientation: Int) {
    val activity = LocalContext.current.findActivity() ?: return
    VideoFullscreenBars(on)
    // A new orientation (an upright video's size arriving, say) simply replaces the last one; only leaving restores. The
    // first pass always asks, as a rebuilt screen inherits whatever the last one asked for.
    val asked = remember(activity) { intArrayOf(NOT_ASKED) }
    SideEffect {
        if (asked[0] != orientation) { asked[0] = orientation; activity.requestedOrientation = orientation }
    }
    DisposableEffect(activity) {
        onDispose {
            if (!activity.isChangingConfigurations && asked[0] != NOT_ASKED && asked[0] != VideoFullscreenRules.RESTORE_ORIENTATION)
                activity.requestedOrientation = VideoFullscreenRules.RESTORE_ORIENTATION
        }
    }
}

/**
 * The bars and cutout part of [VideoFullscreenWindow], for any Raticode screen over a full-screen video. The see-through
 * child lock uses it alone: Android's bars follow the window on top, while the orientation follows the video's screen.
 */
@Composable
internal fun VideoFullscreenBars(on: Boolean) {
    val activity = LocalContext.current.findActivity() ?: return
    DisposableEffect(activity, on) {
        val window = activity.window
        val controller = WindowCompat.getInsetsController(window, window.decorView)
        val cutout = if (Build.VERSION.SDK_INT >= 28) window.attributes.layoutInDisplayCutoutMode else null
        if (on) {
            controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            controller.hide(WindowInsetsCompat.Type.systemBars())
            if (Build.VERSION.SDK_INT >= 28) window.attributes = window.attributes.also {
                it.layoutInDisplayCutoutMode = if (Build.VERSION.SDK_INT >= 30) WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
                    else WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        }
        onDispose {
            if (on && !activity.isChangingConfigurations) {
                controller.show(WindowInsetsCompat.Type.systemBars())
                if (Build.VERSION.SDK_INT >= 28 && cutout != null) window.attributes = window.attributes.also { it.layoutInDisplayCutoutMode = cutout }
            }
        }
    }
}

/** Not an orientation Android has: nothing asked yet. */
private const val NOT_ASKED = Int.MIN_VALUE
