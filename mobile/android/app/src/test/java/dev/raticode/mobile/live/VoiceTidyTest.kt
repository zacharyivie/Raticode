package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceTidyTest {
    @Test fun onlyATidiedAnswerReplacesTheHeardWords() {
        val heard = "um check quad code"
        val tidied = VoiceTidy.parse(heard, JSONObject().put("text", "Check Claude Code.").put("tidied", true))
        assertEquals(TidyResult("Check Claude Code.", true), tidied)
        assertEquals("", VoiceTidy.notice(tidied))
        // The desktop's copy of the words isn't used when it didn't tidy them; the phone keeps its own.
        val refused = VoiceTidy.parse(heard, JSONObject().put("text", "other").put("tidied", false)
            .put("reason", "Claude Haiku 5.5 needs Claude Code 2.1.293 or later. Run claude update, then try again."))
        assertEquals(heard, refused.text)
        assertFalse(refused.tidied)
        assertEquals("Not tidied: Claude Haiku 5.5 needs Claude Code 2.1.293 or later. Run claude update, then try again.", VoiceTidy.notice(refused))
        val empty = VoiceTidy.parse(heard, JSONObject().put("text", " ").put("tidied", true))
        assertEquals(heard, empty.text)
        assertEquals("Tidying didn't work.", empty.reason)
        assertEquals(4000, VoiceTidy.parse(heard, JSONObject().put("text", "x".repeat(5000)).put("tidied", true)).text.length)
    }

    @Test fun onlyWordsAddedAfterTheBoxAreTidied() {
        assertEquals("and rebuild it", VoiceTidy.added("Check the build.", "Check the build. and rebuild it"))
        assertEquals("all of it", VoiceTidy.added("", " all of it "))
        assertEquals("", VoiceTidy.added("Kept.", "Kept."))
        assertNull(VoiceTidy.added("Edited text", "something else entirely"))
    }

    @Test fun desktopsSayWhetherTidyingIsOnAndResultsHaveTheirOwnKey() {
        assertTrue("rem.tidy" in DesktopViews.ACTIONS)
        assertEquals(VoiceTidy.KEY, DesktopViews.key("rem.tidy", JSONObject().put("text", "hi")))
        val on = DesktopViews.parse("overview", JSONObject().put("projects", org.json.JSONArray()).put("voice_tidy", true)) as DesktopOverview
        assertTrue(on.voiceTidy)
        val older = DesktopViews.parse("overview", JSONObject().put("projects", org.json.JSONArray())) as DesktopOverview
        assertFalse("An older desktop never gets rem.tidy", older.voiceTidy)
        val home = DesktopViews.parse("rem.home", JSONObject().put("messages", org.json.JSONArray()).put("tasks", org.json.JSONArray())
            .put("voice_tidy", true)) as RemHome
        assertTrue(home.voiceTidy)
    }
}
