package dev.raticode.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/** YouTube's bar measures 56 dp on the user's phone. Material's NavigationBar can't go below 80 dp, so Raticode draws its own. */
private val BarHeight = 56.dp

/** Raticode's bottom bar. It grows with large text instead of cutting labels off. System insets are applied by the screen around it. */
@Composable
internal fun RaticodeBottomBar(modifier: Modifier = Modifier, content: @Composable RowScope.() -> Unit) {
    Surface(modifier, color = NavigationBarDefaults.containerColor, tonalElevation = NavigationBarDefaults.Elevation) {
        Row(Modifier.fillMaxWidth().heightIn(min = BarHeight).selectableGroup(), verticalAlignment = Alignment.CenterVertically, content = content)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun RowScope.RaticodeBottomItem(selected: Boolean, onClick: () -> Unit, label: String, description: String, icon: ImageVector, badge: String? = null) {
    val colors = MaterialTheme.colorScheme
    Column(Modifier.weight(1f).heightIn(min = BarHeight).selectable(selected, role = Role.Tab, onClick = onClick).semantics { contentDescription = description }.padding(vertical = 4.dp),
        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        Box(Modifier.size(width = 52.dp, height = 28.dp).background(if (selected) colors.secondaryContainer else Color.Transparent, CircleShape), contentAlignment = Alignment.Center) {
            BadgedBox(badge = { if (badge != null) Badge { Text(badge) } }) {
                Icon(icon, null, Modifier.size(22.dp), tint = if (selected) colors.onSecondaryContainer else colors.onSurfaceVariant)
            }
        }
        // Five items on a 320 dp phone leave about 64 dp each, enough for the longest name at this size.
        Text(label, color = if (selected) colors.onSurface else colors.onSurfaceVariant, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis, softWrap = false)
    }
}
