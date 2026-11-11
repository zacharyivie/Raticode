package dev.raticode.mobile.live

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Synthetic contacts only; nothing here touches a real address book. */
class PhonePeopleTest {
    private var ids = 0
    private val id = { "p${++ids}" }
    private val babyBro = PickedContact("Baby Bro", "+1 (555) 010-0001", "Mobile", "lk-baby")
    private val babyBroWork = PickedContact("Baby Bro", "555-010-0009", "Work", "lk-baby")
    private val bigBro = PickedContact("Big Bro", "555 010 0002", "Mobile", "lk-big")
    private val mom = PickedContact("Mom", "555.010.0003", "Home", "lk-mom")

    @Test fun wordsAreComparedPlainly() {
        assertEquals("brother", PhonePeople.key("My Brother"))
        assertEquals("brother", PhonePeople.key("  my   brother! "))
        assertEquals("baby bro", PhonePeople.key("Baby-Bro"))
        assertEquals("mom", PhonePeople.key("Mom's"))
        assertEquals("", PhonePeople.key("my"))
    }

    @Test fun aRememberedPickAnswersToItsWordsAndTheContactName() {
        val people = PhonePeople.remember(emptyList(), babyBro, "my brother", 1, id)
        assertEquals(listOf("my brother"), people.single().names)
        for (words in listOf("brother", "my brother", "Brother", "baby bro", "Baby Bro")) {
            val match = PhonePeople.resolve(people, words)
            assertTrue(words, match is PersonMatch.One && match.person.number == "+1 (555) 010-0001")
        }
        // A misheard or unknown name finds nobody, so the user picks instead of reaching someone else.
        assertEquals(PersonMatch.None, PhonePeople.resolve(people, "baby bra"))
        assertEquals(PersonMatch.None, PhonePeople.resolve(people, "sister"))
        assertEquals(PersonMatch.None, PhonePeople.resolve(people, "my"))
    }

    @Test fun twoPeopleWithTheSameNameAreOfferedAsAChoice() {
        var people = PhonePeople.remember(emptyList(), babyBro, "brother", 1, id)
        people = PhonePeople.remember(people, bigBro, "brother", 2, id)
        val match = PhonePeople.resolve(people, "my brother")
        assertTrue(match is PersonMatch.Several)
        assertEquals(listOf("Big Bro", "Baby Bro"), (match as PersonMatch.Several).people.map { it.contactName })
        // Each name still finds its own person.
        assertEquals("Big Bro", (PhonePeople.resolve(people, "big bro") as PersonMatch.One).person.contactName)
        // A person picked for this card wins over the saved choice.
        val once = PhonePeople.once(mom, 3)
        assertEquals(PersonMatch.One(once), PhonePeople.resolve(people, "brother", once))
    }

    @Test fun choicesAreLimitedToFive() {
        var people = emptyList<SavedPerson>()
        repeat(7) { n -> people = PhonePeople.remember(people, PickedContact("Cousin $n", "555 010 01${10 + n}", "Mobile", "lk-$n"), "cousin", n.toLong(), id) }
        assertEquals(PhonePeople.MAX_CHOICES, (PhonePeople.resolve(people, "cousin") as PersonMatch.Several).people.size)
    }

    @Test fun theSameContactAndNumberGainsANameInsteadOfASecondEntry() {
        var people = PhonePeople.remember(emptyList(), babyBro, "brother", 1, id)
        people = PhonePeople.remember(people, babyBro.copy(number = "+15550100001"), "Baby Bro", 2, id)
        assertEquals(1, people.size)
        assertEquals(listOf("brother", "Baby Bro"), people.single().names)
        // The same name twice is kept once.
        people = PhonePeople.remember(people, babyBro, "BROTHER", 3, id)
        assertEquals(listOf("brother", "Baby Bro"), people.single().names)
        // Another number for the same person is a separate choice, shown with its label.
        people = PhonePeople.remember(people, babyBroWork, "brother", 4, id)
        assertEquals(listOf("Work", "Mobile"), (PhonePeople.resolve(people, "brother") as PersonMatch.Several).people.map { it.label })
    }

    @Test fun namesCanBeAddedRemovedAndNumbersChanged() {
        var people = PhonePeople.remember(emptyList(), babyBro, null, 1, id)
        val person = people.single()
        assertEquals(emptyList<String>(), person.names)
        people = PhonePeople.addName(people, person.id, "  brother ")
        people = PhonePeople.addName(people, person.id, "my brother")
        people = PhonePeople.addName(people, person.id, "!!")
        assertEquals(listOf("brother"), people.single().names)
        people = PhonePeople.change(people, person.id, babyBroWork, 5)
        assertEquals("555-010-0009", people.single().number)
        assertEquals("Work", people.single().label)
        assertEquals(listOf("brother"), people.single().names)
        people = PhonePeople.removeName(people, person.id, "brother")
        assertEquals(PersonMatch.None, PhonePeople.resolve(people, "brother"))
        assertEquals(emptyList<SavedPerson>(), PhonePeople.forget(people, person.id))
    }

    @Test fun pickedNumbersFollowTheSameRuleAsRemsNumbers() {
        assertEquals("+1 (555) 010-0001", PhonePeople.usableNumber("+1 (555) 010-0001"))
        // Contacts apps often store non-breaking spaces and hyphens.
        assertEquals("555 010-0001", PhonePeople.usableNumber("555 010‑0001"))
        for (bad in listOf("*#06#", "555-0100,1234", "555-0100;2", "1-800-FLOWERS", "555 0100 x12", "12", ""))
            assertNull(bad, PhonePeople.usableNumber(bad))
        assertEquals("0001", PhonePeople.ending("+1 (555) 010-0001"))
        assertEquals("Baby Bro · Mobile", PhonePeople.describe(PhonePeople.once(babyBro, 1)))
    }

    @Test fun savedPeopleRoundTripAndBadEntriesAreDropped() {
        var people = PhonePeople.remember(emptyList(), babyBro, "brother", 1, id)
        people = PhonePeople.remember(people, mom, "mom", 2, id)
        assertEquals(people, PhonePeople.decode(PhonePeople.encode(people)))
        val tampered = PhonePeople.encode(people).replace("555.010.0003", "*#06#")
        assertEquals(listOf("Baby Bro"), PhonePeople.decode(tampered).map { it.contactName })
    }

    @Test fun cardsOpenWithTheSavedNumberAndNothingElseChanges() {
        val text = PhoneAction.TextDraft("", "You alive?", "my brother")
        val person = PhonePeople.remember(emptyList(), babyBro, "brother", 1, id).single()
        val spec = PhoneActions.intent(PhoneActions.withNumber(text, person.number))
        assertEquals("android.intent.action.SENDTO", spec.action)
        assertEquals("smsto:%2B1%20%28555%29%20010-0001", spec.uri)
        assertEquals("You alive?", spec.extras["sms_body"])
        val call = PhoneActions.intent(PhoneActions.withNumber(PhoneAction.Dial("", "Mom"), "555.010.0003"))
        assertEquals("android.intent.action.DIAL", call.action)
        assertEquals("tel:555.010.0003", call.uri)
    }
}
