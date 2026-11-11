package dev.raticode.mobile.ui

import android.app.Activity
import android.app.KeyguardManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.core.net.toUri
import dev.raticode.mobile.live.PhoneAction
import dev.raticode.mobile.live.PhoneActionList
import dev.raticode.mobile.live.PhoneActions
import dev.raticode.mobile.live.PhoneIntentSpec
import kotlinx.coroutines.delay
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.TextButton
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.semantics.Role
import dev.raticode.mobile.live.PeopleStore
import dev.raticode.mobile.live.PersonMatch
import dev.raticode.mobile.live.PhonePeople
import dev.raticode.mobile.live.PickPhoneNumber
import dev.raticode.mobile.live.PickedContact
import dev.raticode.mobile.live.readPickedContact
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Rem's suggested phone actions under a reply. Each card shows everything the other app will receive;
 * nothing opens until the user taps its button. [onOpen] is given the action and reports a problem sentence.
 * [onPick] opens Android's contact picker for a call or text card naming a person; with `true` the pick is remembered
 * for the card's words. [hideNames] keeps saved people's names off a locked screen.
 */
@Composable
internal fun PhoneActionCards(list: PhoneActionList, muted: Color, onOpen: (PhoneAction, (String?) -> Unit) -> Unit,
                              onPick: (PhoneAction, Boolean) -> Unit, hideNames: Boolean = false) {
    if (list.actions.isEmpty() && list.refused == 0) return
    Column(Modifier.fillMaxWidth().testTag("phoneActions"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        list.actions.forEachIndexed { index, action -> PhoneActionCard(action, index, muted, onOpen, onPick, hideNames) }
        if (list.refused > 0) Text(
            if (list.refused == 1) "Rem suggested a phone action this app doesn't allow, so it isn't shown."
            else "Rem suggested ${list.refused} phone actions this app doesn't allow, so they aren't shown.",
            Modifier.testTag("phoneActionsRefused"), style = MaterialTheme.typography.bodySmall, color = muted)
    }
}

@Composable
private fun PhoneActionCard(action: PhoneAction, index: Int, muted: Color, onOpen: (PhoneAction, (String?) -> Unit) -> Unit,
                            onPick: (PhoneAction, Boolean) -> Unit, hideNames: Boolean) {
    var problem by remember(action) { mutableStateOf<String?>(null) }
    // Set on a tap and cleared after a moment or a problem, so a double tap opens the other app once.
    var opening by remember(action) { mutableStateOf(false) }
    LaunchedEffect(opening) { if (opening) { delay(PhoneActions.REOPEN_MS); opening = false } }
    val open = { card: PhoneAction -> if (!opening) { opening = true; problem = null; onOpen(card) { problem = it; if (it != null) opening = false } } }
    Surface(shape = RoundedCornerShape(12.dp), color = LocalContentColor.current.copy(alpha = .05f),
        border = BorderStroke(1.dp, LocalContentColor.current.copy(alpha = .22f)), modifier = Modifier.fillMaxWidth().testTag("phoneAction$index")) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Suggested for this phone", style = MaterialTheme.typography.labelSmall, color = muted)
            if (PhoneActions.recipientOf(action).isNotEmpty()) PersonCard(action, index, muted, opening, open, onPick, hideNames)
            else {
                Text(PhoneActions.title(action), style = MaterialTheme.typography.titleSmall)
                PhoneActions.details(action).forEach { Text(it, style = MaterialTheme.typography.bodySmall, maxLines = 6) }
                val spoken = PhoneActions.spoken(action)
                OutlinedButton(onClick = { open(action) }, enabled = !opening,
                    modifier = Modifier.padding(top = 4.dp).testTag("phoneActionOpen$index").semantics { contentDescription = spoken }) {
                    Text(PhoneActions.button(action))
                }
            }
            (problem ?: PeopleStore.notes[action])?.let { Text(it, Modifier.testTag("phoneActionProblem$index").semantics { liveRegion = LiveRegionMode.Polite },
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
        }
    }
}

/**
 * A call or text card that names a person. The phone finds the number among people the user saved here: one match opens
 * straight away, several ask which, and none asks the user to pick a contact. Rem never learns who or which number.
 */
@Composable
private fun PersonCard(action: PhoneAction, index: Int, muted: Color, opening: Boolean, open: (PhoneAction) -> Unit,
                       onPick: (PhoneAction, Boolean) -> Unit, hideNames: Boolean) {
    val context = LocalContext.current
    PeopleStore.load(context)
    val words = PhoneActions.recipientOf(action)
    val text = action is PhoneAction.TextDraft
    val verb = if (text) "Text" else "Call"
    val match = PhonePeople.resolve(PeopleStore.people, words, PeopleStore.chosen[action])
    val body = (action as? PhoneAction.TextDraft)?.body.orEmpty()
    val finish = if (text) "Opens a draft. You choose to send it." else "Opens the dialer. You place the call."
    if (hideNames) {
        Text("$verb a saved contact", style = MaterialTheme.typography.titleSmall)
        Text("Unlock your phone to see who this goes to.", style = MaterialTheme.typography.bodySmall, color = muted)
        return
    }
    // Remembering is the usual choice for someone new; when the words already mean someone, a pick is just for now.
    var keep by remember(action, match is PersonMatch.None) { mutableStateOf(match is PersonMatch.None) }
    val someoneElse = @Composable {
        TextButton(onClick = { onPick(action, keep) }, enabled = !opening, modifier = Modifier.testTag("phoneActionPick$index")) {
            Text(if (match is PersonMatch.None) "Choose contact" else "Someone else")
        }
    }
    when (match) {
        is PersonMatch.One -> {
            val person = match.person
            Text("$verb ${PhonePeople.describe(person)}", Modifier.testTag("phoneActionPerson$index"), style = MaterialTheme.typography.titleSmall)
            Text("Number ending ${PhonePeople.ending(person.number)}", style = MaterialTheme.typography.bodySmall, color = muted)
            if (body.isNotEmpty()) Text(body, style = MaterialTheme.typography.bodySmall, maxLines = 6)
            Text(finish, style = MaterialTheme.typography.bodySmall)
            val card = PhoneActions.withNumber(action, person.number)
            val spoken = PhoneActions.button(card) + ": " + verb + " " + person.contactName
            OutlinedButton(onClick = { open(card) }, enabled = !opening,
                modifier = Modifier.padding(top = 4.dp).testTag("phoneActionOpen$index").semantics { contentDescription = spoken }) {
                Text(PhoneActions.button(card))
            }
            someoneElse()
        }
        is PersonMatch.Several -> {
            Text("$verb $words", style = MaterialTheme.typography.titleSmall)
            Text("Which one?", style = MaterialTheme.typography.bodySmall, color = muted)
            if (body.isNotEmpty()) Text(body, style = MaterialTheme.typography.bodySmall, maxLines = 6)
            match.people.forEachIndexed { choice, person ->
                val card = PhoneActions.withNumber(action, person.number)
                OutlinedButton(onClick = { open(card) }, enabled = !opening, modifier = Modifier.testTag("phoneActionChoice$index-$choice")) {
                    Text(PhonePeople.describe(person) + " · ending " + PhonePeople.ending(person.number))
                }
            }
            Text(finish, style = MaterialTheme.typography.bodySmall)
            someoneElse()
        }
        PersonMatch.None -> {
            Text("$verb $words", style = MaterialTheme.typography.titleSmall)
            if (body.isNotEmpty()) Text(body, style = MaterialTheme.typography.bodySmall, maxLines = 6)
            Text("No one is saved as “$words” on this phone yet. Choose them in your contacts; the number stays on this phone.",
                style = MaterialTheme.typography.bodySmall, color = muted)
            Row(Modifier.fillMaxWidth().toggleable(keep, role = Role.Checkbox) { keep = it }.testTag("phoneActionRemember$index"),
                verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = keep, onCheckedChange = null)
                Text("Remember as “$words”", style = MaterialTheme.typography.bodyMedium)
            }
            Button(onClick = { onPick(action, keep) }, enabled = !opening, modifier = Modifier.testTag("phoneActionPick$index")) { Text("Choose contact") }
        }
    }
}

/**
 * Opens Android's contact picker for a call or text card in Rem's chat and records the person picked. The picker shows
 * the user's contacts to the user, not to Raticode, which only reads the one number chosen.
 */
@Composable
internal fun rememberContactPicker(): (PhoneAction, Boolean) -> Unit {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var pending by remember { mutableStateOf<Pair<PhoneAction, Boolean>?>(null) }
    val launcher = rememberLauncherForActivityResult(PickPhoneNumber()) { uri ->
        val (card, keep) = pending ?: return@rememberLauncherForActivityResult
        pending = null
        scope.launch { finishPick(context, card, keep, uri?.let { withContext(Dispatchers.IO) { readPickedContact(context, it) } }, uri != null) }
    }
    return remember(launcher) { { card, keep ->
        pending = card to keep
        PeopleStore.notes.remove(card)
        try { launcher.launch(Unit) } catch (_: ActivityNotFoundException) { pending = null; PeopleStore.notes[card] = NO_PICKER }
    } }
}

/** Records a pick for [card]. [returned] is whether the picker gave anything back, so a pick that can't be read says so. */
internal fun finishPick(context: Context, card: PhoneAction, keep: Boolean, contact: PickedContact?, returned: Boolean) {
    if (returned && contact == null) PeopleStore.notes[card] = UNREADABLE
    else PeopleStore.picked(context, card, contact, keep)
}

internal const val NO_PICKER = "This phone has no contacts app to choose from."
private const val UNREADABLE = "Couldn't read that contact. Choose it again."

/** Opens a card's action from Rem's chat. The lock screen, if it is showing, must be dismissed first. */
@Composable
internal fun rememberPhoneActionOpener(): (PhoneAction, (String?) -> Unit) -> Unit {
    val context = LocalContext.current
    return remember(context) { { action, report -> context.findActivity()?.let { openPhoneAction(it, action, report) } ?: report(CANT_OPEN) } }
}

/**
 * Starts the other app for [action] after a tap. On a locked phone Android asks for the PIN, pattern, password or
 * fingerprint first, and nothing opens unless that succeeds. [report] receives a sentence when it doesn't open.
 */
internal fun openPhoneAction(activity: Activity, action: PhoneAction, report: (String?) -> Unit) {
    val keyguard = activity.getSystemService(KeyguardManager::class.java)
    if (!keyguard.isKeyguardLocked) { report(startPhoneAction(activity, action)); return }
    keyguard.requestDismissKeyguard(activity, object : KeyguardManager.KeyguardDismissCallback() {
        override fun onDismissSucceeded() { report(startPhoneAction(activity, action)) }
        override fun onDismissCancelled() { report(UNLOCK) }
        override fun onDismissError() { report(UNLOCK) }
    })
}

/**
 * Opens the other app now; call only after a tap on an unlocked phone. Returns a problem sentence, or null once it opened.
 * [newTask] puts the other app in its own task, as voice mode needs: its screen is hidden from Recents and closes after.
 */
internal fun startPhoneAction(activity: Activity, action: PhoneAction, newTask: Boolean = false): String? =
    startSpec(activity, PhoneActions.intent(action), newTask)

/** Starts what [spec] describes, trying its fallback address too. Returns a problem sentence, or null once it opened. */
internal fun startSpec(activity: Activity, spec: PhoneIntentSpec, newTask: Boolean = false): String? {
    for (uri in listOfNotNull(spec.uri, spec.fallbackUri).ifEmpty { listOf(null) }) {
        val intent = spec.toIntent(uri).apply { if (newTask) addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) }
        try { activity.startActivity(intent); return null }
        catch (_: ActivityNotFoundException) { continue }
        catch (_: SecurityException) { return BLOCKED }
    }
    return NO_APP
}

internal fun PhoneIntentSpec.toIntent(data: String? = uri): Intent = Intent(action).also { intent ->
    // An installed app the user named: its launcher screen, nothing else.
    pkg?.let { intent.setPackage(it); if (action == Intent.ACTION_MAIN) intent.addCategory(Intent.CATEGORY_LAUNCHER) }
    val parsed = data?.toUri()
    when {
        parsed != null && type != null -> intent.setDataAndType(parsed, type)
        parsed != null -> intent.data = parsed
        type != null -> intent.type = type
    }
    extras.forEach { (key, value) ->
        when (value) {
            is Int -> intent.putExtra(key, value)
            is Long -> intent.putExtra(key, value)
            is Boolean -> intent.putExtra(key, value)
            is String -> intent.putExtra(key, value)
            is ArrayList<*> -> intent.putIntegerArrayListExtra(key, ArrayList(value.map { it as Int }))
            is Array<*> -> intent.putExtra(key, value.map { it as String }.toTypedArray())
        }
    }
}

internal tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

private const val UNLOCK = "Unlock your phone to open this."
private const val NO_APP = "No app on this phone can open this."
private const val BLOCKED = "Android didn't let Raticode open this app."
private const val CANT_OPEN = "This screen can't open other apps. Open Rem's chat and try again."
