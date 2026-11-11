package dev.raticode.mobile.voice

import android.content.Context
import androidx.core.content.edit

/** Voice mode choices. Only switches are stored here; nothing anyone said or heard is saved. */
object VoicePrefs {
    private fun prefs(context: Context) = context.getSharedPreferences("voice", Context.MODE_PRIVATE)
    /** Read Rem's replies aloud. On by default. */
    fun speak(context: Context) = prefs(context).getBoolean("speak", true)
    fun setSpeak(context: Context, on: Boolean) = prefs(context).edit { putBoolean("speak", on) }
    /** Send a few seconds after Done, instead of waiting for a tap on Send. Off by default. */
    /** Show and read Rem's replies while the phone is locked. Off by default; sending always needs an unlock. */
    fun whileLocked(context: Context) = prefs(context).getBoolean("while_locked", false)
    fun setWhileLocked(context: Context, on: Boolean) = prefs(context).edit { putBoolean("while_locked", on) }
    /**
     * Let Rem read the text on screen when opened over another app. Off by default; turning it on shows what is shared
     * and where it goes. The text goes only with the next message.
     */
    fun screen(context: Context) = prefs(context).getBoolean("screen", false)
    fun setScreen(context: Context, on: Boolean) = prefs(context).edit { putBoolean("screen", on) }
    /** Offer assistant setup each time Raticode opens, until Raticode is chosen or the user says not to ask again. */
    fun offerSetup(context: Context) = prefs(context).getBoolean("offer_setup", true)
    fun setOfferSetup(context: Context, on: Boolean) = prefs(context).edit { putBoolean("offer_setup", on) }
}
