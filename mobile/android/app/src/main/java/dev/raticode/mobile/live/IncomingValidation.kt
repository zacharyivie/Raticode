package dev.raticode.mobile.live

import dev.raticode.mobile.protocol.*
import org.json.JSONObject

internal object IncomingValidation {
    fun validate(event: JSONObject) {
        val type = event.string("type", 40)
        if (!event.isNull("request_id")) event.uuid("request_id")
        if (!event.isNull("thread_id")) event.uuid("thread_id")
        val payload = event.getJSONObject("payload")
        when (type) {
            "sync.snapshot" -> {
                require(event.isNull("thread_id"))
                payload.fields(*(listOf("revision", "through_sequence", "threads") + listOf("connection", "threads_offset", "next_threads_offset").filter { payload.has(it) }).toTypedArray())
                if (payload.has("threads_offset")) payload.integer("threads_offset")
                if (payload.has("next_threads_offset") && !payload.isNull("next_threads_offset")) payload.integer("next_threads_offset")
                if (payload.has("connection")) {
                    val connection = payload.getJSONObject("connection")
                    connection.fields(*(listOf("lan_endpoint", "rem_ready") + listOf("remote_control", "desktop_control", "tailnet_endpoint").filter { connection.has(it) }).toTypedArray())
                    require(connection.get("rem_ready") is Boolean)
                    for (flag in listOf("remote_control", "desktop_control")) if (connection.has(flag)) require(connection.get(flag) is Boolean)
                    if (!connection.isNull("lan_endpoint")) DeviceInvitation.validateDesktopEndpoint(connection.getJSONObject("lan_endpoint"))
                    if (connection.has("tailnet_endpoint") && !connection.isNull("tailnet_endpoint"))
                        DeviceInvitation.validateTailnetEndpoint(connection.getJSONObject("tailnet_endpoint"))
                }
                payload.integer("revision"); payload.integer("through_sequence")
                val threads = payload.getJSONArray("threads"); require(threads.length() <= 100)
                val ids = mutableSetOf<String>()
                repeat(threads.length()) { i ->
                    val t = threads.getJSONObject(i)
                    t.fields(*(listOf("thread_id", "revision", "title", "project_id", "resource_ids", "provider", "model") + listOf("can_create", "effort", "group", "running", "updated_at", "permission_mode", "can_manage", "delete_pending", "unread", "read_at").filter { t.has(it) }).toTypedArray())
                    threadDetails(t)
                    if (t.has("can_create")) require(t.get("can_create") is Boolean)
                    if (t.has("unread")) require(t.get("unread") is Boolean)
                    if (t.has("read_at")) t.string("read_at", 80)
                    require(ids.add(t.uuid("thread_id"))); t.integer("revision"); require(t.string("title", 160).isNotEmpty())
                    if (!t.isNull("project_id")) t.uuid("project_id")
                    val resources = t.getJSONArray("resource_ids"); require(resources.length() <= 32)
                    val unique = mutableSetOf<String>()
                    repeat(resources.length()) { n -> val id = resources.get(n); require(id is String && java.util.UUID.fromString(id).toString() == id && unique.add(id)) }
                    require(t.string("provider", 80).isNotEmpty()); require(t.string("model", 160).isNotEmpty())
                }
            }
            "catalog.snapshot" -> {
                require(event.isNull("thread_id"))
                payload.fields("revision", "projects", "resources", "providers"); payload.integer("revision")
                for (kind in listOf("projects", "resources")) {
                    val list = payload.getJSONArray(kind); require(list.length() <= 100)
                    repeat(list.length()) { i ->
                        val item = list.getJSONObject(i)
                        item.fields(*(if (kind == "projects") arrayOf("id", "name") else arrayOf("id", "name", "project_id")))
                        item.uuid("id"); item.string("name", 160)
                        if (kind == "resources" && !item.isNull("project_id")) item.uuid("project_id")
                    }
                }
                val providers = payload.getJSONArray("providers"); require(providers.length() <= 32)
                repeat(providers.length()) { i ->
                    val provider = providers.getJSONObject(i); provider.fields(*(listOf("id", "models") + listOf("efforts", "permission_modes", "default_permission").filter { provider.has(it) }).toTypedArray());
                    if (provider.has("default_permission")) provider.string("default_permission", 80)
                    if (provider.has("permission_modes")) {
                        val modes = provider.getJSONArray("permission_modes"); require(modes.length() <= 20)
                        repeat(modes.length()) { require(modes.get(it) is String && modes.getString(it).length in 1..80) }
                    }
                    (if (provider.has("efforts")) provider.getJSONObject("efforts") else null)?.let { efforts ->
                        require(efforts.length() <= 100)
                        efforts.keys().forEach { model ->
                            require(model.length in 1..160)
                            val values = efforts.getJSONArray(model); require(values.length() <= 20)
                            repeat(values.length()) { require(values.get(it) is String && values.getString(it).length in 1..80) }
                        }
                    }; provider.string("id", 80)
                    val models = provider.getJSONArray("models"); require(models.length() <= 100)
                    repeat(models.length()) { n -> require(models.get(n) is String && models.getString(n).length in 1..160) }
                }
            }
            "thread.search.snapshot" -> {
                require(event.isNull("thread_id")); event.uuid("request_id")
                payload.fields("query", "offset", "results", "has_more")
                payload.string("query", 300); payload.integer("offset"); require(payload.get("has_more") is Boolean)
                val results = payload.getJSONArray("results"); require(results.length() <= 30)
                repeat(results.length()) { i ->
                    val result = results.getJSONObject(i)
                    result.fields("thread_id", "title", "snippet", "message_id")
                    result.uuid("thread_id"); result.string("title", 160); result.string("snippet", 500)
                    if (!result.isNull("message_id")) result.string("message_id", 180)
                }
            }
            "thread.history.snapshot" -> {
                event.uuid("thread_id"); event.uuid("request_id")
                payload.fields(*(listOf("messages", "before", "has_more") + listOf("total", "resources", "resource_ids").filter { payload.has(it) }).toTypedArray()); payload.integer("before")
                if (payload.has("total")) payload.integer("total")
                resources(payload)
                require(payload.get("has_more") is Boolean)
                val messages = payload.getJSONArray("messages"); require(messages.length() <= 40)
                repeat(messages.length()) { i ->
                    val message = messages.getJSONObject(i); message.fields(*(listOf("id", "role", "text") + listOf("source_id", "offset", "presentation", "attachments").filter { message.has(it) }).toTypedArray())
                    require(message.string("id", 180).isNotEmpty())
                    require(message.string("role", 20) in setOf("user", "assistant", "system")); message.string("text", 4000)
                    if (message.has("source_id")) message.string("source_id", 180)
                    if (message.has("offset")) message.integer("offset")
                    if (message.has("attachments")) {
                        val attached = message.getJSONArray("attachments"); require(attached.length() <= 8)
                        repeat(attached.length()) { n ->
                            val a = attached.getJSONObject(n); a.fields("id", "name", "size", "mime")
                            a.string("id", 180); a.string("name", 160); a.string("mime", 120)
                            require(a.integer("size") <= 10485760)
                        }
                    }
                    if (message.has("presentation")) {
                        val p = message.getJSONObject("presentation")
                        p.fields(*listOf("kind", "groupId", "trace", "running", "startedAt", "completedAt", "durationMs", "truncated").filter { p.has(it) }.toTypedArray())
                        if (p.has("kind")) require(p.string("kind", 30) in setOf("thought", "turn-summary", "error", "final", "system"))
                        if (p.has("groupId")) p.string("groupId", 180)
                        if (p.has("completedAt")) p.string("completedAt", 80)
                        for (field in listOf("running", "truncated")) if (p.has(field)) require(p.get(field) is Boolean)
                        for (field in listOf("startedAt", "durationMs")) if (p.has(field)) p.integer(field)
                        if (p.has("trace")) {
                            val trace = p.getJSONObject("trace")
                            trace.fields(*listOf("id", "kind", "title", "detail", "body", "input", "output", "category", "shell", "command", "status").filter { trace.has(it) }.toTypedArray())
                            trace.keys().forEach { trace.string(it, 4000) }
                        }
                    }
                }
            }
            "fleet.snapshot" -> {
                require(event.isNull("thread_id")); payload.fields("revision", "observed_at", "devices")
                payload.integer("revision"); val observed = payload.integer("observed_at")
                val devices = payload.getJSONArray("devices"); require(devices.length() <= 100)
                repeat(devices.length()) { i -> val d = devices.getJSONObject(i)
                    d.fields("device_id", "name", "role", "status", "last_seen", "observed_at", "fresh_until", "running_jobs")
                    d.uuid("device_id"); require(d.string("name", 80).isNotEmpty())
                    require(d.string("role", 10) in setOf("desktop", "controller"))
                    require(d.string("status", 10) in setOf("reachable", "offline", "unknown"))
                    if (!d.isNull("last_seen")) d.integer("last_seen")
                    val at = d.integer("observed_at"); val fresh = d.integer("fresh_until")
                    require(at <= observed && fresh <= at + 60)
                    val jobs = d.getJSONArray("running_jobs"); require(jobs.length() <= 100)
                    repeat(jobs.length()) { n -> val id = jobs.get(n); require(id is String && java.util.UUID.fromString(id).toString() == id) }
                }
            }
            "file.chunk", "file.status", "file.offer" -> { event.uuid("thread_id") /* exact fields checked by FileTransfers before commit */ }
            "request.status" -> {
                event.uuid("request_id"); event.uuid("thread_id")
                payload.fields("status", "detail"); payload.string("detail", 1000)
                require(payload.string("status", 30) in setOf("accepted", "running", "awaiting_approval", "completed", "failed", "cancelled", "cancel_requested", "outcome_unknown"))
            }
            "error" -> {
                payload.fields("code", "retryable", "detail")
                payload.string("detail", 1000); require(payload.get("retryable") is Boolean)
                require(payload.string("code", 40) in setOf("unsupported", "unsupported_version", "invalid_request", "unauthorized", "revoked", "expired", "rate_limited", "too_large", "not_found", "conflict", "internal"))
            }
            "thread.snapshot" -> {
                event.uuid("thread_id")
                payload.fields(*(listOf("title", "project_id", "resource_ids", "provider", "model", "revision") + listOf("effort", "group", "running", "updated_at", "permission_mode", "can_manage", "delete_pending", "resources").filter { payload.has(it) }).toTypedArray())
                threadDetails(payload)
                resources(payload)
                payload.string("title", 160); if (!payload.isNull("project_id")) payload.uuid("project_id")
                payload.string("provider", 80); payload.string("model", 160); payload.integer("revision")
                val resources = payload.getJSONArray("resource_ids"); require(resources.length() <= 32)
                repeat(resources.length()) { val id = resources.get(it); require(id is String && java.util.UUID.fromString(id).toString() == id) }
            }
            "swarm.snapshot" -> {
                payload.fields(*(listOf("revision", "swarms") + listOf("remote_allowed").filter { payload.has(it) }).toTypedArray())
                payload.integer("revision")
                if (payload.has("remote_allowed")) require(payload.get("remote_allowed") is Boolean)
                val swarms = payload.getJSONArray("swarms"); require(swarms.length() <= 100)
                repeat(swarms.length()) { i ->
                    val swarm = swarms.getJSONObject(i)
                    swarm.fields(*(listOf("swarm_id", "title", "status", "detail") + listOf("project_name", "progress", "agents", "messages").filter { swarm.has(it) }).toTypedArray())
                    swarm.uuid("swarm_id"); swarm.string("title", 160); swarm.string("detail", 1000)
                    require(swarm.string("status", 20) in setOf("queued", "running", "awaiting_approval", "completed", "failed", "cancelled", "paused", "idle"))
                    if (swarm.has("project_name")) swarm.string("project_name", 160)
                    if (swarm.has("progress")) require(swarm.integer("progress") in 0..100)
                    if (swarm.has("agents")) {
                        val agents = swarm.getJSONArray("agents"); require(agents.length() <= 16)
                        repeat(agents.length()) { n -> agents.getJSONObject(n).apply { fields("name", "state"); string("name", 80); string("state", 40) } }
                    }
                    if (swarm.has("messages")) {
                        val messages = swarm.getJSONArray("messages"); require(messages.length() <= 20)
                        repeat(messages.length()) { n -> messages.getJSONObject(n).apply { fields("sender", "body", "created_at"); string("sender", 80); string("body", 1000); string("created_at", 80) } }
                    }
                }
            }
            "approval.snapshot" -> {
                require(event.isNull("thread_id")); event.uuid("request_id")
                payload.fields("remote_allowed", "approvals"); require(payload.get("remote_allowed") is Boolean)
                val approvals = payload.getJSONArray("approvals"); require(approvals.length() <= 50)
                repeat(approvals.length()) { i ->
                    val a = approvals.getJSONObject(i)
                    a.fields("approval_id", "action_digest", "source", "title", "summary", "requested_at", "instruction_required")
                    a.uuid("approval_id"); require(a.string("action_digest", 64).matches(Regex("[0-9a-f]{64}")))
                    require(a.string("source", 10) in setOf("workflow", "swarm"))
                    a.string("title", 160); a.string("summary", 1000); a.string("requested_at", 80)
                    require(a.get("instruction_required") is Boolean)
                }
            }
            "desktop.result" -> {
                // The body is JSON text split into parts; it is parsed only after reassembly.
                require(event.isNull("thread_id")); event.uuid("request_id")
                payload.fields(*(listOf("op", "ok", "part", "parts", "body") + listOf("code", "detail").filter { payload.has(it) }).toTypedArray())
                require(payload.string("op", 64) in DesktopViews.OPERATIONS)
                require(payload.get("ok") is Boolean)
                val parts = payload.integer("parts"); require(parts in 1..DesktopViews.MAX_PARTS && payload.integer("part") < parts)
                payload.string("body", 24000)
                if (payload.has("code")) require(payload.string("code", 20) in setOf("unauthorized", "unavailable", "invalid", "conflict", "not_found"))
                if (payload.has("detail")) payload.string("detail", 1000)
            }
            "chat.accepted", "chat.message", "approval.required" -> {
                if (type != "error") event.uuid("thread_id")
                PayloadValidation.validate(type, payload)
            }
            else -> error("Unsupported incoming record")
        }
    }
    private fun resources(payload: JSONObject) {
        if (payload.has("resource_ids")) {
            val ids = payload.getJSONArray("resource_ids"); require(ids.length() <= 32)
            repeat(ids.length()) { val id = ids.get(it); require(id is String && java.util.UUID.fromString(id).toString() == id) }
        }
        if (!payload.has("resources")) return
        val list = payload.getJSONArray("resources"); require(list.length() <= 32)
        repeat(list.length()) { i ->
            val item = list.getJSONObject(i)
            item.fields("id", "name", "kind", "enabled")
            item.uuid("id"); require(item.string("name", 160).isNotEmpty())
            require(item.string("kind", 10) in setOf("builtin", "skill", "mcp")); require(item.get("enabled") is Boolean)
        }
    }
    private fun threadDetails(payload: JSONObject) {
        for (field in listOf("can_manage", "delete_pending")) if (payload.has(field)) require(payload.get(field) is Boolean)
        if (payload.has("permission_mode")) payload.string("permission_mode", 80)
        if (payload.has("effort") && !payload.isNull("effort")) payload.string("effort", 80)
        if (payload.has("group")) require(payload.string("group", 12) in setOf("pinned", "active", "archived"))
        if (payload.has("running")) require(payload.get("running") is Boolean)
        if (payload.has("updated_at")) payload.string("updated_at", 80)
    }
}
