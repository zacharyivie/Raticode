package dev.raticode.mobile.voice

import dev.raticode.mobile.live.PhoneAction
import dev.raticode.mobile.live.PhoneActionList
import dev.raticode.mobile.live.RemHome
import dev.raticode.mobile.live.RemMessage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceLogicTest {
    private fun message(id: String, role: String, body: String, details: String = "") = RemMessage(id, role, body, "2026-11-02T01:00:00+00:00", null, "", details = details)
    private fun home(turn: String, vararg messages: RemMessage) = RemHome(turn, "codex", "", 0, messages.toList(), emptyList())

    @Test fun replyIsRemsFirstMessageAfterTheRequestOnceRemStops() {
        val earlier = message("old", "rem", "An earlier answer")
        val mine = message("req-1", "user", "What is running?")
        val answer = message("reply-1", "rem", "Two tasks are running.")
        assertNull("Rem is still writing", VoiceLogic.reply(home("replying", earlier, mine, answer), "req-1"))
        assertNull("Not in Rem's chat yet", VoiceLogic.reply(home("idle", earlier), "req-1"))
        assertNull("Nothing after the request yet", VoiceLogic.reply(home("idle", earlier, mine), "req-1"))
        assertEquals("reply-1", VoiceLogic.reply(home("idle", earlier, mine, answer), "req-1")?.id)
    }

    @Test fun cardsComeFromEveryRemMessageAnsweringTheRequest() {
        val timer = PhoneActionList(listOf(PhoneAction.Timer(1200, "")), 0)
        val alarm = PhoneActionList(listOf(PhoneAction.Alarm(7, 0, "", emptyList()), PhoneAction.Timer(60, ""), PhoneAction.Timer(90, "")), 1)
        val mine = message("req-1", "user", "Timer and alarm, and start the research")
        // A reply that also started a task: the notice first, then the desktop's message holding the cards.
        val notice = message("n", "rem", "Started the research.")
        val cards = message("files-req-1", "rem", "Ready on your phone.").copy(phoneActions = timer)
        val more = message("m", "rem", "Also these.").copy(phoneActions = alarm)
        val next = message("req-2", "user", "Something else").copy(phoneActions = timer)
        val after = message("a2", "rem", "Other answer").copy(phoneActions = timer)
        val home = home("idle", mine, notice, cards, more, next, after)
        assertEquals("n", VoiceLogic.reply(home, "req-1")?.id)
        val found = VoiceLogic.actions(home, "req-1")
        assertEquals(listOf(PhoneAction.Timer(1200, ""), PhoneAction.Alarm(7, 0, "", emptyList()), PhoneAction.Timer(60, "")), found.actions)
        // One over the limit of three, plus the one the phone already refused.
        assertEquals(2, found.refused)
        assertEquals(PhoneActionList.EMPTY, VoiceLogic.actions(home, "missing"))
        assertEquals(PhoneActionList.EMPTY, VoiceLogic.actions(null, "req-1"))
    }

    @Test fun replyIgnoresEarlierRemMessagesAndOtherRequests() {
        val other = message("req-0", "user", "Earlier question")
        val otherAnswer = message("a0", "rem", "Earlier answer")
        val mine = message("req-1", "user", "Now this")
        val later = message("req-2", "user", "Another phone message")
        val laterAnswer = message("a2", "rem", "Answer to the other one")
        assertNull(VoiceLogic.reply(home("idle", mine, other, otherAnswer).copy(messages = listOf(other, otherAnswer, mine)), "req-1"))
        // A user message after ours doesn't count as Rem's reply; the next Rem message does.
        assertEquals("a2", VoiceLogic.reply(home("idle", other, otherAnswer, mine, later, laterAnswer), "req-1")?.id)
        assertNull("Only the user's own message ID counts", VoiceLogic.reply(home("idle", message("req-1", "rem", "same id"), otherAnswer), "req-1"))
    }

    @Test fun emptyRemMessagesAreSkippedAndDetailsCount() {
        val mine = message("req-1", "user", "Start the research")
        val blank = message("b", "rem", " ")
        val notice = message("n", "rem", "", details = "Started Research voice mode.")
        val found = VoiceLogic.reply(home("idle", mine, blank, notice), "req-1")!!
        assertEquals("n", found.id)
        assertEquals("Started Research voice mode.", VoiceLogic.replyText(found))
    }

    @Test fun acceptedOnlyOnceTheUserMessageIsInRemsChat() {
        assertFalse(VoiceLogic.accepted(null, "req-1"))
        assertFalse(VoiceLogic.accepted(home("idle", message("x", "user", "hi")), "req-1"))
        assertTrue(VoiceLogic.accepted(home("replying", message("req-1", "user", "hi")), "req-1"))
    }

    @Test fun readinessWaitsForTheDesktopBeforeSending() {
        assertEquals(DeskReadiness.LOADING, VoiceLogic.readiness(loaded = false, paired = true, synced = true, desktopControl = true))
        assertEquals(DeskReadiness.NOT_PAIRED, VoiceLogic.readiness(loaded = true, paired = false, synced = false, desktopControl = false))
        assertEquals(DeskReadiness.CONNECTING, VoiceLogic.readiness(loaded = true, paired = true, synced = false, desktopControl = false))
        assertEquals(DeskReadiness.CONTROL_OFF, VoiceLogic.readiness(loaded = true, paired = true, synced = true, desktopControl = false))
        assertEquals(DeskReadiness.READY, VoiceLogic.readiness(loaded = true, paired = true, synced = true, desktopControl = true))
    }

    @Test fun lockedPhonesKeepRepliesHiddenUnlessChosen() {
        assertTrue(VoiceLogic.showReply(locked = false, whileLocked = false))
        assertFalse(VoiceLogic.showReply(locked = true, whileLocked = false))
        assertTrue(VoiceLogic.showReply(locked = true, whileLocked = true))
    }

    @Test fun outgoingTrimsAndBoundsTheMessage() {
        assertNull(VoiceLogic.outgoing("   "))
        assertEquals("Hello Rem", VoiceLogic.outgoing("  Hello Rem \n"))
        assertEquals(VoiceLogic.MAX_CHARS, VoiceLogic.outgoing("a".repeat(5000))!!.length)
    }

    @Test fun talkingBackOffSaysNothingAboutVoicesWhateverIsInstalled() {
        listOf(null, true, false).forEach { ready ->
            assertEquals(null, VoiceLogic.voiceStatus(talkBack = false, ready = ready))
            assertEquals(null, VoiceLogic.replyNote(talkBack = false, ready = ready))
        }
    }

    @Test fun talkingBackOnExplainsAMissingVoice() {
        assertEquals(VoiceLogic.VOICE_MISSING, VoiceLogic.voiceStatus(talkBack = true, ready = false))
        assertEquals(VoiceLogic.NOT_READ, VoiceLogic.replyNote(talkBack = true, ready = false))
        assertEquals(VoiceLogic.VOICE_READY, VoiceLogic.voiceStatus(talkBack = true, ready = true))
        assertEquals(null, VoiceLogic.replyNote(talkBack = true, ready = true))
        assertEquals(VoiceLogic.VOICE_CHECKING, VoiceLogic.voiceStatus(talkBack = true, ready = null))
        assertEquals("No note before the voice has even started", null, VoiceLogic.replyNote(talkBack = true, ready = null))
    }

    @Test fun aMessageNeedsWordsOrAFile() {
        assertTrue(VoiceLogic.sendable("Fix the 4 AM task", emptyList()))
        assertTrue(VoiceLogic.sendable("", listOf("file-1")))
        assertFalse(VoiceLogic.sendable("  ", emptyList()))
    }

    @Test fun filesGoOnlyOnceTheyHaveAllArrived() {
        assertEquals(VoiceLogic.Files.READY, VoiceLogic.files(listOf("available", "available")))
        assertEquals(VoiceLogic.Files.UPLOADING, VoiceLogic.files(listOf("available", "receiving")))
        assertEquals(VoiceLogic.Files.UPLOADING, VoiceLogic.files(listOf("offered")))
        assertEquals(VoiceLogic.Files.STOPPED, VoiceLogic.files(listOf("available", "failed")))
        assertEquals(VoiceLogic.Files.STOPPED, VoiceLogic.files(listOf("cancelled")))
        assertEquals("A file the phone no longer has", VoiceLogic.Files.STOPPED, VoiceLogic.files(listOf("available", null)))
    }

    @Test fun theScreenGoesOnlyWhenChosenForThisMessage() {
        val read = ScreenShare.Ready(ScreenContext("Chrome", "A recipe", at = 1_000))
        assertNull("Read but not chosen: stays on the phone", VoiceLogic.screenToSend(read, chosen = false, allowed = true, now = 2_000))
        assertEquals("Chrome", VoiceLogic.screenToSend(read, chosen = true, allowed = true, now = 2_000)?.app)
        assertNull("Sharing turned off in settings since", VoiceLogic.screenToSend(read, chosen = true, allowed = false, now = 2_000))
        assertNull("Too old", VoiceLogic.screenToSend(read, chosen = true, allowed = true, now = 1_001 + ScreenText.MAX_AGE_MS))
        assertNull(VoiceLogic.screenToSend(ScreenShare.Unavailable(ScreenText.LOCKED), chosen = true, allowed = true, now = 2_000))
        assertNull(VoiceLogic.screenToSend(ScreenShare.None, chosen = true, allowed = true, now = 2_000))
    }

    @Test fun onlyVoiceModeOpenedOverRemChatClosesOnceSent() {
        assertTrue(VoiceLogic.closeOnceQueued(overRemChat = true))
        assertFalse(VoiceLogic.closeOnceQueued(overRemChat = false))
    }

    @Test fun onlyTheUsersOwnWordsRunAsPhoneCommands() {
        assertTrue(VoiceLogic.mayRunOnPhone(fromRem = false, attachments = 0))
        // A suggestion chip Rem wrote ("Text 5551234567 …") is sent to Rem as a message, never called or texted.
        assertFalse(VoiceLogic.mayRunOnPhone(fromRem = true, attachments = 0))
        // Words with files attached are for Rem.
        assertFalse(VoiceLogic.mayRunOnPhone(fromRem = false, attachments = 1))
    }
}
