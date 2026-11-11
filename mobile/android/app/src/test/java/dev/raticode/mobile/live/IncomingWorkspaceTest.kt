package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.UUID

class IncomingWorkspaceTest {
    private fun event(type: String, payload: JSONObject, thread: String? = UUID.randomUUID().toString()) =
        JSONObject().put("type", type).put("request_id", UUID.randomUUID().toString())
            .put("thread_id", thread ?: JSONObject.NULL).put("payload", payload)

    @Test fun catalogRejectsCredentialsAndNonStringModels() {
        val provider = JSONObject().put("id", "codex").put("models", JSONArray().put("model-a"))
        val payload = JSONObject().put("revision", 1).put("projects", JSONArray()).put("resources", JSONArray())
            .put("providers", JSONArray().put(provider))
        IncomingValidation.validate(event("catalog.snapshot", payload, null))
        provider.put("api_key", "must-never-be-synced")
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(event("catalog.snapshot", payload, null)) }
        provider.remove("api_key"); provider.put("models", JSONArray().put(123))
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(event("catalog.snapshot", payload, null)) }
    }

    @Test fun historyRequiresAThreadAndBoundedTextRoles() {
        val message = JSONObject().put("id", "desktop-message").put("role", "assistant").put("text", "Existing reply")
        val payload = JSONObject().put("before", 0).put("has_more", false).put("messages", JSONArray().put(message))
        IncomingValidation.validate(event("thread.history.snapshot", payload))
        message.put("role", "worker")
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(event("thread.history.snapshot", payload)) }
        message.put("role", "assistant").put("text", "x".repeat(4001))
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(event("thread.history.snapshot", payload)) }
    }

    @Test fun threadDetailsAndProviderPoliciesAreBounded() {
        val thread = JSONObject().put("title", "Shared").put("project_id", JSONObject.NULL).put("resource_ids", JSONArray())
            .put("provider", "grok").put("model", "grok-model").put("revision", 1).put("group", "pinned")
            .put("running", true).put("effort", "high").put("permission_mode", "cli-managed").put("updated_at", "2026-09-20")
        IncomingValidation.validate(event("thread.snapshot", thread))
        thread.put("running", "true")
        assertThrows(Exception::class.java) { IncomingValidation.validate(event("thread.snapshot", thread)) }
        thread.put("running", true).put("group", "invented")
        assertThrows(Exception::class.java) { IncomingValidation.validate(event("thread.snapshot", thread)) }
        val provider = JSONObject().put("id", "grok").put("models", JSONArray().put("grok-model"))
            .put("permission_modes", JSONArray().put("cli-managed")).put("default_permission", "cli-managed")
            .put("efforts", JSONObject().put("grok-model", JSONArray().put("high")))
        val catalog = JSONObject().put("revision", 1).put("projects", JSONArray()).put("resources", JSONArray()).put("providers", JSONArray().put(provider))
        IncomingValidation.validate(event("catalog.snapshot", catalog, null))
        provider.put("efforts", "untrusted")
        assertThrows(Exception::class.java) { IncomingValidation.validate(event("catalog.snapshot", catalog, null)) }
    }

    @Test fun searchResultsRejectPathsExtraFieldsAndOversizedSnippets() {
        val hit = JSONObject().put("thread_id", UUID.randomUUID().toString()).put("title", "Project")
            .put("snippet", "Matching README message").put("message_id", "saved-message")
        val payload = JSONObject().put("query", "README").put("offset", 0).put("has_more", false).put("results", JSONArray().put(hit))
        IncomingValidation.validate(event("thread.search.snapshot", payload, null))
        hit.put("path", "/desktop/file")
        assertThrows(Exception::class.java) { IncomingValidation.validate(event("thread.search.snapshot", payload, null)) }
        hit.remove("path"); hit.put("snippet", "x".repeat(501))
        assertThrows(Exception::class.java) { IncomingValidation.validate(event("thread.search.snapshot", payload, null)) }
    }

    @Test fun cancellationKeepsTheOriginalRequestAndAllocatesAnIndependentEvent() {
        val (queued, submit) = LiveJournal.prepare(JSONObject(), "chat.submit", UUID.randomUUID().toString(),
            JSONObject().put("text", "Do work").put("attachment_ids", JSONArray()))
        val immutable = queued.getJSONArray("outbox").getString(0)
        val (stopping, cancel) = LiveJournal.prepare(queued, "request.cancel", submit.getString("thread_id"),
            JSONObject().put("reason", "Stop"), submit.getString("request_id"))
        assertEquals(submit.getString("request_id"), cancel.getString("request_id"))
        assertNotEquals(submit.getString("id"), cancel.getString("id"))
        assertEquals(submit.getLong("sequence") + 1, cancel.getLong("sequence"))
        assertEquals(immutable, stopping.getJSONArray("outbox").getString(0))
        assertEquals(1, stopping.getJSONArray("messages").length())
    }
    @Test fun richHistoryAcceptsPresentationButRejectsUnknownAttachmentAndTraceFields() {
        val trace = JSONObject().put("id", "tool").put("kind", "tool").put("command", "pwd")
        val attachment = JSONObject().put("id", "file").put("name", "README.md").put("size", 42).put("mime", "text/markdown")
        val message = JSONObject().put("id", "message").put("role", "assistant").put("text", "bash")
            .put("source_id", "desktop-message").put("offset", 0)
            .put("presentation", JSONObject().put("kind", "thought").put("groupId", "turn").put("trace", trace))
            .put("attachments", JSONArray().put(attachment))
        val payload = JSONObject().put("before", 0).put("has_more", false).put("total", 1).put("messages", JSONArray().put(message))
        IncomingValidation.validate(event("thread.history.snapshot", payload))
        attachment.put("path", "/desktop/file")
        assertThrows(Exception::class.java) { IncomingValidation.validate(event("thread.history.snapshot", payload)) }
        attachment.remove("path"); trace.put("execute", true)
        assertThrows(Exception::class.java) { IncomingValidation.validate(event("thread.history.snapshot", payload)) }
        trace.remove("execute"); trace.put("output", "x".repeat(4001))
        assertThrows(Exception::class.java) { IncomingValidation.validate(event("thread.history.snapshot", payload)) }
    }

}
