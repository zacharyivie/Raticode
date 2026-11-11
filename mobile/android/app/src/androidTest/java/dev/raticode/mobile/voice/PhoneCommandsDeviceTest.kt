package dev.raticode.mobile.voice

import android.Manifest
import android.app.Application
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.PeopleStore
import dev.raticode.mobile.live.PhonePeople
import dev.raticode.mobile.live.PickedContact
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Voice mode's phone commands against Android itself, on an emulator only: its modem simulates calls and texts, so
 * nothing reaches a real phone. Saved people are synthetic (555-010-xxxx). The words go straight to the controller, as
 * Send does after speech; no desktop is paired, so anything that wrongly went to Rem would show as a send to it.
 */
class PhoneCommandsDeviceTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private lateinit var controller: VoiceController

    @Before fun setUp() {
        instrumentation.uiAutomation.grantRuntimePermission(app.packageName, Manifest.permission.CALL_PHONE)
        instrumentation.uiAutomation.grantRuntimePermission(app.packageName, Manifest.permission.SEND_SMS)
        instrumentation.runOnMainSync {
            controller = VoiceRuntime.controller(app)
            controller.attach()
            PeopleStore.load(app)
            PeopleStore.update(app) { emptyList() }
            PeopleStore.update(app) {
                var people = PhonePeople.remember(it, PickedContact("Test Emily", "555-010-0001", "Mobile", ""), "emily", 1)
                people = PhonePeople.remember(people, PickedContact("Test Baby Bro", "555-010-0002", "Mobile", ""), "brother", 2)
                PhonePeople.remember(people, PickedContact("Test Big Bro", "555-010-0003", "Work", ""), "brother", 3)
            }
        }
    }

    @After fun tearDown() {
        shell("input keyevent KEYCODE_ENDCALL")
        instrumentation.runOnMainSync { controller.cancel(); PeopleStore.update(app) { emptyList() }; controller.detach() }
    }

    private fun shell(command: String): String = instrumentation.uiAutomation.executeShellCommand(command).use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().toString(Charsets.UTF_8)
    }

    private fun outgoing(): Int = Regex("\\(MO - outgoing\\)").findAll(shell("dumpsys telecom")).count()

    private fun state(): VoiceState { var s: VoiceState = VoiceState.Idle; instrumentation.runOnMainSync { s = controller.state }; return s }
    private fun step(): CommandStep? = (state() as? VoiceState.Command)?.step

    private fun waitFor(what: String, timeout: Long = 15_000, condition: () -> Boolean) {
        val end = System.currentTimeMillis() + timeout
        while (System.currentTimeMillis() < end) { if (condition()) return; Thread.sleep(100) }
        throw AssertionError("$what; state=${state()}")
    }

    @Test fun textsGoStraightThroughTidiedAfterTheCountdown() {
        instrumentation.runOnMainSync { controller.send("Text Emily um i'm running late what the f***") }
        val countdown = step() as CommandStep.Countdown
        assertFalse(countdown.call)
        assertEquals("Test Emily · Mobile", countdown.who)
        assertEquals("I'm running late what the fuck.", countdown.body)
        waitFor("text sent") { (step() as? CommandStep.Done)?.text == "Sent to Test Emily · Mobile." }
        // The system recorded it as a sent text, as it does for any app's text.
        val sent = shell("content query --uri content://sms/sent --projection address:body")
        assertTrue(sent, sent.contains("5550100001") || sent.contains("555-010-0001") || sent.contains("running late"))
    }

    @Test fun callsArePlacedAfterTheCountdown() {
        val before = outgoing()
        instrumentation.runOnMainSync { controller.send("A call Emily.") }
        assertTrue(step() is CommandStep.Countdown)
        waitFor("call placed") { (step() as? CommandStep.Done)?.text == "Calling Test Emily · Mobile." }
        // Telecom records a new outgoing call through the phone's own service; it redacts the number in its log.
        waitFor("telecom has the call") { outgoing() > before }
    }

    @Test fun cancellingStopsTheText() {
        instrumentation.runOnMainSync { controller.send("text emily cancel me") }
        assertTrue(step() is CommandStep.Countdown)
        instrumentation.runOnMainSync { controller.cancel() }
        Thread.sleep(CommandPlan.TEXT_MS + 500)
        assertEquals(VoiceState.Idle, state())
        assertFalse(shell("content query --uri content://sms/sent --projection body").contains("Cancel me"))
    }

    @Test fun twoBrothersAskWhichAndUnknownNamesAskForAContact() {
        instrumentation.runOnMainSync { controller.send("call my brother") }
        val choose = step() as CommandStep.Choose
        assertEquals(listOf("Test Big Bro", "Test Baby Bro"), choose.people.map { it.contactName })
        instrumentation.runOnMainSync { controller.cancel(); controller.send("text jordan hello") }
        assertTrue(step() is CommandStep.Pick)
    }

    @Test fun deviceCommandsAndQuestionsWorkWithoutTheDesktop() {
        instrumentation.runOnMainSync { controller.send("what time is it") }
        assertTrue((step() as CommandStep.Done).text.startsWith("It's "))
        instrumentation.runOnMainSync { controller.send("turn the volume up") }
        assertTrue((step() as? CommandStep.Done)?.text?.startsWith("Volume at") == true)
        instrumentation.runOnMainSync { controller.send("how much battery do I have") }
        assertTrue((step() as CommandStep.Done).text.startsWith("Your battery is at"))
    }

    @Test fun remsSuggestionsNeverCallOrText() {
        val before = outgoing()
        // A suggestion chip Rem wrote, tapped under its reply: it goes to Rem as a message (unpaired here), not a call.
        instrumentation.runOnMainSync { controller.sendSuggestion("Call Emily") }
        assertFalse(state().toString(), state() is VoiceState.Command)
        instrumentation.runOnMainSync { controller.sendSuggestion("Text Emily I'm here") }
        assertFalse(state().toString(), state() is VoiceState.Command)
        Thread.sleep(CommandPlan.TEXT_MS + 500)
        assertEquals(before, outgoing())
        assertFalse(shell("content query --uri content://sms/sent --projection body").contains("I'm here"))
    }

    @Test fun bareWordsStillGoToRem() {
        for (said in listOf("Okay, continue", "stop", "next", "mute")) {
            instrumentation.runOnMainSync { controller.cancel(); controller.send(said) }
            assertFalse(said + " " + state(), state() is VoiceState.Command)
        }
    }

    @Test fun ordinaryRequestsStillGoToRem() {
        instrumentation.runOnMainSync { controller.send("Call Emily and tell her the build is done") }
        // Not a phone command: it heads for the desktop (unpaired here), never a call.
        assertFalse(state() is VoiceState.Command)
    }
}
