package dev.raticode.mobile

import dev.raticode.mobile.data.*
import org.junit.Assert.*
import org.junit.Test

class WorkspaceTest {
    private fun start() = LocalSession.start(Workspace())
    private fun draft(state: Workspace, text: String) = state.updateThread(state.activeId!!) { it.copy(draft = text) }
    @Test fun persistenceRetainsDraftsContextFilesAndQueueIds() {
        val file = LocalFile(newId(), "notes.txt", "text/plain", 12, "ab".repeat(32))
        val state = draft(start(), "Keep this draft").let { s -> s.updateThread(s.activeId!!) { it.copy(project = "mobile", resources = "docs", attachment = file) } }.copy(simulateOffline = true)
        assertEquals(state, WorkspaceCodec.decode(WorkspaceCodec.encode(state)))
        val sent = LocalSession.send(state, state.activeId!!)
        val restored = WorkspaceCodec.decode(WorkspaceCodec.encode(sent))
        assertEquals(sent, restored)
        assertEquals(Delivery.QUEUED, restored.active!!.messages.last().delivery)
    }
    @Test fun providerChangesDoNotReplaceConversationIdentityOrContext() {
        val original = draft(start(), "draft").let { s -> s.updateThread(s.activeId!!) { it.copy(project = "project-a", resources = "resource-a") } }
        val changed = original.updateThread(original.activeId!!) { it.copy(provider = "different", model = "new-model") }
        assertEquals(original.activeId, changed.activeId)
        assertEquals(original.active!!.messages, changed.active!!.messages)
        assertEquals(original.active!!.project, changed.active!!.project)
        assertEquals(original.active!!.resources, changed.active!!.resources)
        assertEquals(original.active!!.draft, changed.active!!.draft)
    }
    @Test fun offlineQueueFlushIsIdempotentAndPreservesMessageId() {
        var state = draft(start().copy(simulateOffline = true), "status")
        state = LocalSession.send(state, state.activeId!!)
        val queued = state.active!!.messages.last()
        assertEquals(Delivery.QUEUED, queued.delivery)
        assertEquals(2, state.active!!.messages.size)
        state = LocalSession.flush(state.copy(simulateOffline = false))
        assertEquals(3, state.active!!.messages.size)
        assertEquals(queued.id, state.active!!.messages[1].id)
        assertEquals(state, LocalSession.flush(state))
        assertEquals(queued.id, state.active!!.messages.last().replyTo)
    }
    @Test fun retryAfterFailureKeepsIdAndCountsAttempts() {
        var state = draft(start().copy(failNext = true), "hello")
        state = LocalSession.send(state, state.activeId!!)
        val failed = state.active!!.messages.last()
        assertEquals(Delivery.FAILED, failed.delivery)
        state = LocalSession.retry(state, state.activeId!!, failed.id)
        assertEquals(2, state.active!!.messages[1].attempts)
        assertEquals(failed.id, state.active!!.messages[1].id)
        assertEquals(1, state.active!!.messages.count { it.replyTo == failed.id })
        assertEquals(state, LocalSession.retry(state, state.activeId!!, failed.id))
    }
    @Test fun cancellationPreventsLocalProcessing() {
        var state = draft(start().copy(simulateOffline = true), "merge it")
        state = LocalSession.send(state, state.activeId!!)
        val id = state.active!!.messages.last().id
        state = LocalSession.cancel(state, state.activeId!!, id)
        state = LocalSession.flush(state.copy(simulateOffline = false))
        assertEquals(2, state.active!!.messages.size)
        assertEquals(Delivery.CANCELLED, state.active!!.messages.last().delivery)
    }
    @Test fun threadsKeepSeparateHistoryAndDeletingActiveSelectsAnother() {
        val first = draft(start(), "first draft")
        val second = LocalSession.addThread(first)
        assertNotEquals(first.activeId, second.activeId)
        assertEquals("first draft", second.threads.first().draft)
        assertEquals("", second.active!!.draft)
        assertEquals(first, LocalSession.delete(second, second.activeId!!))
    }
    @Test fun unknownDiskVersionFailsClosed() {
        val bytes = String(WorkspaceCodec.encode(start())).replace("\"version\":1", "\"version\":2").toByteArray()
        assertThrows(IllegalArgumentException::class.java) { WorkspaceCodec.decode(bytes) }
    }
    @Test fun queueReservesSpaceForRepliesBeforeAcceptingMoreMessages() {
        var state = start().copy(simulateOffline = true)
        repeat(249) { state = LocalSession.send(draft(state, "queued $it"), state.activeId!!) }
        assertThrows(IllegalArgumentException::class.java) { LocalSession.send(draft(state, "overflow"), state.activeId!!) }
        state = LocalSession.flush(state.copy(simulateOffline = false))
        assertEquals(499, state.active!!.messages.size)
        assertEquals(state, WorkspaceCodec.decode(WorkspaceCodec.encode(state)))
    }
    @Test fun emptyOversizedAndUnpairedSendsAreRejected() {
        val state = start()
        assertThrows(IllegalArgumentException::class.java) { LocalSession.send(state, state.activeId!!) }
        assertThrows(IllegalArgumentException::class.java) { LocalSession.send(draft(state, "x".repeat(4001)), state.activeId!!) }
        assertThrows(IllegalArgumentException::class.java) { LocalSession.send(state.copy(demo = false), state.activeId!!) }
    }
}
