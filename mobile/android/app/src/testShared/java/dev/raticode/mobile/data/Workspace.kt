package dev.raticode.mobile.data

import java.util.UUID

fun newId(): String = UUID.randomUUID().toString()
enum class Delivery { QUEUED, SAMPLE_REPLY, FAILED, CANCELLED }
data class LocalFile(val id: String, val name: String, val mime: String, val size: Long, val sha256: String)
data class ChatMessage(
    val id: String = newId(), val text: String, val fromRem: Boolean = false,
    val file: LocalFile? = null, val delivery: Delivery = Delivery.QUEUED,
    val replyTo: String? = null, val createdAt: Long = System.currentTimeMillis(), val attempts: Int = 0,
)
data class Conversation(
    val id: String = newId(), val title: String = "New conversation", val project: String = "",
    val resources: String = "", val provider: String = "Desktop default", val model: String = "Desktop default",
    val draft: String = "", val attachment: LocalFile? = null, val messages: List<ChatMessage> = emptyList(),
)
data class Workspace(
    val demo: Boolean = false, val activeId: String? = null, val threads: List<Conversation> = emptyList(),
    val simulateOffline: Boolean = false, val failNext: Boolean = false,
) {
    val active: Conversation? get() = threads.find { it.id == activeId }
    fun updateThread(id: String, change: (Conversation) -> Conversation): Workspace =
        copy(threads = threads.map { if (it.id == id) change(it) else it })
    fun files(): List<LocalFile> = threads.flatMap { t -> listOfNotNull(t.attachment) + t.messages.mapNotNull { it.file } }.distinctBy { it.id }
}

/** Deterministic local peer. Never authorizes or executes desktop operations. */
object LocalSession {
    const val MAX_TEXT = 4000
    const val MAX_THREADS = 20
    const val MAX_MESSAGES = 500
    fun start(state: Workspace): Workspace = if (state.demo) state else addThread(state.copy(demo = true))
    fun addThread(state: Workspace): Workspace {
        require(state.demo && state.threads.size < MAX_THREADS) { "The local workspace supports 20 conversations. Delete one to continue." }
        val thread = Conversation(messages = listOf(ChatMessage(text = "This is a local conversation with sample Rem replies. Your desktop is not connected.", fromRem = true, delivery = Delivery.SAMPLE_REPLY)))
        return state.copy(activeId = thread.id, threads = state.threads + thread)
    }
    fun send(state: Workspace, threadId: String): Workspace {
        require(state.demo)
        val thread = requireNotNull(state.threads.find { it.id == threadId })
        val text = thread.draft.trim()
        require(text.length <= MAX_TEXT && (text.isNotEmpty() || thread.attachment != null)) { "Write a message or attach a file." }
        val reservedReplies = thread.messages.count { !it.fromRem && it.delivery in setOf(Delivery.QUEUED, Delivery.FAILED) && thread.messages.none { reply -> reply.replyTo == it.id } }
        require(thread.messages.size + reservedReplies + 2 <= MAX_MESSAGES) { "This conversation is full. Start another conversation." }
        val message = ChatMessage(text = text, file = thread.attachment)
        val queued = state.updateThread(threadId) { it.copy(
            title = if (it.title == "New conversation") text.take(60).ifBlank { thread.attachment?.name ?: "File" } else it.title,
            draft = "", attachment = null, messages = it.messages + message,
        ) }
        return if (state.simulateOffline) queued else flush(queued)
    }
    fun flush(initial: Workspace): Workspace {
        if (initial.simulateOffline) return initial
        var state = initial
        for (thread in initial.threads) for (message in thread.messages.filter { !it.fromRem && it.delivery == Delivery.QUEUED }) {
            val fail = state.failNext
            state = state.updateThread(thread.id) { current ->
                val updated = current.messages.map { if (it.id == message.id) it.copy(delivery = if (fail) Delivery.FAILED else Delivery.SAMPLE_REPLY, attempts = it.attempts + 1) else it }
                val replyExists = updated.any { it.replyTo == message.id }
                current.copy(messages = if (fail || replyExists) updated else updated + ChatMessage(
                    text = reply(message), fromRem = true, delivery = Delivery.SAMPLE_REPLY, replyTo = message.id,
                ))
            }.copy(failNext = false)
        }
        return state
    }
    fun retry(state: Workspace, threadId: String, messageId: String): Workspace {
        val queued = state.updateThread(threadId) { thread -> thread.copy(messages = thread.messages.map {
            if (it.id == messageId && it.delivery == Delivery.FAILED && !it.fromRem) it.copy(delivery = Delivery.QUEUED) else it
        }) }
        return flush(queued)
    }
    fun cancel(state: Workspace, threadId: String, messageId: String) = state.updateThread(threadId) { t ->
        t.copy(messages = t.messages.map { if (it.id == messageId && !it.fromRem && it.delivery in setOf(Delivery.QUEUED, Delivery.FAILED)) it.copy(delivery = Delivery.CANCELLED) else it })
    }
    fun delete(state: Workspace, id: String): Workspace {
        val remaining = state.threads.filterNot { it.id == id }
        return state.copy(threads = remaining, activeId = if (state.activeId == id) remaining.firstOrNull()?.id else state.activeId)
    }
    private fun reply(m: ChatMessage): String {
        val lower = m.text.lowercase()
        return when {
            m.file != null -> "Sample attachment reply: ${m.file.name} is stored encrypted on this phone. No file bytes were uploaded."
            "status" in lower || "update" in lower -> "Sample update: Mobile pairing has 2 agents working on a protocol sketch. Attachment previews is ready for review. These are fixtures; no agents are running through this app."
            "file" in lower || "profile_pic" in lower -> "Sample file request recorded. A real desktop must authorize access and return an encrypted file. No desktop file has been fetched. You can export files you attach locally."
            "swarm" in lower || "feature" in lower || "merge" in lower || "pr" in lower -> "Sample plan: Rem would route this request to a desktop thread, preserve its project and resources, and apply desktop approvals before execution or publication. Nothing has been started or merged."
            else -> "Sample reply: your message is saved in this conversation. A paired desktop will eventually answer here using the thread's own context."
        }
    }
}
