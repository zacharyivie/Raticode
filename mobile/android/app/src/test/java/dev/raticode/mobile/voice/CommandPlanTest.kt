package dev.raticode.mobile.voice

import dev.raticode.mobile.live.PhoneAction
import dev.raticode.mobile.live.PhonePeople
import dev.raticode.mobile.live.PickedContact
import dev.raticode.mobile.live.SavedPerson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/** Synthetic contacts (555-010-xxxx); nothing reads a real address book, calls or texts. */
class CommandPlanTest {
    private var ids = 0
    private fun people(): List<SavedPerson> {
        var list = PhonePeople.remember(emptyList(), PickedContact("Emily Stone", "555-010-0001", "Mobile", "lk-e"), "emily", 1) { "p${++ids}" }
        list = PhonePeople.remember(list, PickedContact("Baby Bro", "555-010-0002", "Mobile", "lk-b"), "brother", 2) { "p${++ids}" }
        list = PhonePeople.remember(list, PickedContact("Big Bro", "555-010-0003", "Work", "lk-g"), "brother", 3) { "p${++ids}" }
        return list
    }

    @Test fun oneSavedPersonIsCalledStraightAway() {
        assertEquals(Resolved.One(Recipient("Emily Stone · Mobile", "555-010-0001")), CommandPlan.resolve("emily", people()))
        // The contact's own name works too.
        assertEquals("555-010-0002", (CommandPlan.resolve("baby bro", people()) as Resolved.One).recipient.number)
        // A number the user said needs nobody saved.
        assertEquals(Resolved.One(Recipient("5551234567", "5551234567")), CommandPlan.resolve("5551234567", emptyList()))
    }

    @Test fun severalMatchesAskAndNeverGuess() {
        val both = CommandPlan.resolve("brother", people())
        assertTrue(both is Resolved.Several)
        assertEquals(listOf("Big Bro", "Baby Bro"), (both as Resolved.Several).people.map { it.contactName })
        // "at work" picks the one whose number is the work number.
        assertEquals("555-010-0003", (CommandPlan.resolve("brother", people(), label = "work") as Resolved.One).recipient.number)
        // A label no one has leaves the choice to the user.
        assertTrue(CommandPlan.resolve("brother", people(), label = "home") is Resolved.Several)
    }

    @Test fun unknownOrMisheardNamesFindNobody() {
        assertEquals(Resolved.None, CommandPlan.resolve("emma", people()))
        assertEquals(Resolved.None, CommandPlan.resolve("formatting", people()))
        // A contact picked for this command wins over the saved choices.
        val picked = PhonePeople.once(PickedContact("Jordan", "555-010-0009", "Mobile", ""), 4)
        assertEquals("555-010-0009", (CommandPlan.resolve("jordan", people(), picked) as Resolved.One).recipient.number)
    }

    @Test fun fallbacksAreDraftsThatNeverSend() {
        val dial = CommandPlan.fallback(true, "555-010-0001", "")
        assertEquals("android.intent.action.DIAL", dial.action)
        val draft = CommandPlan.fallback(false, "555-010-0001", "On my way.")
        assertEquals("android.intent.action.SENDTO", draft.action)
        assertEquals("On my way.", draft.extras["sms_body"])
        assertEquals(PhoneAction.TextDraft("", "Hi.", "emily"), CommandPlan.card(false, "emily", "Hi."))
    }

    @Test fun answersAndWording() {
        val now = ZonedDateTime.of(2026, 11, 7, 20, 5, 0, 0, ZoneId.of("America/New_York"))
        assertEquals("It's 8:05 PM.", CommandPlan.answer(Question.TIME, now, null, Locale.US))
        assertEquals("It's Saturday, November 7, 2026.", CommandPlan.answer(Question.DATE, now, null, Locale.US))
        assertEquals("Your battery is at 64% and charging.", CommandPlan.answer(Question.BATTERY, now, 64 to true))
        assertEquals("Starting a 20 min timer", CommandPlan.doing(PhoneAction.Timer(1200, "")))
        assertTrue(CommandPlan.appMatches("Spotify", "spotify"))
        assertTrue(CommandPlan.appMatches("YouTube Music", "youtube music"))
        assertTrue(!CommandPlan.appMatches("Spotify", "spot"))
    }
}
