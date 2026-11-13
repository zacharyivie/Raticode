package dev.raticode.mobile.ui

import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.displayCutout
import androidx.compose.foundation.layout.imeAnimationTarget
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.windowInsetsPadding
import android.view.View
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.offset

/** The part of a window inset that still covers a view, given how far the view's bottom is above the window's. */
internal object KeyboardInsets {
    fun bottom(windowInset: Int, windowHeight: Int, viewBottom: Int): Int =
        (windowInset - (windowHeight - viewBottom).coerceAtLeast(0)).coerceAtLeast(0)

    /**
     * Whether [page] makes room for the keyboard in one step, at the height the keyboard is heading for, rather than
     * frame by frame as it slides. A web page scrolls its focused field into view when its view first gets shorter;
     * shrunk a little at a time, it does that too early and the rest of the slide covers the field's lower part. Each
     * step also lays the whole page out again. The keyboard slides over the room already made.
     */
    fun resizesOnce(page: String): Boolean = page == dev.raticode.mobile.browser.VideoFullscreenRules.BROWSER_PAGE
}

/**
 * Like `safeDrawingPadding().imePadding()`, but the keyboard is counted once whatever the phone does with it.
 *
 * Raticode draws edge to edge, so Android reports the keyboard and the screen pads for it. Some phones also shrink the
 * window above the keyboard, and padding a window that already ends at the keyboard's top counts it twice: the screen's
 * bottom rises a keyboard's height above the keyboard, the chat above it has no room left and the message box is
 * squeezed until its words are cut off. Here the bottom padding is only what the system bars and the keyboard still
 * cover of this view. Every safe-drawing inset is consumed, so nothing inside pads for them again.
 *
 * With [keyboardAtTarget] the keyboard counts at the height it is opening or closing to, not where its slide is now
 * (see [KeyboardInsets.resizesOnce]).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun Modifier.safeDrawingPaddingOnce(keyboardAtTarget: Boolean = false): Modifier {
    val view = LocalView.current
    val safeDrawing = WindowInsets.safeDrawing
    val insets = if (keyboardAtTarget) WindowInsets.systemBars.union(WindowInsets.displayCutout).union(WindowInsets.imeAnimationTarget) else safeDrawing
    // Where this view ends in its window, kept from Android's own layout: during Compose's measuring the view still has
    // its old size, so reading it there would miss a window that just shrank.
    var viewBottom by remember(view) { mutableIntStateOf(-1) }
    var windowHeight by remember(view) { mutableIntStateOf(-1) }
    DisposableEffect(view) {
        val location = IntArray(2)
        fun update() {
            view.getLocationInWindow(location)
            viewBottom = location[1] + view.height
            windowHeight = view.rootView.height
        }
        val listener = View.OnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> update() }
        view.addOnLayoutChangeListener(listener)
        update()
        onDispose { view.removeOnLayoutChangeListener(listener) }
    }
    return this
        .layout { measurable, constraints ->
            val inset = insets.getBottom(this)
            val bottom = if (viewBottom <= 0 || windowHeight <= 0) inset else KeyboardInsets.bottom(inset, windowHeight, viewBottom)
            val placeable = measurable.measure(constraints.offset(vertical = -bottom))
            val height = (placeable.height + bottom).coerceIn(constraints.minHeight, if (constraints.hasBoundedHeight) constraints.maxHeight else Constraints.Infinity)
            layout(placeable.width, height) { placeable.place(0, 0) }
        }
        .windowInsetsPadding(insets.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal))
        .consumeWindowInsets(insets)
        // A closing keyboard's target is already zero while it still covers part of the screen; nothing inside pads for it.
        .consumeWindowInsets(safeDrawing)
}
