package dev.raticode.mobile.voice

import android.app.KeyguardManager
import android.app.assist.AssistStructure
import android.content.Context
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.view.View
import androidx.annotation.RequiresApi
import androidx.compose.ui.platform.ComposeView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import java.lang.ref.WeakReference
import dev.raticode.mobile.childlock.ChildLockRuntime
import dev.raticode.mobile.live.PhoneAction
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.VoiceActions
import dev.raticode.mobile.ui.VoicePanel

/**
 * Makes Raticode selectable as the phone's digital assistant. Android binds it while Raticode holds that role, and
 * shows [RemVoiceSession] for the assist gesture or a power-button hold, even with the app closed.
 */
@RequiresApi(31)
class RemVoiceInteractionService : VoiceInteractionService() {
    override fun onReady() {
        super.onReady()
        running = WeakReference(this)
        applyScreenSetting()
    }

    override fun onShutdown() {
        if (running?.get() === this) running = null
        super.onShutdown()
    }

    /**
     * Screenshots are never requested. The screen's text is requested from Android only while the user has screen
     * sharing on; otherwise Android doesn't collect it at all.
     */
    private fun applyScreenSetting() {
        val text = if (VoicePrefs.screen(this)) 0 else VoiceInteractionSession.SHOW_WITH_ASSIST
        setDisabledShowContext(text or VoiceInteractionSession.SHOW_WITH_SCREENSHOT)
    }

    companion object {
        @Volatile private var running: WeakReference<RemVoiceInteractionService>? = null
        /** Applies a changed screen-sharing setting to the running assistant service, if Raticode is the assistant. */
        fun screenSettingChanged() { running?.get()?.applyScreenSetting() }
    }
}

@RequiresApi(31)
class RemVoiceSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = RemVoiceSession(this)
}

/**
 * The assistant overlay. Android draws it above other apps and above the lock screen. Steps that need an activity,
 * such as unlocking or the microphone prompt, continue in [VoiceActivity] with the same turn.
 */
@RequiresApi(31)
class RemVoiceSession(context: Context) : VoiceInteractionSession(context), LifecycleOwner, SavedStateRegistryOwner {
    private val registry = LifecycleRegistry(this)
    private val saved = SavedStateRegistryController.create(this)
    override val lifecycle: Lifecycle get() = registry
    override val savedStateRegistry: SavedStateRegistry get() = saved.savedStateRegistry
    private val controller by lazy { VoiceRuntime.controller(context.applicationContext as android.app.Application) }
    private var shown = false
    private val close: () -> Unit = { hide() }

    private val actions = object : VoiceActions {
        override fun unlock() = handOff(VoiceActivity.STEP_UNLOCK)
        override fun allowMic() = handOff(VoiceActivity.STEP_MIC)
        override fun openRem() = handOff(VoiceActivity.STEP_OPEN_REM)
        // The overlay can't start another app or ask for an unlock itself; the voice screen does both.
        override fun openPhoneAction(action: PhoneAction, report: (String?) -> Unit) {
            controller.tappedAction = action
            handOff(VoiceActivity.STEP_PHONE_ACTION)
        }
        // The overlay can't show the contact picker either; the voice screen does, after an unlock.
        override fun pickContact(action: PhoneAction, remember: Boolean) {
            controller.tappedPick = action to remember
            handOff(VoiceActivity.STEP_PICK_CONTACT)
        }
        // Permission prompts and other apps need an activity, so phone commands continue on the voice screen too.
        override fun requestPermission(permission: String) {
            controller.pendingPermission = permission
            handOff(VoiceActivity.STEP_PERMISSION)
        }
        // The voice screen shows the same step and opens the app once it is up.
        override fun launch(spec: dev.raticode.mobile.live.PhoneIntentSpec, report: (String?) -> Unit) = handOff(VoiceActivity.STEP_LAUNCH)
        // The overlay can't open pickers; the voice screen does, after an unlock, with listening paused meanwhile.
        override fun attachFiles() {
            controller.leavingForFiles(); controller.handOff()
            handOff(VoiceActivity.STEP_ATTACH)
        }
        override fun close() { controller.cancel(); hide() }
    }

    override fun onCreate() {
        super.onCreate()
        saved.performAttach()
        saved.performRestore(null)
        registry.currentState = Lifecycle.State.CREATED
    }

    override fun onCreateContentView(): View = ComposeView(context).apply {
        setViewTreeLifecycleOwner(this@RemVoiceSession)
        setViewTreeSavedStateRegistryOwner(this@RemVoiceSession)
        setContent { RaticodeTheme { VoicePanel(controller, actions) } }
    }

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        if (ChildLockRuntime.locked(context)) { ChildLockRuntime.toastBlocked(context); hide(); return }
        // Opened without the screen (a tile, the shortcut, sharing off): nothing from an earlier opening carries over.
        if (showFlags and SHOW_WITH_ASSIST == 0) controller.dropScreen()
        registry.currentState = Lifecycle.State.RESUMED
        if (!shown) { shown = true; controller.attach() }
        controller.closeSurface = close
        // Raticode's own Rem chat behind the overlay: once sent, the overlay steps aside for it.
        controller.begin(overRemChat = controller.client.remChatOnScreen)
    }

    override fun onHide() {
        if (controller.closeSurface === close) controller.closeSurface = null
        if (shown) { shown = false; controller.detach() }
        registry.currentState = Lifecycle.State.CREATED
        super.onHide()
    }

    override fun onDestroy() {
        if (shown) { shown = false; controller.detach() }
        registry.currentState = Lifecycle.State.DESTROYED
        super.onDestroy()
    }

    /**
     * Android's description of the app the user was looking at. Only the focused app is read, never one behind a lock
     * screen, never a part the app blocks from assistants, and never password or PIN fields. The words stay in memory
     * until the next message goes or the turn ends.
     */
    override fun onHandleAssist(state: AssistState) {
        if (!state.isFocused || !VoicePrefs.screen(context)) return
        if (context.getSystemService(KeyguardManager::class.java).isKeyguardLocked) { controller.screenUnavailable(ScreenText.LOCKED); return }
        val structure = state.assistStructure ?: run { controller.screenUnavailable(ScreenText.BLOCKED); return }
        val lines = mutableListOf<String>()
        val read = IntArray(1)
        var domain: String? = null
        for (i in 0 until structure.windowNodeCount) {
            collect(structure.getWindowNodeAt(i).rootViewNode, lines, read) { if (domain == null) domain = it }
        }
        domain?.let { lines.add(0, "Website: $it") }
        controller.screenSeen(appName(structure.activityComponent?.packageName), lines)
    }

    override fun onAssistStructureFailure(failure: Throwable) { controller.screenUnavailable(ScreenText.BLOCKED) }

    /** Walks the app's views in screen order. [read] counts characters so a huge screen stops early. */
    private fun collect(node: AssistStructure.ViewNode, out: MutableList<String>, read: IntArray, web: (String) -> Unit) {
        if (node.isAssistBlocked || node.visibility != View.VISIBLE || read[0] > ScreenText.MAX_CHARS * 2) return
        node.webDomain?.takeIf { it.isNotBlank() }?.let(web)
        if (!ScreenText.secret(node.inputType)) (node.text ?: node.contentDescription)?.toString()?.let { out.add(it); read[0] += it.length }
        for (i in 0 until node.childCount) collect(node.getChildAt(i), out, read, web)
    }

    private fun appName(packageName: String?): String = packageName?.let { name ->
        runCatching { context.packageManager.run { getApplicationLabel(getApplicationInfo(name, 0)).toString() } }.getOrNull()
    } ?: "an app"

    /** The voice screen takes over this turn; the overlay steps aside so it doesn't cover it. */
    private fun handOff(step: String) {
        startAssistantActivity(VoiceActivity.intent(context, step))
        hide()
    }
}
