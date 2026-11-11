package dev.raticode.mobile.live

import dev.raticode.mobile.live.RemAlertRules.Fix
import dev.raticode.mobile.live.RemAlertRules.Reply
import dev.raticode.mobile.live.RemAlertRules.Sender
import dev.raticode.mobile.live.RemAlertRules.State
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Rem's chat alerts: replies from the alert, the settings states, the opt-in switches and what the lock screen shows. */
class RemAlertRulesTest {
    private val secret = RemNotice("reply-1", "reply", "The deploy key is in vault/prod and Sam approved it.", "2026-11-02T18:59:15+00:00", "", false)
    private val update = RemNotice("finished-1", "task_update", "Needs you: approve the payroll export.", "2026-11-02T19:00:00+00:00", "Payroll export", true)

    @Test fun repliesNeedAlertsOnAnUnlockedPhoneAndSomeText() {
        assertEquals(Reply.Withdraw, RemAlertRules.reply(enabled = false, sdk = 33, locked = false, typed = "hi"))
        assertEquals(Reply.Refuse("Open Raticode to reply."), RemAlertRules.reply(true, 28, false, "hi"))
        // The lock check comes before anything about the text, and the note never repeats the reply.
        val locked = RemAlertRules.reply(true, 33, locked = true, typed = "send the payroll file")
        assertEquals(Reply.Refuse("Not sent. Unlock your phone, then reply again."), locked)
        assertEquals(Reply.Refuse("Nothing was sent."), RemAlertRules.reply(true, 33, false, "   "))
        assertEquals(Reply.Refuse("Nothing was sent."), RemAlertRules.reply(true, 33, false, null))
        val long = RemAlertRules.reply(true, 33, false, "x".repeat(RemAlertRules.REPLY_LIMIT + 1))
        assertTrue(long is Reply.Refuse && "x" !in (long as Reply.Refuse).note)
        assertEquals(Reply.Send("x".repeat(RemAlertRules.REPLY_LIMIT)), RemAlertRules.reply(true, 33, false, "x".repeat(RemAlertRules.REPLY_LIMIT)))
        assertEquals(Reply.Send("Ship it"), RemAlertRules.reply(true, 29, false, "  Ship it \n"))
    }

    @Test fun settingsNameTheFirstProblemAndWhereToFixIt() {
        fun state(permission: Boolean = true, app: Boolean = true, channel: Boolean = false, desktop: Boolean = true, supported: Boolean? = true) =
            RemAlertRules.state(permission, app, channel, desktop, supported)
        assertEquals(State.NO_PERMISSION, state(permission = false, app = false))
        assertEquals(Fix.ASK_PERMISSION, State.NO_PERMISSION.fix)
        assertEquals(State.APP_BLOCKED, state(app = false, channel = true))
        assertEquals(Fix.APP_SETTINGS, State.APP_BLOCKED.fix)
        assertEquals(State.CHANNEL_BLOCKED, state(channel = true, desktop = false))
        assertEquals(Fix.CHANNEL_SETTINGS, State.CHANNEL_BLOCKED.fix)
        assertEquals(State.DESKTOP_OFF, state(desktop = false, supported = false))
        assertEquals(State.DESKTOP_OLD, state(supported = false))
        // Until the desktop's overview arrives, nothing is wrong yet.
        assertEquals(State.READY, state(supported = null))
        assertEquals(State.READY, state())
        assertEquals(listOf(false, false, false, true, true, true), State.entries.map { it.androidAllows })
        assertTrue(State.entries.filter { it != State.READY }.all { !it.problem.isNullOrBlank() })
    }

    private class Flags : RemAlertRules.Flags {
        val values = mutableMapOf<String, Boolean>()
        override fun get(key: String) = values[key] ?: false
        override fun put(values: Map<String, Boolean>) { this.values.putAll(values) }
        val numbers = mutableMapOf<String, Int>()
        override fun number(key: String, default: Int) = numbers[key] ?: default
        override fun putNumber(key: String, value: Int) { numbers[key] = value }
    }

    private class Surface : RemAlertRules.Surface {
        val calls = mutableListOf<String>()
        override fun clear() { calls += "clear" }
        override fun forgetConversation() { calls += "forget" }
        override fun createChannel() { calls += "channel" }
    }

    @Test fun switchesStartOffAndTurningThemOffRemovesTheAlert() {
        val flags = Flags(); val surface = Surface()
        val switches = RemAlertRules.Switches(flags, surface)
        assertFalse(switches.enabled || switches.showText || switches.takeBaseline())

        switches.setEnabled(true)
        assertTrue(switches.enabled && !switches.showText)
        assertEquals(listOf("channel"), surface.calls)
        // The first read after turning alerts on starts from the desktop's current message, once.
        assertTrue(switches.takeBaseline())
        assertFalse(switches.takeBaseline())

        switches.setShowText(true)
        assertTrue(switches.showText)
        assertEquals(listOf("channel"), surface.calls)
        switches.setShowText(false)
        assertEquals(listOf("channel", "clear"), surface.calls)

        switches.setEnabled(false)
        assertFalse(switches.enabled || switches.takeBaseline())
        assertEquals(listOf("channel", "clear", "clear", "forget"), surface.calls)
        // Turning alerts back on starts from the current message again.
        switches.setEnabled(true)
        assertTrue(switches.takeBaseline())
    }

    @Test fun alertLinesCarryRemsWordsOnlyAfterOptInAndTheLockScreenNever() {
        val generic = RemAlertRules.lines(listOf(secret, update), skipped = 3, showText = false, now = 7)
        assertEquals(listOf(Sender.RATICODE, Sender.REM, Sender.REM), generic.map { it.sender })
        assertEquals(listOf("3 earlier messages from Rem", "New message", "A task needs you"), generic.map { it.text })
        assertEquals(listOf(7L, 1793645955000L, 1793646000000L), generic.map { it.at })
        for (word in listOf("deploy", "vault", "Sam", "payroll", "Payroll")) assertTrue(generic.none { word in it.text })
        assertEquals(RemAlertRules.Line(Sender.YOU, "You replied", 9), RemAlertRules.replyLine("rotate the vault key", false, 9))

        val shown = RemAlertRules.lines(listOf(secret), skipped = 0, showText = true, now = 7)
        assertEquals("The deploy key is in vault/prod and Sam approved it.", shown.single().text)
        assertEquals("rotate the vault key", RemAlertRules.replyLine("rotate the vault key", true, 9).text)

        // The lock screen's version is fixed text, not built from any message.
        assertEquals("Rem", RemAlertRules.LOCK_SCREEN_TITLE)
        assertEquals("New message from Rem", RemAlertRules.LOCK_SCREEN_TEXT)
    }

    @Test fun aReplyWaitsWhileTheChildLockIsOn() {
        val refused = RemAlertRules.reply(enabled = true, sdk = 33, locked = false, typed = "hello", childLocked = true)
        assertTrue(refused is RemAlertRules.Reply.Refuse)
        assertTrue(RemAlertRules.reply(enabled = true, sdk = 33, locked = false, typed = "hello") is RemAlertRules.Reply.Send)
    }

    private fun notice(id: String, kind: String, reason: String = "") = RemNotice(id, kind, "Rem's private words", "2026-11-02T19:00:00+00:00", "Secret task", false, reason)
    private fun read(vararg notices: RemNotice, skipped: Int = 0) = RemNotices(5, notices.toList(), skipped, false)

    @Test fun newAlertSwitchesStartOffAndAreSeparate() {
        val flags = Flags(); val surface = Surface()
        val switches = RemAlertRules.Switches(flags, surface)
        assertEquals(emptyList<String>(), switches.include)
        switches.setFilter(RemAlertRules.PROPOSALS, true)
        assertEquals(listOf("proposals"), switches.include)
        switches.setFilter(RemAlertRules.NEEDS_YOU, true)
        switches.setFilter(RemAlertRules.FINISHED, true)
        switches.setFilter(RemAlertRules.PROPOSALS, false)
        assertEquals(listOf("needs_you", "finished"), switches.include)
        // Choosing what to hear about later doesn't touch the alert on screen or Android's records.
        assertEquals(emptyList<String>(), surface.calls)
        assertFalse(switches.enabled)
        assertFalse(switches.takeBaseline())
    }

    @Test fun quietHoursSpanMidnightAndStaySavedWithinTheDay() {
        val switches = RemAlertRules.Switches(Flags(), Surface())
        assertEquals(RemAlertRules.QuietHours(on = false, start = 1320, end = 420), switches.quietHours)
        val night = RemAlertRules.QuietHours(on = true, start = 22 * 60, end = 7 * 60)
        assertTrue(RemAlertRules.quiet(night, 22 * 60))
        assertTrue(RemAlertRules.quiet(night, 3 * 60))
        assertTrue(RemAlertRules.quiet(night, 7 * 60 - 1))
        assertFalse(RemAlertRules.quiet(night, 7 * 60))
        assertFalse(RemAlertRules.quiet(night, 12 * 60))
        val day = night.copy(start = 9 * 60, end = 17 * 60)
        assertTrue(RemAlertRules.quiet(day, 9 * 60))
        assertFalse(RemAlertRules.quiet(day, 17 * 60))
        assertFalse(RemAlertRules.quiet(day, 8 * 60))
        assertFalse(RemAlertRules.quiet(night.copy(on = false), 23 * 60))
        assertFalse(RemAlertRules.quiet(night.copy(end = night.start), 23 * 60))
        switches.setQuietHours(RemAlertRules.QuietHours(true, -5, 9999))
        assertEquals(RemAlertRules.QuietHours(true, 0, 1439), switches.quietHours)
    }

    @Test fun alertsFollowTheSwitchesEvenForAReadAlreadyOnItsWay() {
        val own = notice("r1", "reply")
        val needs = notice("n1", "task_update", RemAlertRules.NEEDS_YOU)
        val done = notice("o1", "organization", RemAlertRules.FINISHED)
        val round = notice("round-7", "proposals", RemAlertRules.PROPOSALS)
        val all = RemAlertRules.incoming(read(own, needs, done, round), RemAlertRules.FILTERS, emptyList(), chatOpen = false)
        assertEquals(listOf("r1", "n1", "o1", "round-7"), all.notices.map { it.id })
        assertEquals(listOf("round-7"), all.rounds)
        // A phone-started message has no reason and only needs Rem chat notifications to be on.
        assertEquals(listOf("r1"), RemAlertRules.incoming(read(own, needs, done, round), emptyList(), emptyList(), false).notices.map { it.id })
        assertEquals(listOf("r1", "n1"), RemAlertRules.incoming(read(own, needs, done), listOf(RemAlertRules.NEEDS_YOU), emptyList(), false).notices.map { it.id })
    }

    @Test fun aSelfWorkRoundAlertsOnce() {
        val round = notice("round-7", "proposals", RemAlertRules.PROPOSALS)
        val first = RemAlertRules.incoming(read(round, round), listOf(RemAlertRules.PROPOSALS), emptyList(), false)
        assertEquals(1, first.notices.size)
        val again = RemAlertRules.incoming(read(round), listOf(RemAlertRules.PROPOSALS), first.rounds, false)
        assertTrue(again.notices.isEmpty())
        val next = RemAlertRules.incoming(read(notice("round-8", "proposals", RemAlertRules.PROPOSALS)), listOf(RemAlertRules.PROPOSALS), first.rounds, false)
        assertEquals(listOf("round-8"), next.notices.map { it.id })
        assertEquals(listOf("round-7", "round-8"), next.rounds)
        val long = (0 until 30).fold(emptyList<String>()) { seen, i ->
            RemAlertRules.incoming(read(notice("round-$i", "proposals", RemAlertRules.PROPOSALS)), listOf(RemAlertRules.PROPOSALS), seen, false).rounds
        }
        assertEquals(RemAlertRules.ROUNDS_KEPT, long.size)
    }

    @Test fun remsChatOnScreenHoldsBackOnlyWhatTheChatIsShowing() {
        val filters = RemAlertRules.FILTERS
        val shown = RemAlertRules.incoming(read(notice("r1", "reply"), notice("n1", "task_update", RemAlertRules.NEEDS_YOU),
            notice("o1", "organization", RemAlertRules.NEEDS_YOU), notice("round-1", "proposals", RemAlertRules.PROPOSALS), skipped = 4), filters, emptyList(), chatOpen = true)
        assertEquals(listOf("o1", "round-1"), shown.notices.map { it.id })
        assertEquals(0, shown.skipped)
        assertEquals(listOf("round-1"), shown.rounds)
    }

    @Test fun newAlertsStayGenericWithoutRemsWords() {
        val generic = RemAlertRules.lines(listOf(notice("a", "task_update", RemAlertRules.NEEDS_YOU), notice("b", "organization", RemAlertRules.NEEDS_YOU),
            notice("c", "task_update", RemAlertRules.FINISHED), notice("d", "organization", RemAlertRules.FINISHED), notice("e", "proposals", RemAlertRules.PROPOSALS)),
            0, showText = false, now = 1)
        assertEquals(listOf("A task needs you", "A task needs you", "A task finished", "A task finished", "Proposals are waiting"), generic.map { it.text })
        assertTrue(generic.none { "private" in it.text || "Secret" in it.text })
        val shown = RemAlertRules.lines(listOf(notice("a", "task_update", RemAlertRules.NEEDS_YOU)), 0, showText = true, now = 1)
        assertEquals("Rem's private words", shown.single().text)
        // A batch with no text of its own still reads as the batch.
        val blank = notice("e", "proposals", RemAlertRules.PROPOSALS).copy(body = "")
        assertEquals("Proposals are waiting", RemAlertRules.lines(listOf(blank), 0, showText = true, now = 1).single().text)
    }

    @Test fun aSwitchJustTurnedOnKeepsQuietAboutWhatCameBefore() {
        val flags = Flags()
        val switches = RemAlertRules.Switches(flags, Surface())
        switches.setFilter(RemAlertRules.PROPOSALS, true)
        switches.setFilter(RemAlertRules.FINISHED, true)
        assertEquals(listOf("finished", "proposals"), switches.settling)
        val own = notice("r1", "reply")
        val older = notice("o1", "organization", RemAlertRules.FINISHED)
        val round = notice("round-1", "proposals", RemAlertRules.PROPOSALS)
        // Your own replies from the same gap aren't lost, and an old round won't alert when named again.
        val first = RemAlertRules.incoming(read(own, older, round, skipped = 3), switches.include, emptyList(), false, switches.settling)
        assertEquals(listOf("r1"), first.notices.map { it.id })
        assertEquals(0, first.skipped)
        assertEquals(listOf("round-1"), first.rounds)
        switches.settled(listOf(RemAlertRules.FINISHED))
        assertEquals(listOf("proposals"), switches.settling)
        // A switch that settled alerts as usual; turning one off and on starts over.
        assertEquals(listOf("o1"), RemAlertRules.incoming(read(older), switches.include, emptyList(), false, switches.settling).notices.map { it.id })
        assertTrue(RemAlertRules.incoming(read(round), switches.include, first.rounds, false, switches.settling).notices.isEmpty())
        switches.setFilter(RemAlertRules.FINISHED, false)
        assertEquals(listOf("proposals"), switches.settling)
        switches.setFilter(RemAlertRules.FINISHED, true)
        assertEquals(listOf("finished", "proposals"), switches.settling)
    }
}
