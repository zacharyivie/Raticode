package dev.raticode.mobile.live

import org.json.JSONObject

/**
 * The desktop's suspicious-request guard. When it stops a request from this phone it locks
 * the phone: every request is refused with an `unauthorized` answer whose detail starts with
 * [PREFIX], until the user chooses Unlock on the computer (Settings > Devices > Paired devices).
 * Older desktops have no guard and never send it. Older phones show the detail as an error.
 */
object PhoneLock {
    const val PREFIX = "This phone is locked."
    const val SHOWN = "Your computer locked this phone after a request from it looked suspicious. " +
        "Rem didn't see that request, and nothing from this phone reaches your computer until you unlock it there: " +
        "open Raticode on your computer, then Settings > Devices > Paired devices, and choose Unlock."

    /** True when [type]'s answer says the phone is locked, false when it shows the desktop took a request, else null. */
    fun state(type: String, payload: JSONObject): Boolean? = when {
        (type == "error" || type == "desktop.result") && payload.optString("code") == "unauthorized" &&
            payload.optString("detail").startsWith(PREFIX) -> true
        type == "desktop.result" && payload.optBoolean("ok") -> false
        type == "sync.snapshot" || type == "chat.accepted" -> false
        else -> null
    }
}
