package dev.raticode.mobile.ui

import android.app.Application
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.raticode.mobile.data.*
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class RemViewModel @JvmOverloads constructor(
    application: Application,
    private val imports: DocumentImport = DocumentImport(application.contentResolver),
    private val vault: LocalVault = LocalVault(application),
) : AndroidViewModel(application) {
    private val vaultOwner = vault.acquireOwner()
    private suspend fun <T> vaultOperation(operation: () -> T): T =
        withContext(Dispatchers.IO) { vaultOwner.run(operation) }
    private var persistedState: Workspace? = null
    var importing by mutableStateOf(false); private set
    var state by mutableStateOf(Workspace()); private set
    var busy by mutableStateOf(true); private set
    var savingDraft by mutableStateOf(false); private set
    var storageLocked by mutableStateOf(false); private set
    var notice by mutableStateOf<String?>(null); private set
    val paired get() = state.demo
    val swarms = DemoFixtures.swarms
    init {
        viewModelScope.launch {
            try { state = vaultOperation { vault.load().also { vault.cleanup(it) } }; persistedState = state }
            catch (_: Exception) { storageLocked = true; notice = "Local data could not be unlocked. It has not been overwritten. Reset local data to start again." }
            finally { busy = false }
        }
    }
    private fun change(transform: (Workspace) -> Workspace) {
        if (busy || storageLocked) return
        busy = true
        val before = state
        viewModelScope.launch {
            try {
                val next = transform(before)
                vaultOperation { vault.save(next) }
                persistedState = next
                state = next
                vaultOperation { vault.cleanup(next) }
            } catch (e: IllegalArgumentException) { notice = e.message ?: "Invalid local change." }
            catch (_: Exception) { notice = "Could not save this change. Check device storage and try again." }
            finally { busy = false }
        }
    }
    fun pair() = change { LocalSession.start(it) }
    fun newThread() = change { LocalSession.addThread(it) }
    fun selectThread(id: String) = change { s -> if (s.threads.any { it.id == id }) s.copy(activeId = id) else s }
    fun editThread(id: String, title: String, project: String, resources: String, provider: String, model: String) = change { s -> s.updateThread(id) {
        it.copy(title = title.trim().take(80).ifBlank { "New conversation" }, project = project.trim().take(300), resources = resources.trim().take(1000), provider = provider.trim().take(80), model = model.trim().take(80))
    } }
    private var draftJob: Job? = null
    fun appendTranscript(id: String, transcript: String) {
        viewModelScope.launch {
            while (busy) delay(50)
            val thread = state.threads.find { it.id == id } ?: return@launch
            draft(id, listOf(thread.draft, transcript).filter { it.isNotBlank() }.joinToString(" ").take(LocalSession.MAX_TEXT))
        }
    }
    fun flushDraft() {
        draftJob?.cancel()
        draftJob = viewModelScope.launch {
            while (busy) delay(50)
            saveDraft()
        }
    }
    private fun saveDraft() {
        if (busy || storageLocked || state == persistedState) return
        val snapshot = state
        busy = true
        savingDraft = true
        viewModelScope.launch {
            try { vaultOperation { vault.save(snapshot) }; persistedState = snapshot }
            catch (_: Exception) { notice = "Could not save the draft. Check device storage." }
            finally { savingDraft = false; busy = false }
        }
    }
    fun draft(id: String, text: String) {
        if ((busy && !savingDraft) || storageLocked) return
        state = state.updateThread(id) { it.copy(draft = text.take(LocalSession.MAX_TEXT)) }
        draftJob?.cancel()
        draftJob = viewModelScope.launch {
            delay(350)
            while (busy) delay(50)
            saveDraft()
        }
    }
    fun send(id: String, text: String) = change { LocalSession.send(it.updateThread(id) { t -> t.copy(draft = text) }, id) }
    fun deleteThread(id: String) = change { LocalSession.delete(it, id) }
    fun retry(id: String, messageId: String) = change { LocalSession.retry(it, id, messageId) }
    fun cancel(id: String, messageId: String) = change { LocalSession.cancel(it, id, messageId) }
    fun offline(value: Boolean) = change { LocalSession.flush(it.copy(simulateOffline = value)) }
    fun failNext(value: Boolean) = change { it.copy(failNext = value) }
    fun flush() = change { LocalSession.flush(it) }
    fun removeAttachment(id: String) = change { s -> s.updateThread(id) { it.copy(attachment = null) } }
    fun attach(threadId: String, uri: Uri) {
        if (busy || storageLocked || state.threads.none { it.id == threadId }) return
        busy = true
        importing = true
        val started = imports.start(uri) { result ->
            importing = false
            val prepared = result.getOrNull()
            if (prepared == null) {
                busy = false
                notice = "Could not import this file. It may have timed out, exceeded 10 MiB, or lost access."
            } else viewModelScope.launch {
                try {
                    val before = state
                    val next = vaultOperation {
                        val file = vault.storeImport(prepared, before)
                        before.updateThread(threadId) { it.copy(attachment = file) }.also { vault.save(it) }
                    }
                    persistedState = next
                    state = next
                } catch (_: Exception) { notice = "Could not save this file. Check access, size and free storage." }
                finally {
                    prepared.close()
                    try { vaultOperation { vault.cleanup(state) } }
                    finally { busy = false }
                }
            }
        }
        if (!started) {
            importing = false
            busy = false
            notice = "A previous document provider is still stopping. You can keep using the workspace. Try importing again later."
        }
    }
    fun cancelImport() {
        if (!importing) return
        imports.cancel()
        importing = false
        busy = false
    }
    override fun onCleared() {
        vaultOwner.close()
        imports.cancel()
        super.onCleared()
    }
    fun export(file: LocalFile, uri: Uri) {
        if (busy || storageLocked) return
        busy = true
        viewModelScope.launch {
            try {
                // Cancellation can happen between dispatchers, so keep ownership of the bytes
                // in this scope and wipe even when the provider operation never starts.
                var bytes: ByteArray? = null
                try {
                    withContext(Dispatchers.IO) { bytes = vaultOwner.run { vault.prepareExport(file) } }
                    withContext(Dispatchers.IO) { vault.writeExport(requireNotNull(bytes), uri) }
                } finally { bytes?.fill(0) }
                notice = "File saved to your chosen location. That exported copy is no longer protected by this app's local encryption." }
            catch (_: Exception) { notice = "File export failed. Check the destination and try again." }
            finally { busy = false }
        }
    }
    fun reset() {
        if (busy) return
        busy = true
        viewModelScope.launch {
            try { vaultOperation { vault.reset() }; LocalNotifications.clear(getApplication()); state = Workspace(); persistedState = state; storageLocked = false; notice = null }
            catch (_: Exception) { notice = "Could not clear local data. Try again." }
            finally { busy = false }
        }
    }
    fun inform(text: String) { notice = text }
    fun clearNotice() { notice = null }
}
