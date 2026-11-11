package dev.raticode.mobile

import android.app.Application
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.LocalSession
import dev.raticode.mobile.data.LocalVault
import dev.raticode.mobile.data.Workspace
import dev.raticode.mobile.ui.DemoApp
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.RemViewModel
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException

class VaultOwnershipTest {
    @get:Rule val compose = createComposeRule()
    private val app get() = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
    private fun idle(model: RemViewModel) = compose.waitUntil(10000) { !model.busy }
    private fun create(store: ViewModelStore, vault: LocalVault = LocalVault(app)): RemViewModel {
        lateinit var model: RemViewModel
        compose.runOnUiThread { model = RemViewModel(app, vault = vault); store.put("Rem", model) }
        return model
    }

    @Test fun blockedExportOpenDoesNotHoldVaultOwnership() = blockedExport(false)
    @Test fun blockedExportWriteDoesNotHoldVaultOwnership() = blockedExport(true)
    private fun blockedExport(blockWrite: Boolean) {
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val provider = object : android.content.ContentProvider() {
            override fun onCreate() = true
            override fun query(u: android.net.Uri, p: Array<out String>?, s: String?, a: Array<out String>?, o: String?): android.database.Cursor? = null
            override fun getType(u: android.net.Uri) = "application/octet-stream"
            override fun insert(u: android.net.Uri, v: android.content.ContentValues?): android.net.Uri? = null
            override fun delete(u: android.net.Uri, s: String?, a: Array<out String>?) = 0
            override fun update(u: android.net.Uri, v: android.content.ContentValues?, s: String?, a: Array<out String>?) = 0
            override fun openFile(u: android.net.Uri, mode: String): android.os.ParcelFileDescriptor {
                if (!blockWrite) { entered.countDown(); check(release.await(20, TimeUnit.SECONDS)) }
                val pipe = android.os.ParcelFileDescriptor.createPipe()
                Thread {
                    try {
                        if (blockWrite) { entered.countDown(); check(release.await(20, TimeUnit.SECONDS)) }
                        android.os.ParcelFileDescriptor.AutoCloseInputStream(pipe[0]).use { it.copyTo(java.io.OutputStream.nullOutputStream()) }
                    } catch (_: Exception) { }
                }.start()
                return pipe[1]
            }
        }
        val vault = LocalVault(app, android.content.ContentResolver.wrap(provider))
        vault.reset()
        val state = LocalSession.start(Workspace())
        val file = vault.storeImport(dev.raticode.mobile.data.DocumentImport.Prepared("export.bin", ByteArray(1024 * 1024)), state)
        vault.save(state.updateThread(state.activeId!!) { it.copy(attachment = file) })
        val oldStore = ViewModelStore()
        val replacementStore = ViewModelStore()
        try {
            val old = create(oldStore, vault)
            idle(old)
            compose.runOnUiThread { old.export(file, android.net.Uri.parse("content://export/target")) }
            assertTrue(entered.await(5, TimeUnit.SECONDS))
            compose.runOnUiThread { oldStore.clear() }
            val replacement = create(replacementStore)
            idle(replacement)
            assertFalse(replacement.storageLocked)
            compose.runOnUiThread { replacement.reset() }
            idle(replacement)
            assertEquals(Workspace(), LocalVault(app).load())
            compose.runOnUiThread { replacement.pair() }
            idle(replacement)
            assertEquals(replacement.state, LocalVault(app).load())
        } finally {
            release.countDown()
            compose.runOnUiThread { oldStore.clear(); replacementStore.clear() }
            vault.reset()
        }
    }

    @Test fun destroyedOwnerWriteFinishesBeforeReplacementLoadAndReset() {
        val vault = LocalVault(app)
        vault.reset()
        val oldStore = ViewModelStore()
        val replacementStore = ViewModelStore()
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        try {
            val old = create(oldStore, vault)
            idle(old)
            compose.runOnUiThread { old.pair() }
            idle(old)
            vault.snapshotWriteStarted = { entered.countDown(); check(release.await(10, TimeUnit.SECONDS)) }
            compose.runOnUiThread { old.draft(old.state.activeId!!, "old owner draft"); old.flushDraft() }
            assertTrue(entered.await(5, TimeUnit.SECONDS))
            // Actually clears the ViewModel, unlike ActivityScenario.recreate which retains it.
            compose.runOnUiThread { oldStore.clear() }
            val replacement = create(replacementStore)
            assertTrue(replacement.busy)
            release.countDown()
            idle(replacement)
            assertFalse(replacement.storageLocked)
            assertEquals("old owner draft", replacement.state.active!!.draft)
            compose.runOnUiThread { replacement.reset() }
            idle(replacement)
            assertEquals(Workspace(), LocalVault(app).load())
            compose.runOnUiThread { replacement.pair() }
            idle(replacement)
            assertEquals(replacement.state, LocalVault(app).load())
            assertFalse(LocalVault(app).load().threads.any { it.draft == "old owner draft" })
        } finally {
            release.countDown()
            compose.runOnUiThread { oldStore.clear(); replacementStore.clear() }
            vault.snapshotWriteStarted = null
            vault.reset()
        }
    }

    @Test fun destroyedOwnerQueuedSaveCannotResurrectReplacementReset() {
        val vault = LocalVault(app)
        vault.reset()
        val oldStore = ViewModelStore()
        val replacementStore = ViewModelStore()
        val queued = CountDownLatch(1)
        val release = CountDownLatch(1)
        try {
            val old = create(oldStore, vault)
            idle(old)
            compose.runOnUiThread { old.pair() }
            idle(old)
            // Simulate synchronous IO delayed before acquiring persistence ownership.
            vault.ownedOperationQueued = { queued.countDown(); check(release.await(10, TimeUnit.SECONDS)) }
            compose.runOnUiThread { old.draft(old.state.activeId!!, "must never return"); old.flushDraft() }
            assertTrue(queued.await(5, TimeUnit.SECONDS))
            compose.runOnUiThread { oldStore.clear() }
            val replacement = create(replacementStore)
            idle(replacement)
            compose.runOnUiThread { replacement.reset() }
            idle(replacement)
            assertEquals(Workspace(), LocalVault(app).load())
            release.countDown()
            idle(old)
            assertEquals(Workspace(), LocalVault(app).load())
            compose.runOnUiThread { replacement.pair() }
            idle(replacement)
            assertEquals(replacement.state, LocalVault(app).load())
            assertFalse(LocalVault(app).load().threads.any { it.draft == "must never return" })
        } finally {
            release.countDown()
            compose.runOnUiThread { oldStore.clear(); replacementStore.clear() }
            vault.ownedOperationQueued = null
            vault.reset()
        }
    }

    @Test fun closedAndReplacedOwnersRejectQueuedWritesAfterReset() {
        val vault = LocalVault(app)
        vault.reset()
        val staleState = LocalSession.start(Workspace())
        val old = vault.acquireOwner()
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val queued = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)
        try {
            val holding = executor.submit {
                old.run { entered.countDown(); check(release.await(10, TimeUnit.SECONDS)) }
            }
            assertTrue(entered.await(5, TimeUnit.SECONDS))
            val stale = executor.submit<Boolean> {
                queued.countDown()
                try { old.run { vault.save(staleState); vault.cleanup(staleState) }; false }
                catch (_: CancellationException) { true }
            }
            assertTrue(queued.await(5, TimeUnit.SECONDS))
            old.close()
            val current = vault.acquireOwner()
            release.countDown()
            current.run { vault.reset() }
            holding.get(5, TimeUnit.SECONDS)
            assertTrue(stale.get(5, TimeUnit.SECONDS))
            assertEquals(Workspace(), vault.load())
            // Replacement invalidates even an owner whose close has not yet run.
            val next = vault.acquireOwner()
            assertThrows(CancellationException::class.java) { current.run { vault.save(staleState) } }
            next.run { assertEquals(Workspace(), vault.load()) }
            next.close()
        } finally {
            release.countDown()
            executor.shutdownNow()
            assertTrue(executor.awaitTermination(5, TimeUnit.SECONDS))
            vault.reset()
        }
    }

    @Test fun pendingDebounceAndStopFlushRespectResetSelectionAndDeletion() {
        val vault = LocalVault(app)
        vault.reset()
        val store = ViewModelStore()
        val lifecycle = object : LifecycleOwner {
            val registry = LifecycleRegistry(this)
            override val lifecycle: Lifecycle get() = registry
        }
        try {
            val model = create(store)
            idle(model)
            compose.runOnUiThread { lifecycle.registry.currentState = Lifecycle.State.RESUMED; model.pair() }
            idle(model)
            compose.setContent {
                CompositionLocalProvider(LocalLifecycleOwner provides lifecycle) {
                    RaticodeTheme { DemoApp(model) }
                }
            }
            val first = model.state.activeId!!
            compose.runOnUiThread {
                model.draft(first, "draft before reset")
                model.reset()
                lifecycle.registry.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
            }
            idle(model)
            compose.waitUntil(10000) { vault.load() == Workspace() }
            compose.runOnUiThread { model.pair() }
            idle(model)
            val selected = model.state.activeId!!
            compose.runOnUiThread { model.newThread() }
            idle(model)
            val deleted = model.state.activeId!!
            compose.runOnUiThread {
                model.draft(deleted, "pending deleted draft")
                model.selectThread(selected)
                model.flushDraft()
            }
            idle(model)
            compose.runOnUiThread { model.deleteThread(deleted) }
            idle(model)
            // Let the pending flush/debounce execute, then inspect disk before teardown.
            compose.runOnUiThread { model.flushDraft() }
            compose.waitUntil(10000) {
                val saved = vault.load()
                !model.busy && saved == model.state && saved.activeId == selected && saved.threads.none { it.id == deleted || it.id == first }
            }
            assertEquals(selected, vault.load().activeId)
        } finally {
            compose.runOnUiThread { store.clear(); lifecycle.registry.currentState = Lifecycle.State.DESTROYED }
            vault.reset()
        }
    }
}
