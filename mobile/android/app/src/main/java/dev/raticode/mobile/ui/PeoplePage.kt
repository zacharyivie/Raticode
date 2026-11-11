package dev.raticode.mobile.ui

import android.content.ActivityNotFoundException
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.InputChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.PeopleStore
import dev.raticode.mobile.live.PhonePeople
import dev.raticode.mobile.live.PickPhoneNumber
import dev.raticode.mobile.live.PickedContact
import dev.raticode.mobile.live.SavedPerson
import dev.raticode.mobile.live.readPickedContact
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * People Rem's call and text cards can reach, reached from More. Everything here stays on this phone: the user adds
 * people with Android's contact picker, gives them names such as "brother", changes their number or removes them.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun PeoplePage(onBack: () -> Unit) {
    val context = LocalContext.current
    PeopleStore.load(context)
    val scope = rememberCoroutineScope()
    // The entry a pick is for, or NEW for someone new; kept across the trip to the contacts app.
    var target by rememberSaveable { mutableStateOf<String?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }
    var clearing by remember { mutableStateOf(false) }
    val picker = rememberLauncherForActivityResult(PickPhoneNumber()) { uri ->
        val id = target ?: return@rememberLauncherForActivityResult
        target = null
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            val contact = withContext(Dispatchers.IO) { readPickedContact(context, uri) }
            problem = when {
                contact == null -> "Couldn't read that contact. Choose it again."
                PhonePeople.usableNumber(contact.number) == null -> PeopleStore.UNUSABLE
                else -> { save(context, id, contact); null }
            }
        }
    }
    fun pick(id: String) {
        problem = null; target = id
        try { picker.launch(Unit) } catch (_: ActivityNotFoundException) { target = null; problem = NO_PICKER }
    }
    Column(Modifier.fillMaxSize()) {
        Header("People Rem can reach", "Kept on this phone only", onBack)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 8.dp).testTag("peoplePage"),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Note("When you ask Rem to call or text someone by name, such as “my brother”, the card finds them here. " +
                "Rem only hears the words you said. Numbers and contact names never leave this phone, and you still press Call or Send yourself.")
            problem?.let { Text(it, Modifier.testTag("peopleProblem"), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium) }
            Button(onClick = { pick(NEW) }, modifier = Modifier.testTag("peopleAdd")) { Text("Add someone from contacts") }
            val people = PeopleStore.people
            if (people.isEmpty()) Note("No one yet. Add someone here, or choose a contact on a call or text card from Rem.")
            people.forEach { person -> PersonRow(person, onChange = { pick(person.id) }) }
            if (people.isNotEmpty()) TextButton(onClick = { clearing = true }, modifier = Modifier.testTag("peopleClear")) { Text("Remove everyone") }
        }
    }
    if (clearing) AlertDialog(onDismissRequest = { clearing = false },
        title = { Text("Remove everyone?") },
        text = { Text("Rem's cards will ask you to choose a contact again. Your phone's contacts aren't changed.") },
        confirmButton = { TextButton(onClick = { clearing = false; PeopleStore.update(context) { emptyList() }; PeopleStore.chosen.clear() }) { Text("Remove") } },
        dismissButton = { TextButton(onClick = { clearing = false }) { Text("Cancel") } })
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PersonRow(person: SavedPerson, onChange: () -> Unit) {
    val context = LocalContext.current
    var name by remember(person.id) { mutableStateOf("") }
    val add = { PeopleStore.update(context) { PhonePeople.addName(it, person.id, name) }; name = "" }
    SettingsGroup(person.contactName) {
        Text("${person.label.ifBlank { "Number" }} ending ${PhonePeople.ending(person.number)}", style = MaterialTheme.typography.bodyMedium)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            person.names.forEach { word ->
                InputChip(selected = false, onClick = { PeopleStore.update(context) { PhonePeople.removeName(it, person.id, word) } },
                    label = { Text(word) }, trailingIcon = { Icon(Icons.Filled.Close, "Stop answering to $word") })
            }
        }
        if (person.names.isEmpty()) Note("Answers to “${person.contactName}”. Add a name such as “brother”.")
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(name, { name = it.take(PhonePeople.MAX_NAME) }, Modifier.weight(1f).testTag("peopleName"), singleLine = true,
                label = { Text("Also call them") }, keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { add() }))
            TextButton(onClick = add, enabled = PhonePeople.key(name).isNotEmpty()) { Text("Add") }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = onChange) { Text("Change number") }
            TextButton(onClick = {
                PeopleStore.update(context) { PhonePeople.forget(it, person.id) }
                PeopleStore.chosen.entries.removeAll { it.value.number == person.number }
            }) { Text("Remove") }
        }
    }
}

private fun save(context: android.content.Context, id: String, contact: PickedContact) {
    val now = System.currentTimeMillis()
    PeopleStore.update(context) { if (id == NEW) PhonePeople.remember(it, contact, null, now) else PhonePeople.change(it, id, contact, now) }
}

private const val NEW = "new"
