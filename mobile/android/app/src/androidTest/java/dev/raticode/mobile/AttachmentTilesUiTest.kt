package dev.raticode.mobile

import android.app.Application
import android.graphics.Bitmap
import android.graphics.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.time.ZonedDateTime

/**
 * Attachments in Rem's chat: thumbnails for pictures this phone holds, file-type tiles for the rest, and what
 * tapping each one opens. A stand-in host plays the phone's stored files, so no desktop is needed.
 */
class AttachmentTilesUiTest {
    @get:Rule val compose = createComposeRule()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(600)
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    @Test fun sentAttachmentsPreviewWhatThePhoneHolds() {
        val chart = Bitmap.createBitmap(480, 320, Bitmap.Config.ARGB_8888).also { Canvas(it).drawColor(android.graphics.Color.rgb(79, 70, 229)) }
        val held = listOf("chart.png" to 2048L, "notes.md" to 900L, "report.pdf" to 48000L)
            .mapIndexed { index, (name, size) -> LiveClient.LiveFile("f$index", "rem", name, "available", "upload", size, size) }
        val opened = mutableListOf<String>()
        val host = AttachmentHost(files = { held }, previews = { mapOf("f0" to MediaPreview.Ready(chart)) }, requestPreview = {},
            openImage = { _, done -> done(chart) }, readMedia = { _, done -> done(null) },
            readText = { _, done -> done(TextPreview("# Launch notes\n\n- Ship the phone build", false)) }, open = { _, name -> opened += name })
        val now = ZonedDateTime.now().toOffsetDateTime().toString()
        val home = RemHome("idle", "codex", "", 0, listOf(
            RemMessage("ask", "user", "Here's what I have so far.", now, null, "",
                attachments = held.map { RemFile(it.id, it.name, it.size) } + RemFile("gone", "old.png", 12)),
        ), emptyList())
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        lateinit var client: LiveClient
        InstrumentationRegistry.getInstrumentation().runOnMainSync { client = LiveClient(app) }
        var dark by mutableStateOf(false)
        val removed = mutableListOf<String>()
        compose.setContent {
            RaticodeTheme(darkTheme = dark) {
                CompositionLocalProvider(LocalAttachments provides host) {
                    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Column(Modifier.weight(1f)) { RemConversation(client, home, onTask = {}, onThread = {}, onSuggest = {}) }
                            // The composer's chips: a picture being sent and a file waiting to send.
                            Row(Modifier.padding(8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                AttachmentTile("f0", "chart.png", 2048, onRemove = { removed += "chart.png" })
                                AttachmentTileView("data.csv", 8, null, PreviewMode.TEXT, sending = true, missing = false, onOpen = {},
                                    onRemove = { removed += "data.csv" })
                            }
                        }
                    }
                }
            }
        }

        // The held picture shows itself; other files show their type; a file the phone doesn't have says so.
        // Sent with the message and waiting in the composer.
        assertEquals(2, compose.onAllNodesWithContentDescription("chart.png, PNG · 2 KB").fetchSemanticsNodes().size)
        compose.onNodeWithContentDescription("notes.md, MD · 900 B").assertIsDisplayed()
        compose.onNodeWithContentDescription("report.pdf, PDF · 46 KB").assertIsDisplayed()
        compose.onNodeWithContentDescription("old.png, Not on this phone").assertIsDisplayed()
        screenshot("attachments-light.png")
        dark = true
        screenshot("attachments-dark.png")
        dark = false

        compose.onAllNodesWithContentDescription("chart.png, PNG · 2 KB")[0].performClick()
        compose.onNodeWithTag("mediaViewer").assertIsDisplayed()
        compose.onNodeWithContentDescription("Close").performClick()

        compose.onNodeWithContentDescription("notes.md, MD · 900 B").performClick()
        compose.onNodeWithTag("textPreview").assertIsDisplayed()
        compose.onNodeWithText("- Ship the phone build", substring = true).assertIsDisplayed()
        screenshot("attachments-text.png")
        compose.onNodeWithContentDescription("Close").performClick()

        // A PDF opens straight in the phone's app for it.
        compose.onNodeWithContentDescription("report.pdf, PDF · 46 KB").performClick()
        compose.waitForIdle()
        assertEquals(listOf("report.pdf"), opened)

        compose.onNodeWithContentDescription("old.png, Not on this phone").performClick()
        // This stand-in desktop can't send attachments, so the tile says to update it rather than to wait.
        compose.onNodeWithText("Update Raticode on your computer", substring = true).assertIsDisplayed()
        compose.onNodeWithText("Close").performClick()

        compose.onNodeWithContentDescription("Remove data.csv").performClick()
        compose.onNodeWithContentDescription("Remove chart.png").performClick()
        assertEquals(listOf("data.csv", "chart.png"), removed)
    }
}
