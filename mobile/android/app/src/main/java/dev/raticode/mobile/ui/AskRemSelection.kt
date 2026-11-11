@file:OptIn(ExperimentalFoundationApi::class)

package dev.raticode.mobile.ui

import android.os.SystemClock
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.text.contextmenu.builder.item
import androidx.compose.foundation.text.contextmenu.data.TextContextMenuItem
import androidx.compose.foundation.text.contextmenu.data.TextContextMenuKeys
import androidx.compose.foundation.text.contextmenu.modifier.appendTextContextMenuComponents
import androidx.compose.foundation.text.contextmenu.modifier.filterTextContextMenuComponents
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.PointerInputScope
import androidx.compose.ui.platform.ClipEntry
import androidx.compose.ui.platform.Clipboard
import androidx.compose.ui.platform.LocalClipboard

/**
 * Selectable text whose selection menu also offers Ask Rem. Compose gives a menu item no way to read the selection, so
 * Ask Rem runs the menu's own Copy with the clipboard swapped for this one: the selected text comes here and never
 * reaches the phone's clipboard. Copy itself still goes to the real clipboard. With no [onAsk] this is a plain selection.
 */
@Composable
internal fun AskRemSelection(modifier: Modifier = Modifier, onAsk: ((String) -> Unit)?, content: @Composable () -> Unit) {
    if (onAsk == null) { SelectionContainer(modifier, content); return }
    val system = LocalClipboard.current
    val clipboard = remember(system) { SelectionClipboard(system) }
    val latest by rememberUpdatedState(onAsk)
    val copy = remember { arrayOfNulls<TextContextMenuItem>(1) }
    CompositionLocalProvider(LocalClipboard provides clipboard) {
        SelectionContainer(modifier
            // The filter sees the finished menu, which is the only place Copy can be reached from.
            .filterTextContextMenuComponents { component ->
                if (component is TextContextMenuItem && component.key == TextContextMenuKeys.CopyKey) copy[0] = component
                true
            }
            .appendTextContextMenuComponents {
                item(ASK_REM_KEY, "Ask Rem") {
                    val item = copy[0]
                    if (item == null) close()
                    else {
                        clipboard.takeNext { selected -> if (selected.isNotBlank()) latest(selected) }
                        item.onClick(this)
                    }
                }
            }, content)
    }
}

private val ASK_REM_KEY = Any()

/** The clipboard, except that the next copy after [takeNext] is handed over instead of stored. */
private class SelectionClipboard(private val real: Clipboard) : Clipboard {
    private var taker: ((String) -> Unit)? = null
    private var takeUntil = 0L

    /** Copy may find nothing selected and never arrive; a later, ordinary copy must not be swallowed by this one. */
    fun takeNext(action: (String) -> Unit) { taker = action; takeUntil = SystemClock.uptimeMillis() + 1000 }

    override suspend fun getClipEntry() = real.getClipEntry()

    override suspend fun setClipEntry(clipEntry: ClipEntry?) {
        val take = taker.takeIf { SystemClock.uptimeMillis() <= takeUntil }
        taker = null
        if (take == null) real.setClipEntry(clipEntry)
        else take(clipEntry?.clipData?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.text?.toString().orEmpty())
    }

    override val nativeClipboard get() = real.nativeClipboard
}

/**
 * Notes which row each selection starts on: the row of a press held for a long press. It watches without taking the
 * touch, so scrolling and selecting behave as before, and a press that scrolls is ignored.
 */
internal suspend fun PointerInputScope.watchLongPress(list: LazyListState, onRow: (Int) -> Unit) {
    awaitEachGesture {
        val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
        val row = list.layoutInfo.visibleItemsInfo.firstOrNull { down.position.y >= it.offset && down.position.y < it.offset + it.size }?.index
            ?: return@awaitEachGesture
        val ended = withTimeoutOrNull(viewConfiguration.longPressTimeoutMillis) {
            do {
                val event = awaitPointerEvent(PointerEventPass.Initial)
                val still = event.changes.all { it.pressed && (it.position - down.position).getDistance() <= viewConfiguration.touchSlop }
            } while (still)
            true
        }
        if (ended == null) onRow(row)
    }
}
