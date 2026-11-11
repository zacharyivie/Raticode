package dev.raticode.mobile

import android.app.Application
import android.content.Intent
import android.os.Build
import android.os.ParcelFileDescriptor
import android.speech.SpeechRecognizer
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.LiveRuntime
import dev.raticode.mobile.live.TidyResult
import dev.raticode.mobile.voice.OnDeviceSpeech
import dev.raticode.mobile.voice.SpeechEvents
import dev.raticode.mobile.voice.SpeechSession
import dev.raticode.mobile.voice.VoiceActivity
import dev.raticode.mobile.voice.VoiceRuntime
import dev.raticode.mobile.voice.VoiceState
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import kotlinx.coroutines.CompletableDeferred

/**
 * Dictation on the real voice screen, with a stand-in for Android's recognizer so each pause, final result and
 * interruption happens exactly when the test says. The emulator has no microphone input, so real speech needs a phone.
 */
class VoiceDictationTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val controller get() = VoiceRuntime.controller(app)

    private inner class Session(val events: SpeechEvents) : SpeechSession {
        var finished = false; var closed = false
        override fun start() = true
        override fun finish() { finished = true }
        override fun close() { closed = true }
    }
    private val sessions = mutableListOf<Session>()
    private var now = 1_000_000L
    private fun current() = sessions.last()
    private fun onMain(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun state() = controller.state
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty()
    private fun waitForSessions(count: Int) = compose.waitUntil(5000) { sessions.size >= count && !current().closed }
    private fun screenshot(name: String) = instrumentation.uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-voice-dictation-$name.png")
        .use { ParcelFileDescriptor.AutoCloseInputStream(it).readBytes() }

    @Before fun standIn() {
        assumeTrue("Voice mode needs Android 12", Build.VERSION.SDK_INT >= 31)
        instrumentation.uiAutomation.executeShellCommand("pm grant ${app.packageName} android.permission.RECORD_AUDIO").close()
        DeviceStore(app).reset()
        onMain {
            LiveRuntime.install(LiveClient(app))
            controller.cancel()
            controller.speech = { events -> Session(events).also { sessions += it } }
            controller.clock = { now }
        }
        app.startActivity(VoiceActivity.intent(app).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        compose.waitUntil(10000) { shown("voiceMic") }
        waitForSessions(1)
    }

    @After fun restore() {
        onMain {
            controller.cancel(); controller.speech = { OnDeviceSpeech(app, it) }; controller.clock = { android.os.SystemClock.elapsedRealtime() }
            controller.tidyOn = { LiveRuntime.client(app).voiceTidy }; controller.tidyVoice = { LiveRuntime.client(app).tidyVoice(it) }
        }
        if (shown("voicePanel")) compose.onNodeWithContentDescription("Close voice mode").performClick()
    }

    /** Tidying on, with a stand-in desktop whose answers the test gives when it chooses. */
    private val asked = mutableListOf<String>()
    private val answers = mutableListOf<CompletableDeferred<TidyResult>>()
    private fun tidyingOn() {
        onMain {
            controller.tidyOn = { true }
            controller.tidyVoice = { words -> asked += words; CompletableDeferred<TidyResult>().also { answers += it }.await() }
            controller.listen()
        }
        waitForSessions(2)
    }
    private fun answer(index: Int, result: TidyResult) = onMain { answers[index].complete(result) }
    /**
     * The words Send handed over. No desktop is paired here, so the message stops at Sending or fails to send; either
     * way it carries exactly the text that would have gone to Rem.
     */
    private fun sent(): String? = when (val s = state()) {
        is VoiceState.Sending -> s.text
        is VoiceState.Failed -> s.text
        else -> null
    }
    private fun sendIs(text: String) { compose.waitUntil(5000) { sent() == text }; assertEquals(text, sent()) }

    @Test fun withTidyingOnAPauseShowsTheTidiedWordsWhileListeningGoesOn() {
        tidyingOn()
        onMain { current().events.partial("book a room um for friday") }
        assertTrue("hidden until tidied", !shown("voiceHeard"))
        // Two seconds of quiet sends what was said, and listening carries on.
        compose.waitUntil(5000) { asked.size == 1 }
        assertEquals("book a room um for friday", asked[0])
        assertTrue(state() is VoiceState.Listening)
        // More words while that is out wait for its answer; then, still quiet, everything goes again.
        onMain { current().events.partial("book a room um for friday and invite sam"); now += 3_000 }
        answer(0, TidyResult("Book a room for Friday.", true))
        compose.waitUntil(5000) { shown("voiceHeard") }
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Book a room for Friday.")
        compose.waitUntil(5000) { asked.size == 2 }
        assertEquals("book a room um for friday and invite sam", asked[1])
        answer(1, TidyResult("Book a room for Friday and invite Sam.", true))
        compose.waitUntil(5000) { (state() as? VoiceState.Listening)?.preview == "Book a room for Friday and invite Sam." }
        screenshot("tidied-while-listening")
        // Send: everything said is already tidied, so it goes at once without asking again.
        compose.onNodeWithTag("voiceSend").assertIsEnabled().performClick()
        onMain { current().events.result("book a room um for friday and invite sam") }
        sendIs("Book a room for Friday and invite Sam.")
        assertEquals(2, asked.size)
    }

    @Test fun wordsTheDesktopTidiedIntoACommandGoToRemAndNeverRunOnThePhone() {
        tidyingOn()
        onMain { current().events.partial("well i guess text emily i'm on my way") }
        compose.waitUntil(5000) { asked.size == 1 }
        answer(0, TidyResult("Text Emily I'm on my way.", true))
        compose.waitUntil(5000) { !controller.tidyPending }
        compose.onNodeWithTag("voiceSend").assertIsEnabled().performClick()
        onMain { current().events.result("well i guess text emily i'm on my way") }
        // The user never said a command, so the model's wording can't call or text anyone.
        sendIs("Text Emily I'm on my way.")
        assertTrue(state() !is VoiceState.Command)
    }

    @Test fun sendWaitsWhileATidyRoundIsOutAndWordsThatCouldntBeTidiedWaitInTheBox() {
        tidyingOn()
        onMain { current().events.partial("hello there") }
        compose.waitUntil(5000) { asked.size == 1 }
        // Never mid-tidy: Send waits for the round that is out.
        compose.onNodeWithTag("voiceSend").assertIsNotEnabled()
        assertTrue(state() is VoiceState.Listening)
        answer(0, TidyResult("hello there", false, "Tidying took too long."))
        compose.waitUntil(5000) { !controller.tidyPending }
        // A failed round changes nothing on screen and frees Send; Send tidies again.
        compose.onNodeWithTag("voiceSend").assertIsEnabled().performClick()
        onMain { current().events.result("hello there") }
        compose.waitUntil(5000) { asked.size == 2 }
        answer(1, TidyResult("hello there", false, "Tidying took too long."))
        // Never sent unseen: the words open in the box, as heard, with the reason.
        compose.waitUntil(5000) { shown("voiceInput") }
        compose.onNodeWithTag("voiceInput").assertTextContains("hello there")
        compose.onNodeWithTag("voiceNotice").assertTextContains("Not tidied: Tidying took too long.")
        assertEquals(null, sent())
        compose.onNodeWithTag("voiceSend").performClick()
        sendIs("hello there")
    }

    @Test fun nothingSaidMeansNoSendAndNoTidying() {
        tidyingOn()
        compose.onNodeWithTag("voiceSend").assertIsNotEnabled()
        // Recognizer noise that isn't words, and a pause: still nothing to tidy or send.
        onMain { current().events.partial(" . ") }
        Thread.sleep(2_500)
        assertEquals(0, asked.size)
        compose.onNodeWithTag("voiceSend").assertIsNotEnabled()
        onMain { current().events.partial("ok") }
        compose.onNodeWithTag("voiceSend").assertIsEnabled()
    }

    @Test fun longSpeechIsTidiedOnceFifteenWordsAndFiveSecondsHavePassedWithoutAPause() {
        tidyingOn()
        val words = (1..60).map { "word$it" }
        fun said(n: Int) = words.take(n).joinToString(" ")
        // Fifteen words in under five seconds: a fast talker isn't cut off yet.
        onMain { now += 1_000; current().events.partial(said(15)) }
        Thread.sleep(500)
        assertEquals(0, asked.size)
        // Five seconds in, the next word sends everything so far, without a pause.
        onMain { now += 4_200; current().events.partial(said(22)) }
        compose.waitUntil(1_500) { asked.size == 1 }
        assertEquals(said(22), asked[0])
        assertTrue(state() is VoiceState.Listening)
        // Fifteen more words a second later wait for the five seconds; then the next word sends them all.
        onMain { now += 1_000; current().events.partial(said(40)) }
        answer(0, TidyResult("Word1 to word22", true))
        compose.waitUntil(5000) { (state() as? VoiceState.Listening)?.preview == "Word1 to word22" }
        Thread.sleep(300)
        assertEquals("not five seconds since the last round", 1, asked.size)
        onMain { now += 4_500; current().events.partial(said(41)) }
        compose.waitUntil(1_500) { asked.size == 2 }
        assertEquals(said(41), asked[1])
        // The second round mends the cut-off sentence and replaces what was shown.
        answer(1, TidyResult("Word1 to word41.", true))
        compose.waitUntil(5000) { (state() as? VoiceState.Listening)?.preview == "Word1 to word41." }
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Word1 to word41.")
        compose.onNodeWithTag("voiceSend").assertIsEnabled().performClick()
        onMain { current().events.result(said(41)) }
        sendIs("Word1 to word41.")
        assertEquals("everything was already tidied", 2, asked.size)
    }

    @Test fun aPauseTidiesTheLastFewWordsThatMetNeitherThreshold() {
        tidyingOn()
        onMain { current().events.partial("just a few words") }
        // Two seconds of quiet sends them though there are under fifteen and under five seconds passed.
        compose.waitUntil(5000) { asked.size == 1 }
        assertEquals("just a few words", asked[0])
        compose.onNodeWithTag("voiceSend").assertIsNotEnabled()
        answer(0, TidyResult("Just a few words.", true))
        compose.waitUntil(5000) { (state() as? VoiceState.Listening)?.preview == "Just a few words." }
        compose.onNodeWithTag("voiceSend").assertIsEnabled()
    }

    @Test fun aDesktopThatFailsAtOnceIsNotAskedInALoopWhileQuiet() {
        // Tidying off or offline answers at once; a quiet user must not set off a loop of requests.
        var calls = 0
        onMain {
            controller.tidyOn = { true }
            controller.tidyVoice = { words -> calls++; TidyResult(words, false, "Tidying is off on your desktop.") }
            controller.listen()
        }
        waitForSessions(2)
        // Time passes as it would for real, so the user counts as quiet when the instant failure comes back.
        onMain { current().events.partial("check the build"); now += 3_000 }
        compose.waitUntil(5000) { calls >= 1 }
        Thread.sleep(3_000)
        assertEquals(1, calls)
        // More words try again, once.
        onMain { current().events.partial("check the build now"); now += 3_000 }
        compose.waitUntil(5000) { calls >= 2 }
        Thread.sleep(2_500)
        assertEquals(2, calls)
        compose.onNodeWithTag("voiceSend").assertIsEnabled()
    }

    @Test fun wordsSaidAfterThePauseAreTidiedWhenSendIsTapped() {
        tidyingOn()
        onMain { current().events.partial("first part") }
        compose.waitUntil(5000) { asked.size == 1 }
        answer(0, TidyResult("First part.", true))
        compose.waitUntil(5000) { shown("voiceHeard") }
        // New words straight away, then Send before the next pause.
        onMain { current().events.partial("first part second part") }
        compose.onNodeWithTag("voiceSend").performClick()
        onMain { current().events.result("first part second part") }
        compose.waitUntil(5000) { asked.size == 2 }
        assertEquals("first part second part", asked[1])
        compose.onNodeWithTag("voiceSaid").assertTextEquals("First part.")
        answer(1, TidyResult("First part. Second part.", true))
        sendIs("First part. Second part.")
    }

    @Test fun theKeyboardOpensTheTidiedWordsForEditingAndKeepTalkingKeepsTheEdits() {
        tidyingOn()
        onMain { current().events.partial("remind me to call the bank") }
        compose.waitUntil(5000) { asked.size == 1 }
        // The keyboard while that pause tidy is out: it is used, not asked again, and nothing is sent.
        compose.onNodeWithTag("voiceType").assertContentDescriptionEquals("Edit with the keyboard").performClick()
        compose.waitUntil(5000) { state() is VoiceState.Tidying }
        answer(0, TidyResult("Remind me to call the bank.", true))
        compose.waitUntil(5000) { shown("voiceInput") }
        assertEquals(true, (state() as VoiceState.Review).editing)
        compose.onNodeWithTag("voiceInput").assertTextContains("Remind me to call the bank.").assertIsFocused()
        assertEquals(1, asked.size)
        compose.onNodeWithTag("voiceInput").performTextReplacement("Remind me to call the bank today.")
        // Talking again keeps the edited words and adds only the new ones, which are tidied on their own.
        compose.onNodeWithTag("voiceKeepTalking").performClick()
        waitForSessions(3)
        assertEquals("Remind me to call the bank today.", (state() as VoiceState.Listening).kept)
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Remind me to call the bank today.")
        onMain { current().events.partial("and pay rent") }
        compose.waitUntil(5000) { asked.size == 2 }
        assertEquals("and pay rent", asked[1])
        answer(1, TidyResult("And pay rent.", true))
        compose.waitUntil(5000) { (state() as? VoiceState.Listening)?.preview == "Remind me to call the bank today. And pay rent." }
        compose.onNodeWithTag("voiceSend").performClick()
        onMain { current().events.result("and pay rent") }
        sendIs("Remind me to call the bank today. And pay rent.")
    }

    @Test fun pausesDontEndItAndSendTakesEverythingSaid() {
        compose.onNodeWithTag("voiceSend").assertIsDisplayed()
        assertTrue("no Done step", !shown("voiceDone"))
        compose.onNodeWithTag("voiceStatus").assertContentDescriptionEquals("Listening")
        compose.onNodeWithTag("voiceMic").assert(SemanticsMatcher.expectValue(androidx.compose.ui.semantics.SemanticsProperties.StateDescription, "Listening"))
        onMain { current().events.partial("Book a"); current().events.level(8f) }
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Book a")
        screenshot("listening")
        // Android ends a session at each pause; listening carries on with a new one.
        onMain { current().events.result("Book a meeting") }
        waitForSessions(2)
        onMain { current().events.ended(SpeechRecognizer.ERROR_SPEECH_TIMEOUT) }
        waitForSessions(3)
        assertTrue(state() is VoiceState.Listening)
        onMain { current().events.partial("for Friday") }
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Book a meeting for Friday")
        compose.onNodeWithTag("voiceSend").performClick()
        assertTrue("Send asks the recognizer for its last words", current().finished)
        onMain { current().events.result("for Friday at noon") }
        sendIs("Book a meeting for Friday at noon")
        assertEquals(3, sessions.size)
        assertTrue(sessions.all { it.closed })
    }

    @Test fun aRecognizerThatKeepsListeningAcrossPausesNeedsNoRestart() {
        onMain { current().events.partial("Book a"); current().events.segment("Book a meeting") }
        onMain { current().events.partial("for Friday") }
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Book a meeting for Friday")
        assertEquals("One session across the pause", 1, sessions.size)
        assertTrue(state() is VoiceState.Listening)
        compose.onNodeWithTag("voiceSend").performClick()
        onMain { current().events.segment("for Friday at noon"); current().events.result("") }
        sendIs("Book a meeting for Friday at noon")
        assertEquals(1, sessions.size)
    }

    @Test fun keepTalkingAddsToWhatWasSaid() {
        onMain { current().events.result("Remind me to call the bank") }
        waitForSessions(2)
        compose.onNodeWithTag("voiceType").performClick()
        compose.waitUntil(6000) { shown("voiceInput") }
        compose.onNodeWithTag("voiceInput").assertTextContains("Remind me to call the bank")
        compose.onNodeWithTag("voiceKeepTalking").assertContentDescriptionEquals("Keep talking").performClick()
        waitForSessions(3)
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Remind me to call the bank")
        onMain { current().events.partial("tomorrow morning") }
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Remind me to call the bank tomorrow morning")
        compose.onNodeWithTag("voiceSend").performClick()
        // No final words arrive after Send; the words heard so far are used.
        sendIs("Remind me to call the bank tomorrow morning")
    }

    @Test fun aBrokenMicrophoneKeepsTheWordsWithTheReason() {
        onMain { current().events.result("Summarize the build") }
        waitForSessions(2)
        onMain { current().events.partial("and the tests"); current().events.ended(SpeechRecognizer.ERROR_AUDIO) }
        compose.waitUntil(5000) { shown("voiceNotice") }
        compose.onNodeWithTag("voiceInput").assertTextContains("Summarize the build and the tests")
        compose.onNodeWithTag("voiceNotice").assertTextContains("The microphone stopped working. Your words so far are kept.")
        screenshot("interrupted")
    }

    @Test fun theMicrophonePausesAndResumesWithoutLosingWords() {
        onMain { current().events.partial("Open the release") }
        compose.onNodeWithTag("voiceMic").performClick()
        compose.waitUntil(5000) { (state() as? VoiceState.Listening)?.paused == true }
        // The microphone stops at once, but the session waits for its last words rather than dropping them.
        assertTrue(current().finished)
        onMain { current().events.result("Open the release notes") }
        compose.waitUntil(5000) { current().closed }
        compose.onNodeWithTag("voiceStatus").assertContentDescriptionEquals("Paused")
        compose.onNodeWithTag("voiceMic").assert(SemanticsMatcher.expectValue(androidx.compose.ui.semantics.SemanticsProperties.StateDescription, "Paused"))
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Open the release notes")
        assertEquals("no new session while paused", 1, sessions.size)
        screenshot("paused")
        compose.onNodeWithContentDescription("Keep talking").performClick()
        waitForSessions(2)
        onMain { current().events.partial("for version ten") }
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Open the release notes for version ten")
    }

    @Test fun aPauseWhoseLastWordsNeverComeKeepsWhatWasHeard() {
        onMain { current().events.partial("Check the deploy logs") }
        compose.onNodeWithTag("voiceMic").performClick()
        // No final words within the wait: the words heard so far stay and the session closes.
        compose.waitUntil(6000) { current().closed }
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Check the deploy logs")
        assertEquals(true, (state() as? VoiceState.Listening)?.paused)
    }

    @Test fun theKeyboardWaitsForTheLastWordsBeforeOpeningThemToEdit() {
        onMain { current().events.partial("Remind me to call") }
        compose.onNodeWithTag("voiceType").performClick()
        assertTrue("the keyboard asks for the last words", current().finished)
        assertTrue("still collecting them", state() is VoiceState.Listening)
        onMain { current().events.result("Remind me to call the bank") }
        compose.waitUntil(5000) { shown("voiceInput") }
        compose.onNodeWithTag("voiceInput").assertTextContains("Remind me to call the bank").assertIsFocused()
        assertEquals(true, (state() as VoiceState.Review).editing)
    }

    @Test fun aRepeatedPartialDuringAPauseDoesNotPostponeTheRound() {
        tidyingOn()
        onMain { current().events.partial("book a room") }
        // The recognizer repeats the same words while the user is quiet; that isn't new speech.
        repeat(4) { Thread.sleep(500); onMain { current().events.partial("book a room") } }
        compose.waitUntil(1_500) { asked.size == 1 }
        assertEquals("book a room", asked[0])
    }

    @Test fun withTidyingOnAPauseTidiesTheLastWordsToo() {
        tidyingOn()
        onMain { current().events.partial("book a room") }
        compose.onNodeWithTag("voiceMic").performClick()
        Thread.sleep(300)
        assertEquals("waits for the session's last words before tidying", 0, asked.size)
        onMain { current().events.result("book a room for friday") }
        compose.waitUntil(5000) { asked.size == 1 }
        assertEquals("book a room for friday", asked[0])
        // Tidying never touches the microphone: talking again starts a new session while the round is still out.
        compose.onNodeWithContentDescription("Keep talking").performClick()
        waitForSessions(3)
        onMain { current().events.partial("and invite sam") }
        assertTrue(controller.tidyPending)
        assertTrue((state() as VoiceState.Listening).heard.endsWith("book a room for friday and invite sam"))
        answer(0, TidyResult("Book a room for Friday.", true))
        compose.waitUntil(5000) { (state() as? VoiceState.Listening)?.preview == "Book a room for Friday." }
        assertTrue("still listening after the answer", (state() as VoiceState.Listening).paused.not())
    }

    @Test fun aLongSilencePausesInsteadOfListeningForever() {
        onMain { current().events.result("Check the deploy") }
        waitForSessions(2)
        now += 46_000
        onMain { current().events.ended(SpeechRecognizer.ERROR_SPEECH_TIMEOUT) }
        compose.waitUntil(5000) { (state() as? VoiceState.Listening)?.paused == true }
        compose.onNodeWithTag("voiceHeard").assertTextEquals("Check the deploy")
        assertEquals("No new session while paused", 2, sessions.size)
    }

    @Test fun closingVoiceModeMidSentenceKeepsTheWordsForNextTime() {
        onMain { current().events.partial("Draft a reply to Alex") }
        compose.onNodeWithContentDescription("Close voice mode").assertExists()
        // Leaving the screen, as Home or another app would.
        instrumentation.uiAutomation.executeShellCommand("input keyevent KEYCODE_HOME").close()
        compose.waitUntil(5000) { state() is VoiceState.Review }
        assertTrue(current().closed)
        // Past the moment the turn itself ends, the words still wait.
        Thread.sleep(3000)
        assertTrue(state() is VoiceState.Review)
        app.startActivity(VoiceActivity.intent(app).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        compose.waitUntil(10000) { shown("voiceInput") }
        compose.onNodeWithTag("voiceInput").assertTextContains("Draft a reply to Alex")
        compose.onNodeWithTag("voiceNotice").assertTextContains("Listening stopped when voice mode closed.")
    }

    @Test fun keptWordsExpireAfterTenMinutes() {
        onMain { current().events.partial("Old words") }
        instrumentation.uiAutomation.executeShellCommand("input keyevent KEYCODE_HOME").close()
        compose.waitUntil(5000) { state() is VoiceState.Review }
        Thread.sleep(3000)
        now += 11 * 60_000L
        app.startActivity(VoiceActivity.intent(app).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        compose.waitUntil(10000) { state() is VoiceState.Listening }
        // Nothing kept: no words on screen, just the listening orb.
        assertTrue(compose.onAllNodesWithText("Old words", substring = true).fetchSemanticsNodes().isEmpty())
    }
}
