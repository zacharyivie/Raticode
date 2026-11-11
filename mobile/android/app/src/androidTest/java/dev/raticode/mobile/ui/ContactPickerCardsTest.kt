package dev.raticode.mobile.ui

import android.Manifest
import android.content.ContentProviderOperation
import android.provider.ContactsContract
import android.provider.ContactsContract.CommonDataKinds.Phone
import android.provider.ContactsContract.CommonDataKinds.StructuredName
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.PeopleStore
import dev.raticode.mobile.live.PhoneAction
import dev.raticode.mobile.live.PhoneActionList
import dev.raticode.mobile.live.PhonePeople
import dev.raticode.mobile.live.PickedContact
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Rem's call and text cards against Android's real contact picker, with synthetic contacts written for the test and
 * removed afterwards. The app holds no contacts permission: the contacts are inserted under the shell's permissions,
 * which are dropped again before anything is picked, so reading the pick relies on the picker's one-entry grant.
 * Nothing is called or sent; opening the dialer stops at its screen.
 */
class ContactPickerCardsTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val automation get() = instrumentation.uiAutomation
    private val app get() = instrumentation.targetContext.applicationContext
    private val inserted = mutableListOf<Long>()
    private val opened = mutableListOf<PhoneAction>()
    private val cards = mutableStateOf(PhoneActionList.EMPTY)

    @Before fun setUp() {
        assertEquals("The app must not hold the contacts permission", android.content.pm.PackageManager.PERMISSION_DENIED,
            app.checkSelfPermission(Manifest.permission.READ_CONTACTS))
        insert("Raticode Test Baby Bro", "+1 555-010-0001" to Phone.TYPE_MOBILE)
        insert("Raticode Test Big Bro", "555-010-0002" to Phone.TYPE_MOBILE, "555-010-0009" to Phone.TYPE_WORK)
        PeopleStore.load(app)
        PeopleStore.update(app) { emptyList() }
        PeopleStore.chosen.clear(); PeopleStore.notes.clear()
        compose.setContent {
            RaticodeTheme {
                Column {
                    PhoneActionCards(cards.value, Color.Gray, { card, report -> opened += card; report(null) }, rememberContactPicker())
                }
            }
        }
    }

    @After fun tearDown() {
        PeopleStore.update(app) { emptyList() }
        PeopleStore.chosen.clear(); PeopleStore.notes.clear()
        automation.adoptShellPermissionIdentity(Manifest.permission.WRITE_CONTACTS)
        try {
            inserted.forEach { id ->
                app.contentResolver.delete(ContactsContract.RawContacts.CONTENT_URI.buildUpon()
                    .appendQueryParameter(ContactsContract.CALLER_IS_SYNCADAPTER, "true").build(), "${ContactsContract.RawContacts._ID}=?", arrayOf("$id"))
            }
        } finally { automation.dropShellPermissionIdentity() }
    }

    @Test fun pickRememberResolveChooseAndCancel() {
        // No one saved yet: the card asks the user to choose, remembering the pick for "my brother".
        val text = PhoneAction.TextDraft("", "Synthetic test message, never sent", "my brother")
        show(text)
        compose.onNodeWithText("Remember as “my brother”").assertExists()
        compose.onNodeWithTag("phoneActionPick0").performClick()
        tapInPicker("Raticode Test Baby Bro")
        awaitCard { compose.onAllNodesWithTag("phoneActionPerson0").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Text Raticode Test Baby Bro · Mobile").assertExists()
        compose.onNodeWithText("Number ending 0001").assertExists()
        val saved = PeopleStore.people.single()
        assertEquals(listOf("my brother"), saved.names)
        assertEquals("Mobile", saved.label)
        // The saved list on disk is encrypted: neither the number nor the name appears in it.
        val disk = File(app.noBackupFilesDir, "people-v1.bin").readBytes().toString(Charsets.ISO_8859_1)
        assertFalse(disk.contains("010-0001") || disk.contains("Baby Bro") || disk.contains("brother"))
        screenshot("contact-card-picked.png")
        compose.onNodeWithTag("phoneActionOpen0").performClick()
        val sent = opened.single() as PhoneAction.TextDraft
        assertEquals("+1 555-010-0001", sent.to)
        assertEquals("smsto:%2B1%20555-010-0001", dev.raticode.mobile.live.PhoneActions.intent(sent).uri)

        // A new card with the same person's other name resolves at once, without the picker.
        show(PhoneAction.Dial("", "brother"))
        compose.onNodeWithText("Call Raticode Test Baby Bro · Mobile").assertExists()

        // Someone else, just for this card: a contact with two numbers, where the user chooses the work one. Google
        // Contacts lists each number on its own row under the contact's name.
        show(PhoneAction.TextDraft("", "Another synthetic test", "my brother"))
        compose.onNodeWithTag("phoneActionPick0").performClick()
        tapInPicker("555-010-0009 · Work")
        awaitCard { compose.onAllNodesWithText("Text Raticode Test Big Bro · Work").fetchSemanticsNodes().isNotEmpty() }
        assertEquals("Picking someone else for one card doesn't change who is saved", 1, PeopleStore.people.size)

        // Two people saved under the same words: the card asks which one.
        PeopleStore.update(app) { PhonePeople.remember(it, PickedContact("Raticode Test Big Bro", "555-010-0002", "Mobile", ""), "brother", 2) }
        show(PhoneAction.Dial("", "my brother"))
        compose.onNodeWithText("Which one?").assertExists()
        compose.onNodeWithTag("phoneActionChoice0-0").assertExists()
        compose.onNodeWithTag("phoneActionChoice0-1").performClick()
        assertEquals(PhoneAction.Dial("555-010-0002"), opened.last())
        screenshot("contact-card-choice.png")

        // Backing out of the picker changes nothing and says so.
        show(PhoneAction.TextDraft("", "Synthetic", "Mom"))
        compose.onNodeWithTag("phoneActionPick0").performClick()
        awaitForeign()
        automation.executeShellCommand("input keyevent KEYCODE_BACK").close()
        awaitCard { compose.onAllNodesWithText(PeopleStore.NOT_CHOSEN).fetchSemanticsNodes().isNotEmpty() }
        assertEquals(2, PeopleStore.people.size)
    }

    /** Back from the contacts app: the cards are found again once this app's window is in front. */
    private fun awaitCard(condition: () -> Boolean) = try {
        compose.waitUntil(20_000) { foreign() == null && runCatching(condition).getOrDefault(false) }
    } catch (failure: ComposeTimeoutException) {
        throw AssertionError("front=${automation.rootInActiveWindow?.packageName} notes=${PeopleStore.notes.values} people=${PeopleStore.people.size} chosen=${PeopleStore.chosen.size}", failure)
    }

    private fun show(card: PhoneAction) {
        compose.runOnUiThread { cards.value = PhoneActionList(listOf(card), 0) }
        compose.waitForIdle()
    }

    private fun insert(name: String, vararg numbers: Pair<String, Int>) {
        automation.adoptShellPermissionIdentity(Manifest.permission.WRITE_CONTACTS, Manifest.permission.READ_CONTACTS)
        try {
            val ops = arrayListOf(ContentProviderOperation.newInsert(ContactsContract.RawContacts.CONTENT_URI)
                .withValue(ContactsContract.RawContacts.ACCOUNT_TYPE, null).withValue(ContactsContract.RawContacts.ACCOUNT_NAME, null).build(),
                ContentProviderOperation.newInsert(ContactsContract.Data.CONTENT_URI).withValueBackReference(ContactsContract.Data.RAW_CONTACT_ID, 0)
                    .withValue(ContactsContract.Data.MIMETYPE, StructuredName.CONTENT_ITEM_TYPE).withValue(StructuredName.DISPLAY_NAME, name).build())
            numbers.forEach { (number, type) ->
                ops += ContentProviderOperation.newInsert(ContactsContract.Data.CONTENT_URI).withValueBackReference(ContactsContract.Data.RAW_CONTACT_ID, 0)
                    .withValue(ContactsContract.Data.MIMETYPE, Phone.CONTENT_ITEM_TYPE).withValue(Phone.NUMBER, number).withValue(Phone.TYPE, type).build()
            }
            val result = app.contentResolver.applyBatch(ContactsContract.AUTHORITY, ops)
            inserted += android.content.ContentUris.parseId(result[0].uri!!)
        } finally { automation.dropShellPermissionIdentity() }
    }

    private fun foreign(): AccessibilityNodeInfo? = automation.rootInActiveWindow?.takeIf { it.packageName?.toString() != app.packageName }

    private fun awaitForeign() = compose.waitUntil(20_000) { foreign() != null }

    /** Taps [text] in the contacts app's picker, then [then] if the picker asks which number. */
    private fun tapInPicker(text: String, then: String? = null) {
        tap(text)
        if (then == null) return
        // Some pickers list each number on its own row; others ask after the contact is chosen.
        compose.waitUntil(15_000) { foreign()?.let { find(it, then) } != null || foreign() == null }
        foreign()?.let { tap(then) }
    }

    private fun tap(text: String) {
        var target: AccessibilityNodeInfo? = null
        try {
            compose.waitUntil(20_000) { target = foreign()?.let { find(it, text) }; target != null }
        } catch (failure: ComposeTimeoutException) {
            val seen = mutableListOf<String>()
            fun walk(node: AccessibilityNodeInfo?) { node ?: return; listOfNotNull(node.text, node.contentDescription).forEach { seen += it.toString() }; (0 until node.childCount).forEach { walk(node.getChild(it)) } }
            walk(foreign())
            throw AssertionError("No \"$text\" in ${foreign()?.packageName}: $seen", failure)
        }
        // The picker's rows are clickable around their text; click the row itself.
        var row: AccessibilityNodeInfo? = target
        while (row != null && !row.isClickable) row = row.parent
        if (row?.performAction(AccessibilityNodeInfo.ACTION_CLICK) != true) {
            val bounds = android.graphics.Rect().also { target!!.getBoundsInScreen(it) }
            automation.executeShellCommand("input tap ${bounds.centerX()} ${bounds.centerY()}").close()
        }
    }

    /** The first node on screen whose text contains [text]. The picker's text search misses some rows, so walk the tree. */
    private fun find(node: AccessibilityNodeInfo?, text: String): AccessibilityNodeInfo? {
        node ?: return null
        if (node.text?.toString()?.contains(text) == true && android.graphics.Rect().also { node.getBoundsInScreen(it) }.let { !it.isEmpty && it.top >= 0 }) return node
        return (0 until node.childCount).firstNotNullOfOrNull { find(node.getChild(it), text) }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        automation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }
}
