package dev.raticode.mobile

import android.app.Application
import android.graphics.Bitmap
import android.os.SystemClock
import android.util.Log
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.core.content.FileProvider
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.CameraCaptures
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.Random

/**
 * Times Rem chat uploads of a picture of about 600 KB against `live-device-fixture.py --workspace --remote --desktop`:
 * alone, and while a 7 MB file is still uploading. Timings go to logcat under `RaticodeUpload`. It uses only the
 * client's import call, so the same test measures older builds.
 */
class LiveUploadSpeedTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    @Test fun aSixHundredKilobytePictureUploadsPromptlyEvenBehindALargeFile() {
        val path = InstrumentationRegistry.getArguments().getString("uploadInvitationFile")
        assumeTrue("Requires a disposable fixture with --workspace --remote --desktop", path != null)
        val app = instrumentation.targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("upload", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            compose.waitUntil(30_000) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            compose.waitUntil(30_000) { (client.desktopViews["rem.home"] as? RemHome)?.filesThread != null }

            val alone = picture(app, "alone.png", 1)
            assertTrue("about 600 KB: ${alone.length()}", alone.length() in 550_000L..700_000L)
            val aloneMillis = upload(client, app, alone)
            Log.i(TAG, "picture alone: ${alone.length()} bytes in $aloneMillis ms (${chunks(alone)} chunks, ${aloneMillis / chunks(alone)} ms per chunk)")

            val large = File(CameraCaptures.directory(app.cacheDir), "clip.mp4").apply { writeBytes(ByteArray(7_000_000).also { Random(2).nextBytes(it) }) }
            val largeId = import(client, app, large)
            compose.waitUntil(60_000) { client.files.any { it.id == largeId && it.bytes > 0 } }
            val behind = picture(app, "behind.png", 2)
            val startedAt = SystemClock.elapsedRealtime()
            val behindMillis = upload(client, app, behind)
            val largeDone = client.files.single { it.id == largeId }.bytes
            Log.i(TAG, "picture behind a 7 MB upload: ${behind.length()} bytes in $behindMillis ms; the large file had $largeDone of 7000000 bytes then")
            compose.waitUntil(600_000) { client.files.single { it.id == largeId }.state == "available" }
            Log.i(TAG, "7 MB upload finished ${SystemClock.elapsedRealtime() - startedAt} ms after the picture was attached")
        } finally {
            CameraCaptures.cleanup(app.cacheDir, keepMillis = 0)
            compose.runOnUiThread { client.reset(); owners.clear() }
        }
    }

    /** Imports [file] and waits until the desktop holds all of it, returning the time from the import's commit. */
    private fun upload(client: LiveClient, app: Application, file: File): Long {
        val id = import(client, app, file)
        val start = SystemClock.elapsedRealtime()
        compose.waitUntil(600_000) { client.files.single { it.id == id }.state == "available" }
        return SystemClock.elapsedRealtime() - start
    }

    private fun import(client: LiveClient, app: Application, file: File): String {
        val uri = FileProvider.getUriForFile(app, app.packageName + ".opened", file)
        var id: String? = null
        var failure: String? = null
        compose.runOnUiThread { client.importRemFile(uri, file.name, onFailed = { failure = it }) { id = it } }
        compose.waitUntil(60_000) { id != null || failure != null }
        assertNull(failure)
        return id!!
    }

    private fun chunks(file: File) = (file.length() + FileTransfers.CHUNK - 1) / FileTransfers.CHUNK

    companion object {
        const val TAG = "RaticodeUpload"

        /** A real PNG of noise, which doesn't compress, so [side] 440 gives about 580 KB. */
        fun picture(app: Application, name: String, seed: Long, side: Int = 440): File {
            val random = Random(seed)
            val pixels = IntArray(side * side) { 0xFF000000.toInt() or (random.nextInt() and 0xFFFFFF) }
            val bitmap = Bitmap.createBitmap(pixels, side, side, Bitmap.Config.ARGB_8888).apply { setHasAlpha(false) }
            val file = File(CameraCaptures.directory(app.cacheDir).apply { mkdirs() }, name)
            file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
            bitmap.recycle()
            return file
        }
    }
}
