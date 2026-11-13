package dev.raticode.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.data.LongText

/**
 * Under the message box's file chips: the first words of each long text going as a file, its length, and Put back,
 * which returns the words to the message when they fit in Rem's limit. [texts] pairs each attachment ID with its text.
 */
@Composable
internal fun LongTextFiles(texts: List<Pair<String, String>>, draft: String, onPutBack: (String, String) -> Unit) {
    if (texts.isEmpty()) return
    Column(Modifier.fillMaxWidth().padding(start = 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        texts.forEach { (id, text) ->
            Row(Modifier.fillMaxWidth().testTag("remTextFile"), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("“${LongText.preview(text)}”", Modifier.testTag("remTextPreview"), style = MaterialTheme.typography.bodySmall,
                        fontStyle = FontStyle.Italic, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    val fits = LongText.putBack(draft, text) != null
                    Text(LongText.characters(text.length) + if (fits) ", attached as a file" else ", attached as a file because it's too long for one message",
                        style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (LongText.putBack(draft, text) != null) TextButton(onClick = { onPutBack(id, text) },
                    Modifier.testTag("remTextPutBack").semantics { contentDescription = "Put the text back in the message" }) { Text("Put back") }
            }
        }
    }
}

/**
 * Near Rem's limit: how many characters the message has, in the error color once it is full, and a button that sends
 * the message's words as an attached file instead. When that can't happen now, it says why.
 */
@Composable
internal fun LongTextCounter(length: Int, blocked: String?, onToFile: () -> Unit) {
    val count = LongText.counter(length) ?: return
    val full = length >= LongText.LIMIT
    // Announced once the message is full, not on every keystroke before.
    Row(Modifier.fillMaxWidth().padding(start = 8.dp).then(if (full) Modifier.semantics { liveRegion = LiveRegionMode.Polite } else Modifier),
        verticalAlignment = Alignment.CenterVertically) {
        Text(count, Modifier.testTag("remCharCount").semantics { contentDescription = "$count characters" }, style = MaterialTheme.typography.labelSmall,
            color = if (full) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(8.dp))
        if (blocked == null) {
            Text(if (full) "Message is full." else "", Modifier.weight(1f), style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = onToFile, Modifier.testTag("remToFile")) { Text("Attach as file") }
        } else if (full) Text(blocked, Modifier.weight(1f), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
