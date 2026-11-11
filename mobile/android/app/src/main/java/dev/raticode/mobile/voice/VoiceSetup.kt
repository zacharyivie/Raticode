package dev.raticode.mobile.voice

import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.Settings
import java.util.Locale

/** Whether Raticode can be, or already is, the phone's digital assistant. */
enum class AssistantSupport {
    /** Older than Android 12: no on-device speech recognition, so voice mode is off. */
    UNSUPPORTED,
    /** Android doesn't offer the assistant choice here, such as in a work profile. */
    ROLE_UNAVAILABLE,
    SELECTED,
    NOT_SELECTED,
}

/** What the user is told to tap: where to choose the assistant, and what the power or side button can do. */
data class AssistantSteps(val choose: String, val button: String)

/**
 * Setting up Rem as the digital assistant. Android lets only the user choose the assistant, so the app opens the
 * closest settings page and explains the rest; it never chooses for them.
 */
object VoiceSetup {
    fun support(context: Context): AssistantSupport {
        if (Build.VERSION.SDK_INT < 31) return AssistantSupport.UNSUPPORTED
        val roles = context.getSystemService(RoleManager::class.java) ?: return AssistantSupport.ROLE_UNAVAILABLE
        return runCatching {
            when {
                !roles.isRoleAvailable(RoleManager.ROLE_ASSISTANT) -> AssistantSupport.ROLE_UNAVAILABLE
                roles.isRoleHeld(RoleManager.ROLE_ASSISTANT) -> AssistantSupport.SELECTED
                else -> AssistantSupport.NOT_SELECTED
            }
        }.getOrDefault(AssistantSupport.ROLE_UNAVAILABLE)
    }

    /** The launch offer appears for a paired phone that could choose Raticode, until the user says not to ask again. */
    fun offerOnLaunch(support: AssistantSupport, paired: Boolean, allowed: Boolean): Boolean =
        support == AssistantSupport.NOT_SELECTED && paired && allowed

    /**
     * Settings pages to try, closest first. Pixel's voice input page is the assistant picker itself; Samsung lists the
     * digital assistant under Apps › Choose default apps. Apps can't open Android's assistant picker directly.
     */
    fun settingsActions(manufacturer: String = Build.MANUFACTURER): List<String> = when (manufacturer.lowercase(Locale.ROOT)) {
        "samsung" -> listOf(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS, Settings.ACTION_VOICE_INPUT_SETTINGS)
        else -> listOf(Settings.ACTION_VOICE_INPUT_SETTINGS, Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS)
    }

    /** Opens the first settings page the phone has. False means none opened, so the steps have to be followed by hand. */
    fun openAssistantSettings(context: Context): Boolean = settingsActions().any { action ->
        runCatching { context.startActivity(Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.isSuccess
    }

    /** Galaxy S20, S20+, S20 Ultra and S20 FE (SM-G98x, SM-G78x). Their last update was One UI 5.1 on Android 13. */
    fun isGalaxyS20(manufacturer: String = Build.MANUFACTURER, model: String = Build.MODEL): Boolean =
        manufacturer.equals("samsung", ignoreCase = true) && Regex("^SM-G(98[0-8]|78[01])").containsMatchIn(model.uppercase(Locale.ROOT))

    fun steps(manufacturer: String = Build.MANUFACTURER, model: String = Build.MODEL): AssistantSteps = when {
        isGalaxyS20(manufacturer, model) -> AssistantSteps(
            "In Settings › Apps › Choose default apps, tap Digital assistant app, then Digital assistant app again, and pick Raticode.",
            "On the Galaxy S20, Settings › Advanced features › Side key › Press and hold usually offers only Bixby or the power-off menu, so the side key may not open Rem. " +
                "If yours lists Digital assistant there, choose it. Otherwise hold Home (with navigation buttons) or swipe up from a bottom corner (with swipe gestures, where offered), " +
                "or use the Talk to Rem tile or the shortcut on Raticode's icon.")
        manufacturer.equals("samsung", ignoreCase = true) -> AssistantSteps(
            "In Settings › Apps › Choose default apps, tap Digital assistant app, then Digital assistant app again, and pick Raticode.",
            "Open Settings › Advanced features › Side button (or Side key). If Press and hold offers Digital assistant, choose it. Older Galaxy phones offer only Bixby or the power-off menu; " +
                "use the assist gesture, the Talk to Rem tile or the shortcut on Raticode's icon instead.")
        manufacturer.equals("google", ignoreCase = true) -> AssistantSteps(
            "In Settings › Apps › Default apps › Digital assistant app, tap Default digital assistant app and pick Raticode.",
            "Open Settings › System › Gestures › Press and hold power button, then choose Digital assistant. Pixel 6 and later do this by default; hold Power and Volume up for the power menu.")
        else -> AssistantSteps(
            "In Settings › Apps › Default apps › Digital assistant app (some phones call it Assist & voice input), tap the default assistant and pick Raticode.",
            "Look for Press and hold power button in Settings › System › Gestures or Buttons & gestures. Not every phone offers it; the assist gesture, the tile and the shortcut work everywhere.")
    }
}
