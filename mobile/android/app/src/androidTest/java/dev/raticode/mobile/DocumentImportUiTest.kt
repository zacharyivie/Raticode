package dev.raticode.mobile

import android.app.Application
import android.content.ContentResolver
import android.net.Uri
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.DocumentImport
import dev.raticode.mobile.data.LocalVault
import dev.raticode.mobile.ui.DemoApp
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.RemViewModel
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.util.concurrent.TimeUnit

class DocumentImportUiTest {
    @get:Rule val compose = createComposeRule()
    @Test fun cancelButtonReleasesWorkspaceDuringStalledProvider() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val app = instrumentation.targetContext.applicationContext as Application
        val provider = DocumentImportTest.Provider(queryStalls = true)
        val vault = LocalVault(app)
        vault.reset()
        val importer = DocumentImport(ContentResolver.wrap(provider))
        lateinit var model: RemViewModel
        compose.runOnUiThread { model = RemViewModel(app, importer) }
        compose.setContent { RaticodeTheme { DemoApp(model) } }
        compose.waitUntil(10000) { !model.busy }
        compose.onNodeWithTag("pairDemo").performScrollTo().performClick()
        compose.waitUntil(10000) { !model.busy }
        compose.runOnUiThread { model.attach(model.state.activeId!!, Uri.parse("content://test-document/fixture")) }
        try {
            assertTrue(provider.entered.await(5, TimeUnit.SECONDS))
            compose.onNodeWithText("Cancel import").assertIsDisplayed().performClick()
            compose.onNodeWithTag("tabThreads").assertIsEnabled().performClick()
            assertFalse(model.busy)
            assertNull(model.state.active?.attachment)
        } finally {
            compose.runOnUiThread { model.cancelImport() }
            provider.release.countDown()
            assertTrue(provider.finished.await(5, TimeUnit.SECONDS))
            var started = false
            var completed = false
            compose.waitUntil(10000) {
                compose.runOnUiThread {
                    if (!started) started = importer.start(Uri.parse("content://test-document/fixture")) { it.getOrNull()?.close(); completed = true }
                }
                completed
            }
        }
    }
}
