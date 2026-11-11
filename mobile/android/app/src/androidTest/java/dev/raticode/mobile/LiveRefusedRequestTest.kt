package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.UUID

/**
 * A queued request the desktop refuses must not block the phone. The phone resends its
 * whole queue on every connection and keeps a request until an answer with its ID
 * arrives, so a desktop that only closed the session left the phone reconnecting
 * forever until Raticode was reinstalled. Uses actual pinned TLS over Wi-Fi with a
 * disposable fixture; no provider runs.
 */
class LiveRefusedRequestTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun staleFileRequestIsAnsweredAndLaterRequestsStillGoThrough() {
        val path = InstrumentationRegistry.getArguments().getString("refusedInvitationFile")
        assumeTrue("Requires a fresh disposable fixture", path != null)
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        var owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("live", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            try { compose.waitUntil(20000) { client.connected && client.threads.isNotEmpty() } }
            catch (failure: Throwable) { throw AssertionError("Fixture connection: ${client.status}", failure) }
            val thread = client.threads.first().first

            // The phone slept through a download overnight. Its queued request for the file
            // outlived the desktop's offer, and is resent first when the phone reconnects.
            compose.runOnUiThread { owners.clear() }
            val store = DeviceStore(app)
            val (queued, _) = LiveJournal.prepare(store.load(), "file.request", thread,
                JSONObject().put("file_id", UUID.randomUUID().toString()))
            store.save(queued)
            store.close()
            owners = ViewModelStore()
            compose.runOnUiThread {
                client = LiveClient(app)
                owners.put("live", client)
                compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
                client.connect()
            }
            try { compose.waitUntil(30000) { client.requestError.orEmpty().contains("no longer available") } }
            catch (failure: Throwable) {
                throw AssertionError("Refused request not answered: ${client.status}; connected=${client.connected}", failure)
            }

            // The request left the queue, so the same pairing keeps working.
            compose.runOnUiThread { client.send(thread, "Question after the refused request") }
            try { compose.waitUntil(30000) { client.messages.any { it.thread == thread && it.state == "Rem" } } }
            catch (failure: Throwable) {
                throw AssertionError("Later request: ${client.status}; connected=${client.connected}; states=${client.messages.map { it.state }}", failure)
            }
            compose.runOnUiThread { client.disconnect(); client.connect() }
            compose.waitUntil(20000) { client.connected && client.status == "Authenticated desktop via Wi-Fi" }
            assertEquals(1, client.messages.count { it.thread == thread && it.state == "Rem" })
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
