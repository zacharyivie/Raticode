package dev.raticode.mobile

import android.content.ActivityNotFoundException
import android.content.ContextWrapper
import android.content.Intent
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.ChatLink
import dev.raticode.mobile.live.LinkSource
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.ChatLinkHandler
import dev.raticode.mobile.ui.ChatMarkdown
import dev.raticode.mobile.ui.LinkFileDialog
import dev.raticode.mobile.ui.LocalChatLinks
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.openInBrowser
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Tapping links in messages, the sheet for a tapped file link, and handing web links to the browser. */
class ChatLinksUiTest {
    @get:Rule val compose = createComposeRule()

    @Test fun tappingALinkReportsExactlyWhatWasTapped() {
        val tapped = mutableListOf<Pair<ChatLink, LinkSource?>>()
        val source = LinkSource.of("rem", null, "reply-1")
        compose.setContent {
            RaticodeTheme {
                CompositionLocalProvider(LocalChatLinks provides ChatLinkHandler { link, from -> tapped += link to from }) {
                    androidx.compose.foundation.layout.Column {
                        ChatMarkdown("[the fixture report](</home/me/Fixture report.md>)", source = source)
                        ChatMarkdown("[docs](https://example.com/docs)", source = source)
                    }
                }
            }
        }
        compose.onNodeWithText("the fixture report").performClick()
        compose.onNodeWithText("docs").performClick()
        assertEquals(listOf(ChatLink.File("/home/me/Fixture report.md", "Fixture report.md") to source,
            ChatLink.Web("https://example.com/docs") to source), tapped)
    }

    @Test fun theFileSheetFollowsTheDownload() {
        val taps = mutableListOf<String>()
        var fetch by mutableStateOf(LiveClient.LinkFetch("files.link:/r.md", "r.md"))
        var file by mutableStateOf<LiveClient.LiveFile?>(null)
        compose.setContent {
            RaticodeTheme {
                LinkFileDialog(fetch, file, connected = true, onDownload = { taps += "download" }, onDismiss = { taps += "close" })
            }
        }
        compose.onNodeWithText("Asking your desktop for this file…").assertExists()
        fetch = fetch.copy(fileId = "f1")
        file = LiveClient.LiveFile("f1", "t", "r.md", "offered", "download", 0, 4096)
        compose.onNodeWithText("Download").performClick()
        file = file!!.copy(state = "receiving", bytes = 2048)
        compose.onNodeWithText("Downloading 2 KB of 4 KB").assertExists()
        compose.onNodeWithContentDescription("50% downloaded").assertExists()
        // A downloaded file opens straight away, so the sheet never asks whether to save or open it.
        file = file!!.copy(state = "available", bytes = 4096)
        compose.onNodeWithText("Opening…").assertExists()
        compose.onNodeWithText("Save").assertDoesNotExist()
        compose.onNodeWithText("Open").assertDoesNotExist()
        compose.onNodeWithText("Close").performClick()
        assertEquals(listOf("download", "close"), taps)
        fetch = fetch.copy(fileId = null, error = "That link isn't in this message on the desktop.")
        compose.onNodeWithText("That link isn't in this message on the desktop.").assertExists()
    }

    @Test fun webLinksGoToTheDefaultBrowserFirst() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val started = mutableListOf<Intent>()
        var refuse = 0
        val recorder = object : ContextWrapper(context) {
            override fun startActivity(intent: Intent) { started += intent; if (started.size <= refuse) throw ActivityNotFoundException() }
        }
        assertTrue(openInBrowser(recorder, "https://example.com/docs"))
        val first = started.single()
        assertEquals(Intent.ACTION_VIEW, first.action)
        assertEquals("https://example.com/docs", first.dataString)
        assertTrue(first.hasCategory(Intent.CATEGORY_BROWSABLE))
        assertTrue(first.selector!!.hasCategory(Intent.CATEGORY_APP_BROWSER) && first.selector!!.action == Intent.ACTION_MAIN)
        assertTrue(first.flags and Intent.FLAG_ACTIVITY_NEW_TASK != 0)
        // Without a default browser, any app that views web pages; without one at all, the caller explains.
        started.clear(); refuse = 1
        assertTrue(openInBrowser(recorder, "https://example.com/docs"))
        assertNull(started.last().selector)
        started.clear(); refuse = 2
        assertFalse(openInBrowser(recorder, "https://example.com/docs"))
    }

    /** The real hand-off on this device: the page opens in the phone's default browser app. */
    @Test fun aWebLinkOpensInTheDefaultBrowserApp() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        // Package visibility hides browsers from this app's own lookups, so ask the shell which is the default.
        val browser = instrumentation.uiAutomation.executeShellCommand(
            "cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.APP_BROWSER").let {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).bufferedReader().readLines()
        }.lastOrNull { "/" in it }?.substringBefore('/')
        org.junit.Assume.assumeTrue("No default browser on this device", browser != null)
        assertTrue(openInBrowser(context, "https://example.com/raticode-link-test"))
        try {
            val deadline = System.currentTimeMillis() + 15000
            var shown: CharSequence? = null
            while (System.currentTimeMillis() < deadline && shown != browser) {
                Thread.sleep(250); shown = instrumentation.uiAutomation.rootInActiveWindow?.packageName
            }
            assertEquals(browser, shown)
        } finally { instrumentation.uiAutomation.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME) }
    }
}
