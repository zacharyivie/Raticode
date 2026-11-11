package dev.raticode.mobile.voice

import dev.raticode.mobile.live.PhoneAction
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime

/** Synthetic names only. Every case runs on the phone's rules alone; no model and no desktop. */
class PhoneCommandsTest {
    private val now = ZonedDateTime.of(2026, 11, 7, 20, 30, 0, 0, ZoneId.of("America/New_York"))
    private val saved = setOf("baby bro", "brother", "emily", "mom", "big bro")
    private fun parse(text: String) = PhoneCommands.parse(text, { it.lowercase() in saved }, now)

    @Test fun callsByNameOrNumber() {
        assertEquals(PhoneCommand.Call("emily"), parse("Call Emily"))
        // What the S20's recognizer actually produced on 7 November.
        assertEquals(PhoneCommand.Call("emily"), parse("A call Emily."))
        assertEquals(PhoneCommand.Call("brother"), parse("Hey Rem, call my brother please"))
        assertEquals(PhoneCommand.Call("baby bro"), parse("ring baby bro"))
        assertEquals(PhoneCommand.Call("mom", "work"), parse("call mom at work"))
        assertEquals(PhoneCommand.Call("mom", "mobile"), parse("call mom on her cell"))
        assertEquals(PhoneCommand.Call("5551234567"), parse("call 555 123 4567"))
        assertEquals(PhoneCommand.Call("5550100"), parse("dial five five five oh one oh oh"))
    }

    @Test fun everyCallTextOrEmailPhrasingStaysOffTheDesktopWhileItIsSaid() {
        for (said in listOf(
            "give a call to Emily", "make a call to Emily", "place a call to Emily",
            "send mom a text that says Love you", "send Emily a message saying running late",
            "shoot Emily a text saying running late", "send email to sam@example.com about lunch",
        )) {
            assertTrue(said, parse(said) != null)
            assertTrue(said, PhoneCommands.mightBeCommand(said))
        }
    }

    @Test fun textsSplitThePersonFromTheMessage() {
        assertEquals(PhoneCommand.Text("emily", "I'm running late"), parse("Text Emily I'm running late"))
        assertEquals(PhoneCommand.Text("baby bro", "you alive?"), parse("text baby bro you alive?"))
        assertEquals(PhoneCommand.Text("brother", "dinner at 7"), parse("send a text to my brother saying dinner at 7"))
        assertEquals(PhoneCommand.Text("sam", "on my way"), parse("message Sam saying on my way"))
        assertEquals(PhoneCommand.Text("mom", "Love you"), parse("send mom a text that says Love you"))
        assertEquals(PhoneCommand.Text("5551234567", "on my way"), parse("text 555 123 4567 on my way"))
        // Unknown name and no "saying": the first word is the person.
        assertEquals(PhoneCommand.Text("jordan", "where are you"), parse("text jordan where are you"))
        // Profanity is passed through untouched.
        assertEquals(PhoneCommand.Text("emily", "this shit is fucking great"), parse("text emily this shit is fucking great"))
        assertEquals(PhoneCommand.Text("emily", ""), parse("text emily"))
    }

    @Test fun ordinaryRequestsToRemAreNotCommands() {
        for (said in listOf(
            "Call Emily and tell her the build is done", "call it a day", "call me back later", "call Emily tomorrow",
            "text me when the build finishes", "message Rem about the report", "Investigate why the build failed",
            "What's running on my desktop?", "schedule the nightly audit for 4am", "link me to YouTube",
            "play some jazz on Spotify", "set a timer", "turn on dark mode", "Why'd that thread go for so long?",
            "what is the date of the next release", "next steps for the mobile app?", "Can you call the API twice?",
            "text the team that the deploy is done", "message the desktop app",
            // Bare words people say to Rem, which once pressed media keys or muted the phone.
            "continue", "Okay, continue", "stop", "So stop", "next", "skip", "go back", "last", "mute", "unmute", "play",
            "pause", "resume", "louder", "turn it up", "next one", "skip that", "stop the build", "continue the task",
        )) assertNull(said, parse(said))
    }

    @Test fun timersAndAlarms() {
        assertEquals(PhoneCommand.Open(PhoneAction.Timer(1200, "")), parse("set a timer for 20 minutes"))
        assertEquals(PhoneCommand.Open(PhoneAction.Timer(1200, "")), parse("20 minute timer"))
        assertEquals(PhoneCommand.Open(PhoneAction.Timer(5400, "")), parse("set a timer for an hour and a half"))
        assertEquals(PhoneCommand.Open(PhoneAction.Timer(5400, "")), parse("timer for 1 hour 30 minutes"))
        assertEquals(PhoneCommand.Open(PhoneAction.Timer(1800, "")), parse("start a timer for half an hour"))
        assertEquals(PhoneCommand.Open(PhoneAction.Timer(600, "")), parse("set a timer for ten minutes"))
        assertEquals(PhoneCommand.Open(PhoneAction.Timer(2700, "")), parse("set a timer for forty five minutes"))
        assertEquals(PhoneCommand.Open(PhoneAction.Alarm(7, 0, "", emptyList())), parse("wake me up at 7 am"))
        assertEquals(PhoneCommand.Open(PhoneAction.Alarm(18, 45, "", emptyList())), parse("set an alarm for 6:45 pm"))
        // No am or pm: the next one to come round (it is 20:30), so 7 means tomorrow morning.
        assertEquals(PhoneCommand.Open(PhoneAction.Alarm(7, 0, "", emptyList())), parse("set an alarm for 7"))
        assertEquals(PhoneCommand.Open(PhoneAction.Alarm(21, 15, "", emptyList())), parse("alarm for 9 15"))
        assertEquals(PhoneCommand.ShowAlarms, parse("show my alarms"))
    }

    @Test fun clockTimesTakeTheNextOne() {
        assertEquals(LocalTime.of(15, 0), PhoneCommands.clockTime("3", LocalTime.of(9, 0)))
        assertEquals(LocalTime.of(3, 0), PhoneCommands.clockTime("3", LocalTime.of(16, 0)))
        assertEquals(LocalTime.of(6, 45), PhoneCommands.clockTime("six forty five", LocalTime.of(22, 0)))
        assertEquals(LocalTime.NOON, PhoneCommands.clockTime("noon", LocalTime.of(9, 0)))
        assertNull(PhoneCommands.clockTime("25", LocalTime.of(9, 0)))
        assertNull(PhoneCommands.duration("forever"))
    }

    @Test fun mapsCalendarAndEmail() {
        assertEquals(PhoneCommand.Open(PhoneAction.Place("Home Depot", true)), parse("Navigate to Home Depot"))
        assertEquals(PhoneCommand.Open(PhoneAction.Place("the airport", true)), parse("get directions to the airport"))
        assertEquals(PhoneCommand.Open(PhoneAction.Place("coffee near me", false)), parse("find coffee near me"))
        val event = parse("add dinner with Sam to my calendar tomorrow at 7 pm") as PhoneCommand.Open
        val draft = event.action as PhoneAction.CalendarEvent
        assertEquals("dinner with Sam", draft.title)
        assertEquals(ZonedDateTime.of(2026, 11, 8, 19, 0, 0, 0, now.zone).toInstant().toEpochMilli(), draft.start)
        val friday = (parse("create an event called Dentist on friday") as PhoneCommand.Open).action as PhoneAction.CalendarEvent
        assertTrue(friday.allDay)
        assertEquals(ZonedDateTime.of(2026, 11, 13, 0, 0, 0, 0, now.zone).toInstant().toEpochMilli(), friday.start)
        assertEquals(PhoneCommand.Open(PhoneAction.EmailDraft("sam@example.com", "", "the report is ready")),
            parse("email sam@example.com saying the report is ready"))
    }

    @Test fun deviceControlsAndQuestions() {
        assertEquals(PhoneCommand.Flashlight(true), parse("turn on the flashlight"))
        assertEquals(PhoneCommand.Flashlight(false), parse("flashlight off"))
        assertEquals(PhoneCommand.Media(MediaKey.PAUSE), parse("pause the music"))
        assertEquals(PhoneCommand.Media(MediaKey.NEXT), parse("skip this song"))
        assertEquals(PhoneCommand.Media(MediaKey.PREVIOUS), parse("previous track"))
        assertEquals(PhoneCommand.Media(MediaKey.PLAY), parse("resume the music"))
        assertEquals(PhoneCommand.Media(MediaKey.NEXT), parse("play the next song"))
        assertEquals(PhoneCommand.Media(MediaKey.PAUSE), parse("ok stop the podcast"))
        assertEquals(PhoneCommand.Volume(VolumeChange.UP), parse("turn the volume up"))
        assertEquals(PhoneCommand.Volume(VolumeChange.SET, 40), parse("set volume to 40%"))
        assertEquals(PhoneCommand.Volume(VolumeChange.MUTE), parse("mute the sound"))
        assertEquals(PhoneCommand.Ask(Question.TIME), parse("What time is it?"))
        assertEquals(PhoneCommand.Ask(Question.DATE), parse("what's the date today"))
        assertEquals(PhoneCommand.Ask(Question.BATTERY), parse("how much battery do I have left"))
        // Opening needs an installed app of that name; the voice controller checks before treating it as a command.
        assertEquals(PhoneCommand.OpenApp("spotify"), parse("open Spotify"))
    }

    @Test fun commandsInProgressAreSpottedEarly() {
        assertTrue(PhoneCommands.mightBeCommand("text Emily I'm"))
        assertTrue(PhoneCommands.mightBeCommand("um call"))
        assertTrue(PhoneCommands.mightBeCommand("set a timer for"))
        assertFalse(PhoneCommands.mightBeCommand("investigate the build"))
        assertFalse(PhoneCommands.mightBeCommand("what's running"))
        assertFalse(PhoneCommands.mightBeCommand("continue with the plan"))
        assertFalse(PhoneCommands.mightBeCommand("next steps"))
        assertTrue(PhoneCommands.mightBeCommand("pause the music"))
    }

    @Test fun spokenNumbers() {
        assertEquals("+15551234567", PhoneCommands.spokenDigits("+1 555-123-4567"))
        assertEquals("911", PhoneCommands.spokenDigits("nine one one"))
        assertNull(PhoneCommands.spokenDigits("emily"))
        assertNull(PhoneCommands.spokenDigits("55"))
    }
}
