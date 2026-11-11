package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.ZoneId
import java.util.Locale

class PhoneActionsTest {
    private val zone = ZoneId.of("America/New_York")
    private fun action(json: String) = PhoneActions.parse(JSONObject(json), zone)

    @Test fun timerAndAlarmOpenTheClockScreenWithoutSkippingIt() {
        val timer = action("""{"type":"timer","seconds":1200,"label":"Pasta"}""") as PhoneAction.Timer
        val spec = PhoneActions.intent(timer)
        assertEquals("android.intent.action.SET_TIMER", spec.action)
        assertEquals(1200, spec.extras["android.intent.extra.alarm.LENGTH"])
        assertEquals(false, spec.extras["android.intent.extra.alarm.SKIP_UI"])
        assertEquals("Timer · 20 min", PhoneActions.title(timer))

        val alarm = action("""{"type":"alarm","hour":7,"minute":5,"days":["fri","mon"]}""") as PhoneAction.Alarm
        assertEquals(listOf("mon", "fri"), alarm.days)
        val alarmSpec = PhoneActions.intent(alarm)
        assertEquals("android.intent.action.SET_ALARM", alarmSpec.action)
        assertEquals(arrayListOf(2, 6), alarmSpec.extras["android.intent.extra.alarm.DAYS"])
        assertEquals(false, alarmSpec.extras["android.intent.extra.alarm.SKIP_UI"])
        // Written the way the phone writes times, so a 12-hour phone shows AM or PM.
        assertEquals("Alarm · 7:05 AM", PhoneActions.title(alarm, Locale.US).replace('\u202f', ' '))
        assertEquals("Alarm · 07:05", PhoneActions.title(alarm, Locale.GERMANY))
    }

    @Test fun clockValuesOutsideTheirRangeAreRefused() {
        assertNull(action("""{"type":"timer","seconds":0}"""))
        assertNull(action("""{"type":"timer","seconds":86401}"""))
        assertNull(action("""{"type":"timer","seconds":"60"}"""))
        assertNull(action("""{"type":"timer","seconds":60.5}"""))
        assertNull(action("""{"type":"alarm","hour":24,"minute":0}"""))
        assertNull(action("""{"type":"alarm","hour":7,"minute":0,"days":["someday"]}"""))
        assertNull(action("""{"type":"alarm","hour":7,"minute":0,"days":["mon","mon"]}"""))
        assertNull(action("""{"type":"alarm","hour":7,"minute":0,"days":"mon"}"""))
    }

    @Test fun calendarDraftsUseTheGivenTimesAndShowThemOnTheCard() {
        val event = action("""{"type":"calendar_event","title":"Dinner with Sam","start":"2026-11-06T19:00:00-05:00","location":"Luigi's"}""")
            as PhoneAction.CalendarEvent
        assertEquals(java.time.Instant.parse("2026-11-07T00:00:00Z").toEpochMilli(), event.start)
        assertEquals(event.start + 3_600_000, event.end)
        val spec = PhoneActions.intent(event)
        assertEquals("android.intent.action.INSERT", spec.action)
        assertEquals("content://com.android.calendar/events", spec.uri)
        assertEquals("vnd.android.cursor.dir/event", spec.type)
        assertEquals("Dinner with Sam", spec.extras["title"])
        assertEquals("Luigi's", spec.extras["eventLocation"])
        val before = java.time.Instant.parse("2026-11-04T12:00:00Z")
        assertTrue(PhoneActions.details(event, zone, Locale.US, before).first().startsWith("Nov 6, 2026"))
        assertFalse(PhoneActions.PAST in PhoneActions.details(event, zone, Locale.US, before))
        // A draft for a time already over says so on the card; the user can still open and fix it.
        assertTrue(PhoneActions.PAST in PhoneActions.details(event, zone, Locale.US, java.time.Instant.parse("2026-11-08T00:00:00Z")))

        val allDay = action("""{"type":"calendar_event","title":"Trip","start":"2026-11-06","end":"2026-11-08"}""") as PhoneAction.CalendarEvent
        assertTrue(allDay.allDay)
        assertEquals(3 * 86_400_000L, allDay.end - allDay.start)
        // A time without an offset is read in the phone's own time zone.
        val local = action("""{"type":"calendar_event","title":"Call","start":"2026-11-06T09:30:00"}""") as PhoneAction.CalendarEvent
        assertEquals(java.time.Instant.parse("2026-11-06T14:30:00Z").toEpochMilli(), local.start)
    }

    @Test fun calendarDraftsWithBadTimesAreRefused() {
        assertNull(action("""{"type":"calendar_event","title":"","start":"2026-11-06T19:00:00Z"}"""))
        assertNull(action("""{"type":"calendar_event","title":"X","start":"next friday"}"""))
        assertNull(action("""{"type":"calendar_event","title":"X","start":"2026-11-06T19:00:00Z","end":"2026-11-06T18:00:00Z"}"""))
        assertNull(action("""{"type":"calendar_event","title":"X","start":"2026-11-06T19:00:00Z","end":"2026-12-06T19:00:00Z"}"""))
        assertNull(action("""{"type":"calendar_event","title":"X","start":"2026-11-06T19:00:00Z","attendees":["a@b.c"]}"""))
    }

    @Test fun dialOnlyOpensTheDialerAndRefusesServiceCodes() {
        val dial = action("""{"type":"dial","number":"+1 (555) 123-4567"}""") as PhoneAction.Dial
        val spec = PhoneActions.intent(dial)
        assertEquals("android.intent.action.DIAL", spec.action)
        assertEquals("tel:%2B1%20%28555%29%20123-4567", spec.uri)
        for (number in listOf("*#06#", "555#1234", "*67 555 1234", "555,1234", "555;1234", "1-800-FLOWERS", "12", "+1 555 123 4567 8901 23"))
            assertNull(number, action(JSONObject().put("type", "dial").put("number", number).toString()))
    }

    @Test fun draftsNeverSendAndKeepToOneRecipient() {
        val text = action("""{"type":"sms_draft","to":"555 123 4567","body":"Running late"}""") as PhoneAction.TextDraft
        val spec = PhoneActions.intent(text)
        assertEquals("android.intent.action.SENDTO", spec.action)
        assertEquals("smsto:555%20123%204567", spec.uri)
        assertEquals("Running late", spec.extras["sms_body"])
        // No number: the messaging app asks who to send it to. Rem never gets the contact list.
        assertEquals("smsto:", PhoneActions.intent(action("""{"type":"sms_draft","body":"Hi"}""")!!).uri)
        assertNull(action("""{"type":"sms_draft","to":"5551234567,5559876543","body":"Hi"}"""))
        assertNull(action("""{"type":"sms_draft"}"""))

        val email = action("""{"type":"email_draft","to":"sam@example.com","subject":"Dinner","body":"Friday?"}""") as PhoneAction.EmailDraft
        val mail = PhoneActions.intent(email)
        assertEquals("mailto:", mail.uri)
        assertEquals(listOf("sam@example.com"), (mail.extras["android.intent.extra.EMAIL"] as Array<*>).toList())
        for (to in listOf("a@b.com,c@d.com", "a@b.com;c@d.com", "Sam <sam@example.com>", "sam@@example.com", "sam"))
            assertNull(to, action(JSONObject().put("type", "email_draft").put("to", to).put("body", "x").toString()))
    }

    @Test fun mapQueriesAreEncodedSoTheyCantAddParameters() {
        val place = action("""{"type":"map","query":"1st & Pike, Seattle"}""") as PhoneAction.Place
        assertEquals("geo:0,0?q=1st%20%26%20Pike%2C%20Seattle", PhoneActions.intent(place).uri)
        // Without a maps app the same place opens as a web map.
        assertEquals("https://www.google.com/maps/search/?api=1&query=1st%20%26%20Pike%2C%20Seattle", PhoneActions.intent(place).fallbackUri)
        val route = action("""{"type":"map","query":"Home Depot?x=1#y","directions":true}""") as PhoneAction.Place
        assertEquals("https://www.google.com/maps/dir/?api=1&destination=Home%20Depot%3Fx%3D1%23y", PhoneActions.intent(route).uri)
    }

    @Test fun unknownTypesFieldsAndHiddenCharactersAreRefused() {
        assertNull(action("""{"type":"call","number":"5551234567"}"""))
        assertNull(action("""{"type":"open_app","package":"com.bank"}"""))
        assertNull(action("""{"type":"sms_send","to":"5551234567","body":"Hi"}"""))
        assertNull(action("""{"type":"timer","seconds":60,"skip_ui":true}"""))
        assertNull(action("""{"seconds":60}"""))
        assertNull(action("""{"type":"map","query":"Bank‮"}"""))
        assertNull(action("""{"type":"map","query":"two\nlines"}"""))
        assertNull(action("""{"type":"sms_draft","to":"5551234567","body":"${"x".repeat(1001)}"}"""))
    }

    @Test fun onlyMapsHaveAFallbackAndButtonsSayWhatTheyOpen() {
        val others = listOf("""{"type":"timer","seconds":60}""", """{"type":"dial","number":"5551234567"}""",
            """{"type":"map","query":"Home","directions":true}""", """{"type":"sms_draft","body":"Hi"}""")
        for (json in others) assertNull(json, PhoneActions.intent(action(json)!!).fallbackUri)
        val timer = action("""{"type":"timer","seconds":1200}""")!!
        assertEquals("Start timer: Timer · 20 min", PhoneActions.spoken(timer, Locale.US))
        // Clock apps act on the tap, so the card says so instead of promising a screen to confirm on.
        assertTrue(PhoneActions.details(timer).any { "starts it when you tap" in it })
        assertEquals("Set alarm", PhoneActions.button(action("""{"type":"alarm","hour":7,"minute":0}""")!!))
    }

    @Test fun aMessageShowsAtMostThreeActionsAndCountsTheRest() {
        val raw = JSONArray()
        repeat(4) { raw.put(JSONObject().put("type", "timer").put("seconds", 60 * (it + 1))) }
        raw.put(JSONObject().put("type", "call").put("number", "5551234567")).put("not an object")
        val list = PhoneActions.parse(raw, zone)
        assertEquals(3, list.actions.size)
        assertEquals(3, list.refused)
        assertEquals(PhoneActionList.EMPTY, PhoneActions.parse(null, zone))
    }

    @Test fun callsAndTextsCanNameAPersonInsteadOfANumber() {
        val call = action("""{"type":"dial","recipient":" my brother "}""") as PhoneAction.Dial
        assertEquals(PhoneAction.Dial("", "my brother"), call)
        assertEquals("Call my brother", PhoneActions.title(call))
        val text = action("""{"type":"sms_draft","to":"","body":"You alive?","recipient":"Baby Bro"}""") as PhoneAction.TextDraft
        assertEquals(PhoneAction.TextDraft("", "You alive?", "Baby Bro"), text)
        assertEquals("Baby Bro", PhoneActions.recipientOf(text))
        assertEquals("", PhoneActions.recipientOf(PhoneAction.Dial("5551234567")))
        // A number and a person at once, or neither, is refused; so is a number disguised as a name.
        for (json in listOf("""{"type":"dial","number":"5551234567","recipient":"brother"}""", """{"type":"dial"}""",
                """{"type":"sms_draft","to":"5551234567","recipient":"brother","body":"hi"}""",
                """{"type":"dial","recipient":"555 123 4567"}""", """{"type":"dial","recipient":"bro 555"}""",
                """{"type":"dial","recipient":"${"x".repeat(61)}"}""", """{"type":"dial","recipient":"Bro\u202e"}""",
                """{"type":"email_draft","recipient":"brother","body":"x"}"""))
            assertNull(json, action(json))
    }

    @Test fun onlyRemsRepliesCarryActions() {
        val actions = JSONArray().put(JSONObject().put("type", "timer").put("seconds", 60))
        val home = DesktopViews.parse("rem.home", JSONObject().put("messages", JSONArray()
            .put(JSONObject().put("id", "u").put("role", "user").put("body", "Timer").put("phone_actions", actions))
            .put(JSONObject().put("id", "r").put("role", "rem").put("body", "Here it is").put("phone_actions", actions)))) as RemHome
        assertTrue(home.messages[0].phoneActions.actions.isEmpty())
        assertEquals(listOf(PhoneAction.Timer(60, "")), home.messages[1].phoneActions.actions)
    }

    /** The assistant role grants these without asking if the app ever lists them. Phone actions must never need them. */
    /**
     * The assistant role grants the whole SMS group and the call log without asking if the app lists them. Only placing
     * calls and sending texts are allowed, for voice mode's own commands; reading texts, the call log or contacts never.
     */
    @Test fun manifestListsOnlyCallingAndSendingNotReading() {
        val manifest = File("src/main/AndroidManifest.xml").readText()
        val listed = Regex("uses-permission[^>]*android:name=\"([^\"]+)\"").findAll(manifest).map { it.groupValues[1] }.toList()
        assertTrue(listed.isNotEmpty())
        val allowed = setOf("android.permission.CALL_PHONE", "android.permission.SEND_SMS")
        val forbidden = Regex("SMS|MMS|WAP_PUSH|CALL_PHONE|CALL_LOG|CONTACTS|SYSTEM_ALERT_WINDOW|ACCESSIBILITY|NOTIFICATION_LISTENER|QUERY_ALL_PACKAGES|PHONE_STATE|PHONE_NUMBERS|ANSWER_PHONE|CELL_BROADCAST")
        assertFalse(listed.toString(), listed.filter { it !in allowed }.any { forbidden.containsMatchIn(it) })
        assertTrue(listed.containsAll(allowed))
        assertFalse(manifest.contains("android.accessibilityservice.AccessibilityService"))
    }
}
