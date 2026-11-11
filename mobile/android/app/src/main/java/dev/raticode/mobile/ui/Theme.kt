package dev.raticode.mobile.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.raticode.mobile.R

// Shared indigo identity; mobile uses a single neutral canvas across destinations.
private val LightPalette = lightColorScheme(
    primary = Color(0xFF4F46E5), onPrimary = Color.White,
    primaryContainer = Color(0xFFE9E8FF), onPrimaryContainer = Color(0xFF302977),
    secondary = Color(0xFF5C6480), onSecondary = Color.White,
    secondaryContainer = Color(0xFFE9E8FF), onSecondaryContainer = Color(0xFF25304A),
    tertiary = Color(0xFF8A4B04), onTertiary = Color.White,
    background = Color(0xFFF5F6FA), onBackground = Color(0xFF25304A),
    surface = Color(0xFFFFFFFF), onSurface = Color(0xFF25304A),
    surfaceVariant = Color(0xFFEAECF3), onSurfaceVariant = Color(0xFF5C6480),
    surfaceContainerLowest = Color(0xFFFFFFFF), surfaceContainerLow = Color(0xFFFFFFFF),
    surfaceContainer = Color(0xFFF5F6FA), surfaceContainerHigh = Color(0xFFEAECF3),
    surfaceContainerHighest = Color(0xFFEAECF3), surfaceTint = Color.Transparent,
    outline = Color(0xFF747D94), outlineVariant = Color(0xFFDADDEA),
    error = Color(0xFFB42332), onError = Color.White,
    errorContainer = Color(0xFFFFDAD6), onErrorContainer = Color(0xFF410008),
)
private val DarkPalette = darkColorScheme(
    primary = Color(0xFFA5A6FF), onPrimary = Color(0xFF191846),
    primaryContainer = Color(0xFF4F46E5), onPrimaryContainer = Color.White,
    secondary = Color(0xFFBDC9C1), onSecondary = Color(0xFF18291F),
    secondaryContainer = Color(0xFF2D2B41), onSecondaryContainer = Color(0xFFF3F3F5),
    tertiary = Color(0xFFF59E0B), onTertiary = Color(0xFF261800),
    background = Color(0xFF1E1E1E), onBackground = Color(0xFFF3F3F5),
    surface = Color(0xFF242426), onSurface = Color(0xFFF3F3F5),
    surfaceVariant = Color(0xFF27272A), onSurfaceVariant = Color(0xFF9D9DA5),
    surfaceContainerLowest = Color(0xFF1E1E1E), surfaceContainerLow = Color(0xFF242426),
    surfaceContainer = Color(0xFF242426), surfaceContainerHigh = Color(0xFF27272A),
    surfaceContainerHighest = Color(0xFF2A2A2E), surfaceTint = Color.Transparent,
    outline = Color(0xFF777780), outlineVariant = Color(0xFF2A2A2E),
    error = Color(0xFFFFB4AB), onError = Color(0xFF690005),
    errorContainer = Color(0xFF93000A), onErrorContainer = Color(0xFFFFDAD6),
)

data class StudioColors(val navigation: Color, val onNavigation: Color, val navigationMuted: Color,
    val navigationSelected: Color, val chat: Color, val onChat: Color, val reply: Color)
private val LightStudioColors = StudioColors(Color.White, Color(0xFF25304A), Color(0xFF5C6480), Color(0xFFE9E8FF),
        Color(0xFFF5F6FA), Color(0xFF25304A), Color.White)
val LocalStudioColors = staticCompositionLocalOf { LightStudioColors }

/** Desktop RemHome.css: the rat face's sage circle and the user's lavender bubble. */
data class RemColors(val sage: Color, val sageInk: Color, val bubble: Color, val bubbleInk: Color, val muted: Color)
private val LightRemColors = RemColors(Color(0xFFDCE5DF), Color(0xFF2A3D36), Color(0xFFDFDCF4), Color(0xFF2C2A52), Color(0xFF5F605C))
private val DarkRemColors = RemColors(Color(0xFF25302A), Color(0xFFBCD6C6), Color(0xFF2C2B47), Color(0xFFE6E4FF), Color(0xFFA1A1AA))
val LocalRemColors = staticCompositionLocalOf { LightRemColors }

private val StudioType = Typography(
    headlineLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold, fontSize = 28.sp, lineHeight = 34.sp),
    headlineMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold, fontSize = 24.sp, lineHeight = 30.sp),
    headlineSmall = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold, fontSize = 21.sp, lineHeight = 28.sp),
    titleLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold, fontSize = 19.sp, lineHeight = 26.sp),
    titleMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold, fontSize = 15.sp, lineHeight = 22.sp),
    bodyLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 14.sp, lineHeight = 21.sp),
    bodySmall = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 12.sp, lineHeight = 18.sp),
    labelLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Medium, fontSize = 14.sp, lineHeight = 20.sp),
)

@Composable
fun RaticodeTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val view = LocalView.current
    if (!view.isInEditMode) SideEffect {
        val window = (view.context as? android.app.Activity)?.window
        if (window != null) WindowCompat.getInsetsController(window, view).apply {
            isAppearanceLightStatusBars = !darkTheme
            isAppearanceLightNavigationBars = !darkTheme
        }
    }
    val studio = if (darkTheme) StudioColors(Color(0xFF242426), Color(0xFFF3F3F5), Color(0xFF9D9DA5),
        Color(0xFF2D2B41), Color(0xFF1E1E1E), Color(0xFFF3F3F5), Color(0xFF242426)) else LightStudioColors
    CompositionLocalProvider(LocalStudioColors provides studio, LocalRemColors provides if (darkTheme) DarkRemColors else LightRemColors) {
        MaterialTheme(colorScheme = if (darkTheme) DarkPalette else LightPalette, typography = StudioType,
            shapes = Shapes(extraSmall = RoundedCornerShape(4.dp), small = RoundedCornerShape(12.dp),
                medium = RoundedCornerShape(16.dp), large = RoundedCornerShape(20.dp), extraLarge = RoundedCornerShape(24.dp)),
            content = content)
    }
}

@Composable
fun StudioHeader(subtitle: String, modifier: Modifier = Modifier, action: @Composable RowScope.() -> Unit = {}) {
    val colors = LocalStudioColors.current
    Surface(color = colors.navigation, contentColor = colors.onNavigation) {
        Row(modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Image(painterResource(R.drawable.raticode_roundel), contentDescription = null, modifier = Modifier.size(32.dp))
            Column(Modifier.weight(1f)) {
                Text("Raticode", style = MaterialTheme.typography.titleMedium)
                Text(subtitle, style = MaterialTheme.typography.bodySmall, color = colors.navigationMuted)
            }
            action()
        }
    }
}
