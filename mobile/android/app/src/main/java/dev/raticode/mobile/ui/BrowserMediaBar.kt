package dev.raticode.mobile.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.browser.BrowserMedia
import dev.raticode.mobile.browser.BrowserMediaRules
import dev.raticode.mobile.browser.BrowserMediaRules.Action

/**
 * Marks [key]'s page as the one the Browser page shows, for as long as this stays composed, so the bar leaves that tab's
 * media to the page and speaks only for other tabs.
 */
@Composable
internal fun ShowingMediaOf(key: BrowserMediaRules.Key) {
    DisposableEffect(key) {
        BrowserMedia.showing(key)
        onDispose { BrowserMedia.hidden(key) }
    }
}

/**
 * The bar above every Raticode page while a tab in one of the app's browsers plays. The site name always shows, since the
 * title and artist are whatever the page says they are. Buttons a page doesn't offer stay in place, disabled.
 */
@Composable
internal fun BrowserMediaBar(track: BrowserMediaRules.Track, onOpen: (BrowserMediaRules.Track) -> Unit, modifier: Modifier = Modifier) {
    val colors = LocalStudioColors.current
    Surface(modifier.fillMaxWidth().testTag("browserMediaBar"), color = colors.navigation, contentColor = colors.onNavigation, shadowElevation = 1.dp) {
        Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(start = 8.dp, end = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Row(Modifier.weight(1f).heightIn(min = 48.dp).clickable(role = Role.Button, onClickLabel = "Open this tab") { onOpen(track) }
                .semantics(mergeDescendants = true) {
                    contentDescription = BrowserMediaRules.spoken(track)
                    stateDescription = if (track.playing) "Playing" else "Paused"
                }.testTag("browserMediaOpen"), verticalAlignment = Alignment.CenterVertically) {
                Thumbnail(track)
                Spacer(Modifier.width(8.dp))
                Column(Modifier.weight(1f)) {
                    Text(track.title, Modifier.testTag("browserMediaTitle"), style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (track.artist.isNotBlank()) {
                            Text(track.artist, Modifier.weight(1f, fill = false), style = MaterialTheme.typography.labelSmall, color = colors.navigationMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(" · ", style = MaterialTheme.typography.labelSmall, color = colors.navigationMuted, maxLines = 1)
                        }
                        // The artist only gets the room left over, so a page can't push the site out of view.
                        Text(track.site, Modifier.widthIn(max = 120.dp).testTag("browserMediaSite"), style = MaterialTheme.typography.labelSmall, color = colors.navigationMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            MediaButton(ThreadIcons.SkipBack, if (track.canPrevious) "Previous" else "Previous, not offered by this page", track.canPrevious, "browserMediaPrevious") { BrowserMedia.perform(track, Action.PREVIOUS) }
            MediaButton(if (track.playing) ThreadIcons.Pause else ThreadIcons.Play,
                when { track.playing && track.canToggle -> "Pause"; track.canToggle -> "Play"; track.playing -> "Pause, not available"; else -> "Play, not available" },
                track.canToggle, "browserMediaToggle") { BrowserMedia.perform(track, if (track.playing) Action.PAUSE else Action.PLAY) }
            MediaButton(ThreadIcons.SkipForward, if (track.canNext) "Next" else "Next, not offered by this page", track.canNext, "browserMediaNext") { BrowserMedia.perform(track, Action.NEXT) }
        }
    }
}

@Composable
private fun Thumbnail(track: BrowserMediaRules.Track) {
    val shape = RoundedCornerShape(6.dp)
    val image = track.artwork?.let { BrowserMedia.images[it] }
    if (image != null) Image(image, null, Modifier.size(40.dp).clip(shape).testTag("browserMediaThumbnail"), contentScale = ContentScale.Crop)
    else Box(Modifier.size(40.dp).background(MaterialTheme.colorScheme.surfaceVariant, shape).testTag("browserMediaNoThumbnail"), contentAlignment = Alignment.Center) {
        Icon(ThreadIcons.Music, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun MediaButton(icon: ImageVector, label: String, enabled: Boolean, tag: String, onClick: () -> Unit) {
    IconButton(onClick = onClick, enabled = enabled, modifier = Modifier.size(48.dp).testTag(tag)) { Icon(icon, label, Modifier.size(22.dp)) }
}
