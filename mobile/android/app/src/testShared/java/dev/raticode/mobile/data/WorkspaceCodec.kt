package dev.raticode.mobile.data

import org.json.JSONArray
import org.json.JSONObject

object WorkspaceCodec {
    fun encode(state: Workspace): ByteArray = JSONObject().apply {
        put("version", 1); put("demo", state.demo); put("activeId", state.activeId)
        put("offline", state.simulateOffline); put("failNext", state.failNext)
        put("threads", JSONArray().apply { state.threads.forEach { t -> put(JSONObject().apply {
            put("id", t.id); put("title", t.title); put("project", t.project); put("resources", t.resources)
            put("provider", t.provider); put("model", t.model); put("draft", t.draft)
            put("attachment", fileJson(t.attachment)); put("messages", JSONArray().apply { t.messages.forEach { m -> put(JSONObject().apply {
                put("id", m.id); put("text", m.text); put("fromRem", m.fromRem); put("file", fileJson(m.file))
                put("delivery", m.delivery.name); put("replyTo", m.replyTo); put("createdAt", m.createdAt); put("attempts", m.attempts)
            }) } })
        }) } })
    }.toString().toByteArray(Charsets.UTF_8)

    fun decode(bytes: ByteArray): Workspace {
        val root = JSONObject(bytes.toString(Charsets.UTF_8))
        require(root.getInt("version") == 1) { "Unsupported local storage version" }
        val threads = root.getJSONArray("threads").objects().map { t -> Conversation(
            id = t.getString("id"), title = t.getString("title"), project = t.getString("project"), resources = t.getString("resources"),
            provider = t.getString("provider"), model = t.getString("model"), draft = t.getString("draft"), attachment = readFile(t.optJSONObject("attachment")),
            messages = t.getJSONArray("messages").objects().map { m -> ChatMessage(
                id = m.getString("id"), text = m.getString("text"), fromRem = m.getBoolean("fromRem"), file = readFile(m.optJSONObject("file")),
                delivery = Delivery.valueOf(m.getString("delivery")), replyTo = m.nullString("replyTo"), createdAt = m.getLong("createdAt"), attempts = m.getInt("attempts"),
            ) },
        ) }
        require(threads.size <= LocalSession.MAX_THREADS && threads.map { it.id }.distinct().size == threads.size)
        threads.forEach { t ->
            require(t.messages.size <= LocalSession.MAX_MESSAGES && t.draft.length <= LocalSession.MAX_TEXT)
            require(t.messages.map { it.id }.distinct().size == t.messages.size)
        }
        val state = Workspace(root.getBoolean("demo"), root.nullString("activeId"), threads, root.getBoolean("offline"), root.getBoolean("failNext"))
        require(state.activeId == null || state.active != null)
        return state
    }
    private fun fileJson(f: LocalFile?): Any = f?.let { JSONObject().apply {
        put("id", it.id); put("name", it.name); put("mime", it.mime); put("size", it.size); put("sha256", it.sha256)
    } } ?: JSONObject.NULL
    private fun readFile(f: JSONObject?): LocalFile? = f?.let { LocalFile(it.getString("id"), it.getString("name"), it.getString("mime"), it.getLong("size"), it.getString("sha256")) }
    private fun JSONObject.nullString(key: String): String? = if (isNull(key)) null else getString(key)
    private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
}
