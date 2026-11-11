package dev.raticode.mobile.protocol

import org.json.JSONObject

internal object PayloadValidation {
    fun validate(type: String, p: JSONObject) {
        when (type) {
            "chat.submit" -> { p.fields("text", "attachment_ids"); p.string("text", 4000); ids(p, "attachment_ids", 1) }
            "chat.accepted" -> { p.fields("message_id"); p.uuid("message_id") }
            "chat.message" -> { p.fields("message_id", "text", "attachment_ids", "final"); p.uuid("message_id"); p.string("text", 4000); ids(p, "attachment_ids", 1); boolean(p, "final") }
            "request.cancel" -> { p.fields("reason"); p.string("reason", 300) }
            "request.status" -> {
                p.fields("status", "detail"); p.string("detail", 1000)
                require(p.string("status", 30) in setOf("accepted", "running", "awaiting_approval", "completed", "failed", "cancelled", "cancel_requested"))
            }
            "thread.snapshot" -> {
                p.fields("title", "project_id", "resource_ids", "provider", "model", "revision")
                p.string("title", 80); if (!p.isNull("project_id")) p.uuid("project_id")
                ids(p, "resource_ids", 100); p.string("provider", 80); p.string("model", 80); p.integer("revision")
            }
            "swarm.snapshot" -> {
                p.fields("revision", "swarms"); p.integer("revision")
                val list = p.getJSONArray("swarms"); require(list.length() <= 100)
                repeat(list.length()) { i -> val swarm = list.getJSONObject(i)
                    swarm.fields("swarm_id", "title", "status", "detail"); swarm.uuid("swarm_id"); swarm.string("title", 160); swarm.string("detail", 1000)
                    require(swarm.string("status", 30) in setOf("queued", "running", "awaiting_approval", "completed", "failed", "cancelled"))
                }
            }
            "approval.required" -> {
                p.fields("approval_id", "action_digest", "summary", "expires_at", "remote_allowed")
                p.uuid("approval_id"); digest(p, "action_digest"); p.string("summary", 1000); p.integer("expires_at"); boolean(p, "remote_allowed")
            }
            "approval.resolve" -> {
                p.fields("approval_id", "action_digest", "decision"); p.uuid("approval_id"); digest(p, "action_digest")
                require(p.string("decision", 10) in setOf("approve", "deny"))
            }
            "file.offer" -> {
                p.fields("file_id", "name", "mime", "size", "sha256", "expires_at"); p.uuid("file_id")
                p.string("name", 160); p.string("mime", 100); require(p.integer("size") <= 10485760); digest(p, "sha256"); p.integer("expires_at")
            }
            "file.request" -> { p.fields("file_id"); p.uuid("file_id") }
            "error" -> {
                p.fields("code", "retryable", "detail"); p.string("detail", 1000); boolean(p, "retryable")
                require(p.string("code", 30) in setOf("unsupported_version", "invalid_request", "unauthorized", "revoked", "expired", "rate_limited", "too_large", "not_found", "conflict", "internal"))
            }
            else -> error("Unsupported event")
        }
    }
    private fun boolean(p: JSONObject, key: String) { require(p.get(key) is Boolean) }
    private fun digest(p: JSONObject, key: String) { require(p.string(key, 64).matches(Regex("[0-9a-f]{64}"))) }
    private fun ids(p: JSONObject, key: String, maximum: Int) {
        val ids = p.getJSONArray(key); require(ids.length() <= maximum)
        repeat(ids.length()) { i -> val id = ids.get(i); require(id is String && java.util.UUID.fromString(id).toString() == id) }
    }
}
