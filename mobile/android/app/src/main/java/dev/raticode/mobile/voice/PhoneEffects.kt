package dev.raticode.mobile.voice

import android.Manifest
import android.app.Activity
import android.app.PendingIntent
import android.app.role.RoleManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.net.Uri
import android.os.BatteryManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.telecom.TelecomManager
import android.telephony.SmsManager
import android.telephony.TelephonyManager
import android.view.KeyEvent
import androidx.annotation.RequiresApi
import androidx.core.content.ContextCompat
import dev.raticode.mobile.live.PhoneIntentSpec
import java.util.concurrent.atomic.AtomicInteger

/**
 * What phone commands do on this phone. Calls go through Android's Telecom service and texts through its SMS manager,
 * each only after the user's own spoken command, on an unlocked phone, with the permission the user granted; they
 * never run from text Rem wrote, a shared screen or a notification. Numbers and messages are never logged or sent to
 * the desktop.
 */
@RequiresApi(29)
class PhoneEffects(private val context: Context, private val isAssistant: () -> Boolean = { holdsAssistantRole(context) }) {
    fun canCall(): Boolean = granted(Manifest.permission.CALL_PHONE)

    /**
     * Google Play lets an app send texts itself only while it is the phone's default assistant, and it must stop as
     * soon as it isn't. Otherwise a dictated text opens as a draft in the messaging app.
     */
    fun mayText(): Boolean = isAssistant()
    fun canText(): Boolean = mayText() && granted(Manifest.permission.SEND_SMS)
    private fun granted(permission: String) = ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED

    fun hasTelephony(): Boolean = context.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    /** Emergency numbers are never called directly; the dialer opens for the user instead. */
    fun isEmergency(number: String): Boolean =
        runCatching { context.getSystemService(TelephonyManager::class.java)?.isEmergencyNumber(number) == true }.getOrDefault(false)

    /** Places the call with the phone's own dialer. Returns a problem sentence, or null once Android took the call. */
    fun placeCall(number: String): String? = try {
        val telecom = context.getSystemService(TelecomManager::class.java) ?: return NO_PHONE
        telecom.placeCall(Uri.fromParts("tel", number, null), Bundle())
        null
    } catch (_: SecurityException) { CALL_OFF } catch (_: Exception) { NO_PHONE }

    /**
     * Sends the text, in parts when it is long, and reports once the phone's radio has accepted every part, or why
     * not. The system's messaging app records it like any sent text.
     */
    fun sendText(number: String, body: String, done: (String?) -> Unit) {
        val sms = runCatching { context.getSystemService(SmsManager::class.java) }.getOrNull()
        if (sms == null) { done(NO_SMS); return }
        val parts = sms.divideMessage(body)
        val id = next.incrementAndGet()
        val action = "dev.raticode.mobile.SMS_SENT.$id"
        val main = Handler(Looper.getMainLooper())
        var left = parts.size
        var problem: String? = null
        var finished = false
        lateinit var receiver: BroadcastReceiver
        val finish = { result: String? ->
            if (!finished) { finished = true; runCatching { context.unregisterReceiver(receiver) }; done(result) }
        }
        receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, intent: Intent) {
                if (resultCode != Activity.RESULT_OK && problem == null) problem = smsProblem(resultCode)
                if (--left <= 0) finish(problem)
            }
        }
        ContextCompat.registerReceiver(context, receiver, IntentFilter(action), ContextCompat.RECEIVER_NOT_EXPORTED)
        val sent = ArrayList(parts.indices.map { i ->
            PendingIntent.getBroadcast(context, id * 64 + i, Intent(action).setPackage(context.packageName), PendingIntent.FLAG_IMMUTABLE)
        })
        try {
            if (parts.size == 1) sms.sendTextMessage(number, null, body, sent[0], null)
            else sms.sendMultipartTextMessage(number, null, parts, sent, null)
        } catch (_: SecurityException) { finish(TEXT_OFF); return } catch (_: Exception) { finish(NO_SMS); return }
        main.postDelayed({ finish(SLOW) }, SEND_WAIT_MS)
    }

    fun flashlight(on: Boolean): String? {
        val camera = context.getSystemService(CameraManager::class.java) ?: return NO_FLASH
        return try {
            val id = camera.cameraIdList.firstOrNull { camera.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true }
                ?: return NO_FLASH
            camera.setTorchMode(id, on); null
        } catch (_: Exception) { "The flashlight is busy, perhaps with the camera." }
    }

    /** Presses a media key for whichever app is playing, as a headset button would. */
    fun media(key: MediaKey) {
        val code = when (key) {
            MediaKey.PLAY -> KeyEvent.KEYCODE_MEDIA_PLAY
            MediaKey.PAUSE -> KeyEvent.KEYCODE_MEDIA_PAUSE
            MediaKey.NEXT -> KeyEvent.KEYCODE_MEDIA_NEXT
            MediaKey.PREVIOUS -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
        }
        val audio = context.getSystemService(AudioManager::class.java) ?: return
        val time = SystemClock.uptimeMillis()
        audio.dispatchMediaKeyEvent(KeyEvent(time, time, KeyEvent.ACTION_DOWN, code, 0))
        audio.dispatchMediaKeyEvent(KeyEvent(time, time, KeyEvent.ACTION_UP, code, 0))
    }

    /** Changes the media volume and shows Android's volume bar. Returns the new level in percent. */
    fun volume(change: VolumeChange, percent: Int): Int? {
        val audio = context.getSystemService(AudioManager::class.java) ?: return null
        val stream = AudioManager.STREAM_MUSIC
        return try {
            when (change) {
                VolumeChange.UP -> audio.adjustStreamVolume(stream, AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI)
                VolumeChange.DOWN -> audio.adjustStreamVolume(stream, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI)
                VolumeChange.MUTE -> audio.adjustStreamVolume(stream, AudioManager.ADJUST_MUTE, AudioManager.FLAG_SHOW_UI)
                VolumeChange.UNMUTE -> audio.adjustStreamVolume(stream, AudioManager.ADJUST_UNMUTE, AudioManager.FLAG_SHOW_UI)
                VolumeChange.SET -> audio.setStreamVolume(stream, (audio.getStreamMaxVolume(stream) * percent + 50) / 100, AudioManager.FLAG_SHOW_UI)
            }
            audio.getStreamVolume(stream) * 100 / audio.getStreamMaxVolume(stream).coerceAtLeast(1)
        } catch (_: SecurityException) { null }
    }

    fun battery(): Pair<Int, Boolean>? {
        val manager = context.getSystemService(BatteryManager::class.java) ?: return null
        val level = manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY).takeIf { it in 0..100 } ?: return null
        return level to manager.isCharging
    }

    /** The launcher screen of the installed app the user named, or null when no app has that name. */
    fun app(name: String): Pair<String, PhoneIntentSpec>? {
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = context.packageManager.queryIntentActivities(launcher, 0)
        val hit = apps.firstOrNull { CommandPlan.appMatches(it.loadLabel(context.packageManager).toString(), name) } ?: return null
        val label = hit.loadLabel(context.packageManager).toString()
        return label to PhoneIntentSpec(Intent.ACTION_MAIN, null, emptyMap(), pkg = hit.activityInfo.packageName)
    }

    private fun smsProblem(code: Int): String = when (code) {
        SmsManager.RESULT_ERROR_NO_SERVICE, SmsManager.RESULT_ERROR_RADIO_OFF -> "The phone has no mobile service right now, so the text didn't go."
        SmsManager.RESULT_ERROR_LIMIT_EXCEEDED -> "Android is holding back texts for a moment. Try again shortly."
        else -> "The text didn't go through (error $code)."
    }

    companion object {
        fun holdsAssistantRole(context: Context): Boolean = runCatching {
            context.getSystemService(RoleManager::class.java)?.isRoleHeld(RoleManager.ROLE_ASSISTANT) == true
        }.getOrDefault(false)

        private val next = AtomicInteger()
        const val SEND_WAIT_MS = 30_000L
        const val CALL_OFF = "Calling is off for Raticode in Android settings."
        const val TEXT_OFF = "Texting is off for Raticode in Android settings."
        const val NO_PHONE = "This phone can't place calls."
        const val NO_SMS = "This phone can't send texts."
        const val NO_FLASH = "This phone has no flashlight."
        const val SLOW = "Couldn't confirm the text was sent. Check your messaging app before sending it again."
    }
}
