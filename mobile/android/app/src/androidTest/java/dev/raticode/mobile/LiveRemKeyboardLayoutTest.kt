package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.unit.Density
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Rem's chat with the keyboard open, in the real activity (edge to edge, adjustResize) against
 * `live-device-fixture.py --workspace --remote --desktop --search`, at normal and large text. The message box sits on the
 * keyboard with its words fully inside it, the messages stay visible above it and show the newest one, and the header's
 * name, status, search and Memory fit side by side. Pass the invitation as `keyboardInvitationFile`. No provider runs.
 *
 * A keyboard counted twice (the user's report: the box pushed up under the header, its words cut off, an empty chat)
 * fails every one of these.
 */
class LiveRemKeyboardLayoutTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: errors=${client.desktopErrors}; busy=${client.desktopBusy}; status=${client.status}", failure)
        }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    /** The keyboard's height in pixels, as Android reports it to the window. */
    private fun keyboard(): Int {
        var bottom = 0
        compose.runOnUiThread {
            bottom = ViewCompat.getRootWindowInsets(compose.activity.window.decorView)?.getInsets(WindowInsetsCompat.Type.ime())?.bottom ?: 0
        }
        return bottom
    }

    private fun showKeyboard() {
        compose.onNodeWithTag("remComposer").performClick()
        compose.runOnUiThread {
            val window = compose.activity.window
            WindowCompat.getInsetsController(window, window.decorView).show(WindowInsetsCompat.Type.ime())
        }
        compose.waitUntil(10000) { keyboard() > 0 && compose.onAllNodesWithTag("desktopNavigation").fetchSemanticsNodes().isEmpty() }
        // The keyboard slides in; measure once it has settled.
        var last = -1
        compose.waitUntil(5000) { val now = keyboard(); (now == last).also { last = now; Thread.sleep(150) } }
        compose.waitForIdle()
    }

    /** A node's bounds in window pixels. */
    private fun bounds(node: SemanticsNodeInteraction): Rect = node.fetchSemanticsNode().boundsInWindow

    /** Every broken expectation, so one run reports each layout that went wrong. */
    private val failures = mutableListOf<String>()
    private fun expect(message: String, condition: Boolean) { if (!condition) failures += message }

    private fun checkLayout(label: String, newest: String) {
        val density = compose.activity.resources.displayMetrics.density
        fun dp(px: Float) = px / density
        val windowHeight = compose.activity.window.decorView.height.toFloat()
        val windowWidth = compose.activity.window.decorView.width.toFloat()
        val keyboardTop = windowHeight - keyboard()
        val box = bounds(compose.onNodeWithTag("remComposer"))
        val list = bounds(compose.onNodeWithTag("remMessages"))
        val header = bounds(compose.onNodeWithTag("remStatusHeader"))
        val search = bounds(compose.onNodeWithTag("remSearch"))
        val memory = bounds(compose.onNodeWithTag("remMemory"))
        val facts = "$label: window=${windowWidth}x$windowHeight keyboardTop=$keyboardTop box=$box list=$list header=$header search=$search memory=$memory density=$density"

        // The message box rests on the keyboard, not a keyboard's height above it.
        expect("Message box below the keyboard's top. $facts", box.bottom <= keyboardTop + 1)
        expect("Message box floats ${dp(keyboardTop - box.bottom)} dp above the keyboard. $facts", dp(keyboardTop - box.bottom) <= 24f)
        // The messages fill the room between the header and the box, and show the newest one.
        expect("Messages squeezed to ${dp(list.height)} dp. $facts", dp(list.height) >= 160f)
        expect("Messages overlap the box. $facts", list.bottom <= box.top + 1)
        expect("The newest message is out of sight. $facts", runCatching { compose.onNodeWithText(newest, substring = true).assertIsDisplayed() }.isSuccess)

        // The box is tall enough for its words, up to four lines, with room above and below them: none is cut off.
        val layouts = mutableListOf<TextLayoutResult>()
        compose.onNodeWithTag("remComposer").fetchSemanticsNode().config[SemanticsActions.GetTextLayoutResult].action?.invoke(layouts)
        val text = layouts.first()
        val needed = text.getLineBottom(minOf(text.lineCount, 4) - 1) + 24 * density
        expect("Box is ${dp(box.height)} dp, its ${text.lineCount} lines need ${dp(needed)} dp. $facts", box.height >= needed)

        // The header: name and status, then search and Memory, side by side and on the screen.
        expect("Status runs under search. $facts", header.right <= search.left + 1)
        expect("Search runs under Memory. $facts", search.right <= memory.left + 1)
        expect("Memory runs off the screen. $facts", memory.right <= windowWidth + 1)
        for ((name, tag) in listOf("Search" to "remSearch", "Memory" to "remMemory")) {
            val touch = compose.onNodeWithTag(tag).fetchSemanticsNode().touchBoundsInRoot
            expect("$name is smaller than a touch target. $facts", dp(touch.width) >= 47f && dp(touch.height) >= 47f)
        }
        // The status keeps room to be read.
        expect("Status squeezed to ${dp(header.width)} dp. $facts", dp(header.width) >= 150f)
        compose.onNodeWithContentDescription("Memory").assertHasClickAction()
        expect("Header is ${dp(header.height)} dp tall. $facts", dp(header.height) <= 96f)
        expect("The status no longer opens Tasks. $facts", runCatching { compose.onNodeWithTag("remStatusHeader").assertHasClickAction() }.isSuccess)
    }

    /**
     * Some phones shrink the window for the keyboard and still report the keyboard to the app, so a screen that pads for the
     * keyboard counts it twice. This does the same on the emulator: the activity's content shrinks above the keyboard while
     * Compose still hears about it.
     */
    private fun shrinkForKeyboard(on: Boolean) {
        compose.runOnUiThread {
            val content = compose.activity.findViewById<android.view.ViewGroup>(android.R.id.content)
            if (on) ViewCompat.setOnApplyWindowInsetsListener(content) { view, insets ->
                view.setPadding(0, 0, 0, insets.getInsets(WindowInsetsCompat.Type.ime()).bottom); insets
            } else { ViewCompat.setOnApplyWindowInsetsListener(content, null); content.setPadding(0, 0, 0, 0) }
            ViewCompat.requestApplyInsets(content)
        }
        compose.waitForIdle()
    }

    @Test fun keyboardKeepsTheChatAndTheMessageBoxVisible() {
        val path = InstrumentationRegistry.getArguments().getString("keyboardInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --search", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        var fontScale by mutableFloatStateOf(1f)
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("keyboard", client)
            compose.activity.setContent {
                val base = LocalDensity.current
                CompositionLocalProvider(LocalDensity provides Density(base.density, fontScale)) {
                    RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") }
                }
            }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem home", client) { (client.desktopViews["rem.home"] as? RemHome)?.let { it.messages.size > 20 && it.memories } == true }
            val newest = (client.desktopViews["rem.home"] as RemHome).messages.last().body

            // Without the keyboard, the bottom bar sits right above Android's navigation bar.
            compose.waitForIdle()
            var navigation = 0
            compose.runOnUiThread {
                navigation = ViewCompat.getRootWindowInsets(compose.activity.window.decorView)
                    ?.getInsets(WindowInsetsCompat.Type.navigationBars())?.bottom ?: 0
            }
            val bar = bounds(compose.onNodeWithTag("desktopNavigation"))
            val windowHeight = compose.activity.window.decorView.height
            assertEquals("Bottom bar $bar, navigation bar $navigation px", (windowHeight - navigation).toFloat(), bar.bottom, 2f)

            showKeyboard()
            compose.onNodeWithTag("remComposer").performTextInput("You can see I have my office")
            compose.waitForIdle()
            screenshot("rem-keyboard-one-line.png")
            checkLayout("one line", newest)

            // Longer words grow the box to a few lines, then it scrolls; the chat keeps its room.
            compose.onNodeWithTag("remComposer").performTextInput((2..9).joinToString("") { "\nLine $it of a long message" })
            compose.waitForIdle()
            screenshot("rem-keyboard-many-lines.png")
            checkLayout("many lines", newest)

            // A phone that shrinks its window for the keyboard as well.
            compose.onNodeWithTag("remComposer").performTextClearance()
            compose.onNodeWithTag("remComposer").performTextInput("You can see I have my office")
            shrinkForKeyboard(true)
            showKeyboard()
            screenshot("rem-keyboard-shrunk-window.png")
            checkLayout("shrunk window", newest)
            shrinkForKeyboard(false)

            // Large text, as with the phone's font size turned up, on both kinds of phone.
            fontScale = 1.5f
            showKeyboard()
            screenshot("rem-keyboard-large-text.png")
            checkLayout("large text", newest)
            shrinkForKeyboard(true)
            showKeyboard()
            screenshot("rem-keyboard-large-text-shrunk.png")
            checkLayout("large text, shrunk window", newest)
            assertTrue(failures.joinToString("\n"), failures.isEmpty())
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
