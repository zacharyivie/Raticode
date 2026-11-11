package dev.raticode.mobile.voice

import android.Manifest
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.core.app.ActivityCompat
import dev.raticode.mobile.MainActivity
import dev.raticode.mobile.childlock.closedByChildLock
import dev.raticode.mobile.live.LiveNotifications
import dev.raticode.mobile.live.PhoneAction
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.raticode.mobile.ui.AttachPicker
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.VoiceActions
import dev.raticode.mobile.ui.VoicePanel
import dev.raticode.mobile.ui.startPhoneAction
import dev.raticode.mobile.ui.startSpec
import dev.raticode.mobile.ui.finishPick
import dev.raticode.mobile.ui.NO_PICKER
import dev.raticode.mobile.live.PeopleStore
import dev.raticode.mobile.live.PickPhoneNumber
import dev.raticode.mobile.live.readPickedContact
import android.content.ActivityNotFoundException
import kotlin.concurrent.thread

/**
 * Voice mode as a screen: opened by the tile, the launcher shortcut, the sync notification, Settings, the assist
 * gesture on phones without the assistant overlay, and by the overlay itself to unlock or ask for the microphone.
 * It may show over the lock screen; sending still needs an unlock.
 */
@RequiresApi(29)
class VoiceActivity : ComponentActivity() {
    private val controller by lazy { VoiceRuntime.controller(application) }
    private var asked = false
    private val microphone = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) controller.listen() else controller.micDenied()
    }
    // The card the contact picker is choosing for; the turn is held meanwhile so the reply and its cards stay.
    private var pickingFor: Pair<PhoneAction, Boolean>? = null
    private val contactPicker = registerForActivityResult(PickPhoneNumber()) { uri ->
        val (card, keep) = pickingFor ?: return@registerForActivityResult
        pickingFor = null
        thread {
            val contact = uri?.let { readPickedContact(this, it) }
            runOnUiThread { finishPick(this, card, keep, contact, uri != null); controller.picked(card); controller.detach() }
        }
    }
    // A phone command's calling or texting permission; the turn is held while Android asks.
    private val commandPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        controller.permissionAnswered(granted); controller.detach()
    }
    private val actions = object : VoiceActions {
        override fun unlock() = whenUnlocked { controller.unlocked() }
        override fun allowMic() {
            // After a permanent denial Android no longer asks, so open the app's settings page instead.
            if (asked && !ActivityCompat.shouldShowRequestPermissionRationale(this@VoiceActivity, Manifest.permission.RECORD_AUDIO))
                whenUnlocked { startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))) }
            else { asked = true; microphone.launch(Manifest.permission.RECORD_AUDIO) }
        }
        override fun openRem() = whenUnlocked {
            startActivity(Intent(this@VoiceActivity, MainActivity::class.java).putExtra(LiveNotifications.THREAD_EXTRA, "live:rem")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
            finish()
        }
        // The other app opens in its own task, since this screen's is hidden from Recents; voice mode is done then.
        override fun openPhoneAction(action: PhoneAction, report: (String?) -> Unit) = whenUnlocked {
            val problem = startPhoneAction(this@VoiceActivity, action, newTask = true)
            if (problem == null) { controller.cancel(); finish() } else report(problem)
        }
        override fun requestPermission(permission: String) = whenUnlocked {
            controller.attach()
            commandPermission.launch(permission)
        }
        // Another app for a phone command opens in its own task; voice mode is done then.
        override fun launch(spec: dev.raticode.mobile.live.PhoneIntentSpec, report: (String?) -> Unit) = whenUnlocked {
            val problem = startSpec(this@VoiceActivity, spec, newTask = true)
            if (problem == null) { controller.cancel(); finish() } else report(problem)
        }
        // The picker shows the phone's contacts, so it needs an unlocked phone.
        override fun pickContact(action: PhoneAction, remember: Boolean) = whenUnlocked {
            if (pickingFor != null) return@whenUnlocked
            PeopleStore.notes.remove(action)
            pickingFor = action to remember; controller.attach()
            try { contactPicker.launch(Unit) } catch (_: ActivityNotFoundException) {
                pickingFor = null; controller.detach(); PeopleStore.notes[action] = NO_PICKER
            }
        }
        // Picking shows the phone's files and photos, so it needs an unlocked phone.
        override fun attachFiles() = whenUnlocked { picking = true }
        override fun close() { controller.cancel(); finish() }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (closedByChildLock()) return
        setShowWhenLocked(true)
        enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        setContent { RaticodeTheme {
            VoicePanel(controller, actions)
            // The same attach menu and pickers as Rem's chat; files upload as they are picked.
            AttachPicker(picking, { open ->
                picking = open
                // Closed without going to another app, such as backing out of the menu: listening carries on.
                if (!open) window.decorView.post { if (!away) controller.backFromFiles() }
            }, controller.attachRoom, controller::attach, { controller.attachNotice = it }, onLeave = ::leaveForFiles, onBack = ::backFromFiles)
        } }
        if (savedInstanceState == null) handle(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (closedByChildLock()) return
        setIntent(intent)
        handle(intent)
    }

    // Reached from the overlay over Rem's chat (to attach or unlock), this screen closes once the message is queued too.
    private val close: () -> Unit = { finish() }
    // Reopened after the lock was turned on.
    override fun onResume() { super.onResume(); closedByChildLock() }
    override fun onStart() { super.onStart(); controller.closeSurface = close; controller.attach() }
    override fun onStop() { if (controller.closeSurface === close) controller.closeSurface = null; controller.detach(); super.onStop() }

    /** Only the app's own launches carry a step. The assist alias is exported, so its extras are ignored. */
    private fun handle(intent: Intent) {
        val own = intent.component?.className == VoiceActivity::class.java.name
        val step = intent.getStringExtra(STEP).takeIf { own }
        if (step in setOf(STEP_UNLOCK, STEP_MIC, STEP_OPEN_REM, STEP_PHONE_ACTION, STEP_PICK_CONTACT, STEP_ATTACH, STEP_PERMISSION, STEP_LAUNCH)) pendingStep = step else controller.begin(fromOtherApp = !own)
    }

    // Android refuses to unlock for a screen that isn't showing yet, so steps wait until this window has focus.
    private var pendingStep: String? = null
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus) return
        when (pendingStep.also { pendingStep = null }) {
            STEP_UNLOCK -> actions.unlock()
            STEP_MIC -> actions.allowMic()
            STEP_OPEN_REM -> actions.openRem()
            STEP_ATTACH -> actions.attachFiles()
            STEP_PHONE_ACTION -> controller.tappedAction?.let { action ->
                controller.tappedAction = null
                actions.openPhoneAction(action) { Toast.makeText(this, it, Toast.LENGTH_LONG).show() }
            }
            STEP_PERMISSION -> controller.pendingPermission?.let { actions.requestPermission(it) }
            // The voice panel opens the app itself as the command's step shows; nothing more to do here.
            STEP_LAUNCH -> Unit
            STEP_PICK_CONTACT -> controller.tappedPick?.let { (action, keep) ->
                controller.tappedPick = null
                actions.pickContact(action, keep)
            }
            // Back from an unlock some other way, such as the lock screen itself: finish what Send started.
            else -> if (!getSystemService(KeyguardManager::class.java).isDeviceLocked) controller.unlockedElsewhere()
        }
    }

    private fun whenUnlocked(then: () -> Unit) {
        val keyguard = getSystemService(KeyguardManager::class.java)
        if (!keyguard.isKeyguardLocked) { then(); return }
        // Android stops this screen while the PIN or fingerprint prompt is up; keep the turn until the answer.
        holdTurn()
        requestUnlock(keyguard, then, retries = 3)
    }

    private fun requestUnlock(keyguard: KeyguardManager, then: () -> Unit, retries: Int) {
        keyguard.requestDismissKeyguard(this, object : KeyguardManager.KeyguardDismissCallback() {
            override fun onDismissSucceeded() { then(); releaseTurn() }
            override fun onDismissCancelled() { releaseTurn() }
            // Refused while the screen is still being shown; ask again shortly.
            override fun onDismissError() {
                if (retries > 0 && !isFinishing) window.decorView.postDelayed({ requestUnlock(keyguard, then, retries - 1) }, 400)
                else releaseTurn()
            }
        })
    }

    private var unlocking = false
    private val unlockTimeout = Runnable { releaseTurn() }
    private fun holdTurn() {
        if (unlocking) return
        unlocking = true; controller.attach()
        window.decorView.postDelayed(unlockTimeout, UNLOCK_WAIT_MS)
    }
    private fun releaseTurn() {
        if (!unlocking) return
        unlocking = false; window.decorView.removeCallbacks(unlockTimeout); controller.detach()
    }

    private var picking by mutableStateOf(false)
    /** Another app is picking files for this turn. The turn is held meanwhile, with listening paused. */
    private var away = false
    private fun leaveForFiles() {
        if (away) return
        away = true; controller.attach(); controller.leavingForFiles()
    }
    private fun backFromFiles() {
        if (!away) return
        away = false; controller.backFromFiles(); controller.detach()
    }

    override fun onDestroy() { releaseTurn(); if (away) { away = false; controller.detach() }; super.onDestroy() }

    companion object {
        const val ACTION = "dev.raticode.mobile.action.VOICE"
        private const val STEP = "voice_step"
        const val STEP_UNLOCK = "unlock"
        const val STEP_MIC = "microphone"
        const val STEP_OPEN_REM = "open_rem"
        const val STEP_PHONE_ACTION = "phone_action"
        const val STEP_PICK_CONTACT = "pick_contact"
        const val STEP_PERMISSION = "permission"
        const val STEP_LAUNCH = "launch"
        const val STEP_ATTACH = "attach"
        /** How long a turn waits for the user to unlock before it ends. */
        private const val UNLOCK_WAIT_MS = 60_000L
        fun intent(context: Context, step: String? = null): Intent = Intent(context, VoiceActivity::class.java).setAction(ACTION)
            .apply { step?.let { putExtra(STEP, it) } }
        /** Voice mode needs on-device speech recognition, which Android 12 added. */
        val supported get() = Build.VERSION.SDK_INT >= 31
    }
}
