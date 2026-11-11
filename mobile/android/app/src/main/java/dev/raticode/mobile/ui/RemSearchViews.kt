package dev.raticode.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.RemSearch
import dev.raticode.mobile.live.RemSearchPart
import dev.raticode.mobile.live.RemSearchResult
import dev.raticode.mobile.live.RemSearchState
import java.time.LocalDate

/** The Rem chat header while searching: a back arrow that closes search, the search box and a clear button. */
@Composable
internal fun RemSearchBar(query: String, onQuery: (String) -> Unit, onSearch: () -> Unit, onClose: () -> Unit) {
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }
    Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 8.dp, top = 6.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onClose) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Close search") }
        OutlinedTextField(query, { onQuery(it.take(RemSearch.MAX_QUERY)) }, Modifier.weight(1f).focusRequester(focus).testTag("remSearchField"),
            singleLine = true, placeholder = { Text("Search Rem's chat") }, shape = MaterialTheme.shapes.large,
            leadingIcon = { Icon(ThreadIcons.Search, null) },
            trailingIcon = { if (query.isNotEmpty()) IconButton(onClick = { onQuery(""); focus.requestFocus() }) { Icon(ThreadIcons.Close, "Clear search") } },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search), keyboardActions = KeyboardActions(onSearch = { onSearch() }))
    }
}

/**
 * Results over the chat, which stays where it was underneath. [opening] is the result whose earlier messages are
 * loading; [openError] says why one couldn't be opened.
 */
@Composable
internal fun RemSearchResults(state: RemSearchState, opening: String?, openError: String, onPick: (RemSearchResult) -> Unit,
                              onMore: () -> Unit, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    val muted = LocalRemColors.current.muted
    val use24Hour = android.text.format.DateFormat.is24HourFormat(LocalContext.current)
    val today = LocalDate.now()
    val page = state.page
    Surface(modifier.fillMaxSize().testTag("remSearchResults"), color = MaterialTheme.colorScheme.background) {
        LazyColumn(contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            item(key = "status") {
                Column(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (state.note.isNotBlank()) Surface(color = MaterialTheme.colorScheme.secondaryContainer, shape = MaterialTheme.shapes.medium) {
                        Text(state.note, Modifier.padding(horizontal = 12.dp, vertical = 8.dp).testTag("remSearchNote"), style = MaterialTheme.typography.bodySmall)
                    }
                    val summary = RemSearch.summary(state)
                    if (summary.isNotBlank()) Text(summary, Modifier.semantics { liveRegion = LiveRegionMode.Polite }.testTag("remSearchSummary"),
                        style = MaterialTheme.typography.labelMedium, color = muted)
                    if (state.status == RemSearchState.Status.LOADING) LinearProgressIndicator(Modifier.fillMaxWidth())
                    if (opening != null) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                        Text("Loading earlier messages…", Modifier.semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodySmall)
                    }
                    if (openError.isNotBlank()) Text(openError, Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    when {
                        state.status == RemSearchState.Status.IDLE ->
                            EmptyState("Search Rem's chat", "Finds words in your messages, Rem's replies, task updates and file names, newest first.")
                        state.status == RemSearchState.Status.ERROR -> Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(state.error, Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.error)
                            TextButton(onClick = onRetry) { Text("Try again") }
                        }
                        state.status == RemSearchState.Status.READY && page != null && page.total == 0 ->
                            EmptyState("No matches", "Nothing in Rem's chat matches “${state.query.trim()}”.")
                    }
                }
            }
            items(page?.results.orEmpty(), key = { it.id }) { result ->
                SearchResultRow(result, today, use24Hour, opening == result.id) { onPick(result) }
            }
            if (page?.next != null) item(key = "more") {
                Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                    TextButton(onClick = onMore, enabled = !state.loadingMore) { Text(if (state.loadingMore) "Loading…" else "Show more results") }
                    if (state.error.isNotBlank() && state.status == RemSearchState.Status.READY) Text(state.error, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }
            }
        }
    }
}

@Composable
private fun SearchResultRow(result: RemSearchResult, today: LocalDate, use24Hour: Boolean, opening: Boolean, onClick: () -> Unit) {
    val (who, where) = RemSearch.label(result)
    val stamp = messageDate(result.createdAt)?.let { messageStamp(it, today, use24Hour) }
    val highlight = SpanStyle(background = MaterialTheme.colorScheme.primary.copy(alpha = .18f), fontWeight = FontWeight.SemiBold)
    val title = result.title.joinToString("") { it.text }
    val spoken = listOfNotNull(who, title.ifBlank { null }, result.snippet.joinToString("") { it.text }, where.ifBlank { null }, stamp?.full).joinToString(". ")
    Surface(onClick = onClick, enabled = !opening, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface,
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("remSearchResult")
            .clearAndSetSemantics { contentDescription = spoken; role = Role.Button }) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(who, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
                if (title.isNotBlank()) Text(highlighted(result.title, highlight), Modifier.weight(1f), style = MaterialTheme.typography.labelMedium,
                    color = LocalRemColors.current.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                else Spacer(Modifier.weight(1f))
                stamp?.let { Text(it.label, style = MaterialTheme.typography.labelSmall, color = LocalRemColors.current.muted) }
            }
            Text(highlighted(result.snippet, highlight), style = MaterialTheme.typography.bodyMedium, maxLines = 4, overflow = TextOverflow.Ellipsis)
            if (where.isNotBlank()) Text(where, style = MaterialTheme.typography.labelSmall, color = LocalRemColors.current.muted)
        }
    }
}

private fun highlighted(parts: List<RemSearchPart>, style: SpanStyle): AnnotatedString = buildAnnotatedString {
    parts.forEach { part -> if (part.match) withStyle(style) { append(part.text) } else append(part.text) }
}
