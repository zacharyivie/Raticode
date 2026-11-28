package dev.raticode.mobile.live

import org.json.JSONObject

/**
 * Social posts from the computer's Posts dashboard: the review list and the user's decisions.
 * Rem, tasks and workflows only propose; a tap here is the user's own decision, pinned to the
 * [PhonePost.contentHash] the phone was shown. The phone can't write or edit a post, connect an
 * account or change settings.
 */
object Posts {
    const val LIST = "posts.list"
    const val DECIDE = "posts.decide"
    val READS = setOf(LIST)
    val ACTIONS = setOf(DECIDE)
    const val UPDATE_DESKTOP = "Update Raticode on your computer to review its social posts here."
    /** Posts is experimental: the phone says so on its page and in More, as the computer does. */
    const val EXPERIMENTAL = "Experimental"
    const val EXPERIMENTAL_NOTE = "Posts is experimental and untested, and it may change. Nothing publishes until you approve it."
    /** Lanes in the dashboard's order, with their headings. */
    val LANES = listOf("review" to "Needs your review", "due" to "Due now", "problems" to "Needs attention",
        "scheduled" to "Scheduled", "done" to "Done")
    private val STATE_LABELS = mapOf(
        "draft" to "Draft", "proposed" to "Needs review", "changes_requested" to "Changes requested", "declined" to "Declined",
        "expired" to "Expired", "approved" to "Approved", "scheduled" to "Scheduled", "due" to "Due now", "publishing" to "Publishing",
        "published" to "Published", "partly_published" to "Partly published", "failed" to "Failed", "missed" to "Missed",
        "cancelled" to "Cancelled", "needs_attention" to "Needs attention", "pending" to "Waiting for approval",
        "held_by_platform" to "Held by the platform", "needs_reconnect" to "Reconnect the account", "outcome_unknown" to "Result unknown",
        "held" to "Held at a cap", "ready_to_post" to "Ready to paste", "posted_by_you" to "Posted by you")

    fun label(state: String) = STATE_LABELS[state] ?: state.replace('_', ' ').replaceFirstChar { it.uppercase() }

    /** Who proposed a post, in words. */
    fun author(createdBy: String) = when {
        createdBy == "user" -> "You"
        createdBy == "rem" -> "Rem"
        createdBy == "cli" -> "A script"
        createdBy.startsWith("workflow:") -> "Workflow " + createdBy.removePrefix("workflow:").substringBefore('@')
        createdBy.startsWith("task:") -> "A Rem task"
        else -> createdBy
    }

    fun parse(json: JSONObject) = PostsView(
        json.optBoolean("paused"),
        json.objects("accounts").map { PostAccount(it.optString("name"), it.optString("platform"), it.optBoolean("needs_reconnect")) },
        json.objects("posts").map(::post),
        json.optInt("more"))

    private fun post(json: JSONObject) = PhonePost(
        json.optString("id"), json.optString("title"), json.optString("state"), json.optString("lane", "done"),
        json.optString("created_by"), json.optString("scheduled_at").takeUnless { json.isNull("scheduled_at") || it.isBlank() },
        json.optString("timezone", "UTC"), json.optString("content_hash"), json.optString("rationale"),
        json.objects("sources").mapNotNull { source -> ChatLinks.web(source.optString("url"))?.let { source.optString("title") to it } },
        json.strings("flags"), json.strings("timing"),
        json.objects("notes").map { it.optString("by") to it.optString("text") },
        json.optBoolean("unseen"),
        json.objects("variants").map { v ->
            PhonePostVariant(v.optString("id"), v.optString("platform"), v.optString("platform_label"), v.optString("account_name"),
                v.optString("state"), v.optString("delivery"), v.optString("text"), v.optBoolean("text_cut"), v.optString("link"),
                v.optInt("media_count"), v.optInt("chars"), v.optInt("limit"),
                if (v.isNull("fold_at") || !v.has("fold_at")) null else v.optInt("fold_at"),
                if (v.isNull("cost_usd") || !v.has("cost_usd")) null else v.optDouble("cost_usd"),
                v.optString("message"), webOrBlank(v.optString("url")), webOrBlank(v.optString("open_url")), v.strings("actions"))
        },
        json.strings("actions"),
        json.objects("copies").map { c ->
            PlatformCopy(c.optString("id"), c.optString("platform"), c.optString("platform_label"), c.optString("account_name"),
                c.optString("message"), webOrBlank(c.optString("url")),
                c.optString("next_try_at").takeUnless { c.isNull("next_try_at") || it.isBlank() }, c.optBoolean("waiting"), c.strings("actions"))
        })

    /** A link the computer sent for a post, kept only when it is a web address; a tap never opens any other scheme. */
    private fun webOrBlank(url: String) = ChatLinks.web(url).orEmpty()

    /** A decision on one post, with the hash of the version the phone showed. */
    fun decision(decision: String, post: PhonePost, variantId: String? = null, note: String = "", url: String = "",
                 unseenConfirmed: Boolean = false): JSONObject = JSONObject().put("decision", decision).put("post_id", post.id)
        .put("content_hash", post.contentHash).apply {
            variantId?.let { put("variant_id", it) }
            if (note.isNotBlank()) put("note", note.trim().take(2000))
            if (url.isNotBlank()) put("url", url.trim().take(2000))
            if (unseenConfirmed) put("unseen_confirmed", true)
        }

    fun pause(paused: Boolean): JSONObject = JSONObject().put("decision", if (paused) "pause" else "resume")

    /** Try deleting a copy again (retry_copy), or say the user deleted it on the platform (copy_deleted). */
    fun copyDecision(decision: String, post: PhonePost, copy: PlatformCopy): JSONObject = JSONObject().put("decision", decision)
        .put("post_id", post.id).put("content_hash", post.contentHash).put("copy_id", copy.id)

    /** The text before and after the platform's "see more" cut, or the whole text and an empty rest. */
    fun fold(variant: PhonePostVariant): Pair<String, String> {
        val at = variant.foldAt ?: return variant.text to ""
        if (at <= 0 || at >= variant.text.length) return variant.text to ""
        return variant.text.substring(0, at) to variant.text.substring(at)
    }

    private fun JSONObject.objects(name: String): List<JSONObject> = optJSONArray(name)?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
    private fun JSONObject.strings(name: String) = optJSONArray(name)?.let { array -> (0 until array.length()).map { array.optString(it) } }.orEmpty()
}

data class PostsView(val paused: Boolean, val accounts: List<PostAccount>, val posts: List<PhonePost>, val more: Int)
data class PostAccount(val name: String, val platform: String, val needsReconnect: Boolean)
data class PhonePost(val id: String, val title: String, val state: String, val lane: String, val createdBy: String,
                     val scheduledAt: String?, val timezone: String, val contentHash: String, val rationale: String,
                     val sources: List<Pair<String, String>>, val flags: List<String>, val timing: List<String>,
                     val notes: List<Pair<String, String>>,
                     /** Part of the post (long text, media or options such as a title) isn't shown on the phone, so approving asks twice. */
                     val unseen: Boolean,
                     val variants: List<PhonePostVariant>, val actions: List<String>,
                     /** Copies a platform still holds from before an edit or cancel that the computer couldn't delete. */
                     val copies: List<PlatformCopy> = emptyList())
data class PhonePostVariant(val id: String, val platform: String, val platformLabel: String, val accountName: String,
                            val state: String, val delivery: String, val text: String, val textCut: Boolean, val link: String,
                            val mediaCount: Int, val chars: Int, val limit: Int,
                            /** Where the platform's feed cuts the text with "see more"; null shows all of it. */
                            val foldAt: Int?, val costUsd: Double?, val message: String, val url: String, val openUrl: String,
                            val actions: List<String>)
/** A copy Facebook or YouTube still holds that the computer tried and failed to delete, with the platform's message. */
data class PlatformCopy(val id: String, val platform: String, val platformLabel: String, val accountName: String,
                        val message: String, val url: String, val nextTryAt: String?,
                        /** The computer stopped trying and waits for Try again or I deleted it. */
                        val waiting: Boolean, val actions: List<String>)
