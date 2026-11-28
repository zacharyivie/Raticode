package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PostsTest {
    private fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())

    @Test fun postsIsLabelledExperimental() {
        assertEquals("Experimental", Posts.EXPERIMENTAL)
        assertTrue(Posts.EXPERIMENTAL_NOTE.contains("untested"))
        assertTrue(Posts.EXPERIMENTAL_NOTE.contains("until you approve"))
    }

    @Test fun aRealDesktopListParsesWithLanesActionsAndFolds() {
        // posts-list.json is what the desktop's phone_posts.py answered for two posts.
        val view = DesktopViews.parse(Posts.LIST, read("posts-list.json")) as PostsView
        assertFalse(view.paused)
        assertEquals(listOf("discord", "linkedin"), view.accounts.map { it.platform })
        val review = view.posts.first { it.lane == "review" }
        assertEquals("Rem", Posts.author(review.createdBy))
        assertEquals(listOf("approve", "approve_now", "request_changes", "decline"), review.actions)
        assertEquals(64, review.contentHash.length)
        assertTrue(review.flags.any { it.startsWith("New hashtags") })
        assertEquals("Changelog" to "https://raticode.dev/changelog", review.sources.single())
        val linkedin = review.variants.first { it.platform == "linkedin" }
        val (shown, rest) = Posts.fold(linkedin)
        assertEquals(linkedin.foldAt, shown.length)
        assertTrue(rest.isNotEmpty() && shown + rest == linkedin.text)
        val discord = review.variants.first { it.platform == "discord" }
        assertNull(discord.foldAt)
        assertEquals(discord.text to "", Posts.fold(discord))
        val manual = view.posts.first { it.lane == "scheduled" }.variants.single()
        assertEquals(listOf("mark_posted"), manual.actions)
        assertEquals("Ready to paste", Posts.label(manual.state))
    }

    @Test fun decisionsCarryTheHashThePhoneShowedAndUnseenOnlyWhenAsked() {
        val view = DesktopViews.parse(Posts.LIST, read("posts-list.json")) as PostsView
        val post = view.posts.first()
        val approve = Posts.decision("approve", post)
        assertEquals(post.contentHash, approve.getString("content_hash"))
        assertFalse(approve.has("unseen_confirmed") || approve.has("note") || approve.has("variant_id"))
        val confirmed = Posts.decision("approve", post, unseenConfirmed = true)
        assertTrue(confirmed.getBoolean("unseen_confirmed"))
        val changes = Posts.decision("request_changes", post, note = "  Shorter  ")
        assertEquals("Shorter", changes.getString("note"))
        assertEquals("resume", Posts.pause(false).getString("decision"))
        // A decision refreshes the list the screen shows.
        assertEquals(Posts.LIST, DesktopViews.key(Posts.DECIDE, approve))
        assertTrue(Posts.DECIDE in DesktopViews.ACTIONS && Posts.LIST in DesktopViews.READS)
    }

    @Test fun aCopyTheComputerCouldntDeleteParsesWithItsMessageLinkAndChoices() {
        val copy = JSONObject().put("id", "copy-1").put("platform", "facebook").put("platform_label", "Facebook Page")
            .put("account_name", "Raticode Page").put("message", "Permissions error").put("url", "https://www.facebook.com/12345_678")
            .put("next_try_at", JSONObject.NULL).put("waiting", true).put("actions", org.json.JSONArray().put("retry_copy").put("copy_deleted"))
        val json = JSONObject().put("posts", org.json.JSONArray().put(JSONObject().put("id", "p").put("lane", "problems")
            .put("content_hash", "h").put("copies", org.json.JSONArray().put(copy).put(JSONObject(copy.toString()).put("id", "copy-2").put("url", "javascript:alert(1)")))))
        val post = Posts.parse(json).posts.single()
        val (first, second) = post.copies
        assertEquals("Permissions error", first.message)
        assertEquals("https://www.facebook.com/12345_678", first.url)
        assertTrue(first.waiting && first.nextTryAt == null)
        assertEquals(listOf("retry_copy", "copy_deleted"), first.actions)
        assertEquals("", second.url)
        val decision = Posts.copyDecision("copy_deleted", post, first)
        assertEquals("copy-1", decision.getString("copy_id"))
        assertEquals("p", decision.getString("post_id"))
        // A computer from before copies were shared sends none.
        assertTrue(DesktopViews.parse(Posts.LIST, read("posts-list.json")).let { it as PostsView }.posts.all { it.copies.isEmpty() })
    }

    @Test fun theOverviewSaysWhetherTheDesktopSharesPosts() {
        val old = DesktopViews.parse("overview", JSONObject().put("projects", org.json.JSONArray())) as DesktopOverview
        assertFalse(old.posts)
        val current = DesktopViews.parse("overview", JSONObject().put("projects", org.json.JSONArray()).put("posts", true)) as DesktopOverview
        assertTrue(current.posts)
    }

    @Test fun authorsReadAsPeopleAndWorkflows() {
        assertEquals("You", Posts.author("user"))
        assertEquals("Workflow release", Posts.author("workflow:release@3"))
        assertEquals("A script", Posts.author("cli"))
    }

    @Test fun onlyWebAddressesFromTheComputerBecomeTappableLinks() {
        val bad = listOf("file:///data/data/dev.raticode.mobile/files/x", "intent://scan/#Intent;scheme=zxing;end", "javascript:alert(1)",
            "content://dev.raticode.mobile.opened/x", "market://details?id=x", "HTTPS://user@example.com/", "tel:123", "https://")
        val sources = org.json.JSONArray().put(JSONObject().put("title", "Good").put("url", "https://x.com/p/1"))
        bad.forEach { sources.put(JSONObject().put("title", it).put("url", it)) }
        val variants = org.json.JSONArray()
        (bad + "https://x.com/p/2").forEach { variants.put(JSONObject().put("id", it).put("url", it).put("open_url", it)) }
        val json = JSONObject().put("posts", org.json.JSONArray().put(JSONObject().put("id", "p").put("sources", sources).put("variants", variants)))
        val post = Posts.parse(json).posts.single()
        assertEquals(listOf("Good" to "https://x.com/p/1"), post.sources)
        assertEquals(listOf("https://x.com/p/2"), post.variants.map { it.url }.filter { it.isNotEmpty() })
        assertEquals(listOf("https://x.com/p/2"), post.variants.map { it.openUrl }.filter { it.isNotEmpty() })
        bad.forEach { assertNull(it, ChatLinks.web(it)) }
        assertEquals("http://example.com/a?b#c", ChatLinks.web("http://example.com/a?b#c"))
    }
}
