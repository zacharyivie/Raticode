package dev.raticode.mobile.childlock

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.addCallback
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.raticode.mobile.ui.ChildLockScreen
import dev.raticode.mobile.ui.RaticodeTheme

/**
 * A see-through screen above everything Raticode shows. Being its own window on top of the app, it also covers pop-ups,
 * sheets, full-screen video and the browser's own dialogs, and takes the keyboard and accessibility focus; the app
 * underneath only pauses, so browser video and sound keep playing. It leaves only once [ChildLock.locked] is false.
 */
@RequiresApi(29)
class ChildLockActivity : ComponentActivity() {
    private val lock by lazy { ChildLockRuntime.get(this) }
    private var notice by mutableStateOf<UnlockOutcome?>(null)
    private var asking by mutableStateOf(false)
    private var counted = false
    private val confirmation: ActivityResultLauncher<Intent> = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { prompt.confirmationFinished(it.resultCode) }
    private val prompt: ScreenLockPrompt = ScreenLockPrompt(this) { confirmation.launch(it) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!lock.locked) { finish(); return }
        ChildLockRuntime.lockScreens++
        counted = true
        enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        // Back does nothing while locked.
        onBackPressedDispatcher.addCallback(this) { }
        setContent { RaticodeTheme { ChildLockScreen(notice, asking, onUnlock = ::askForScreenLock, onOpenSettings = ::openSecuritySettings) } }
    }

    override fun onResume() {
        super.onResume()
        if (!lock.locked) finish()
    }

    override fun onDestroy() {
        prompt.cancel()
        if (counted) { counted = false; ChildLockRuntime.lockScreens-- }
        super.onDestroy()
    }

    private fun askForScreenLock() {
        if (asking) return
        if (!ChildLockRuntime.hasScreenLock(this)) { notice = UnlockOutcome.NO_SCREEN_LOCK; return }
        notice = null
        asking = true
        // The screen lock only: not a fingerprint or face, which could belong to someone else who is enrolled.
        prompt.ask("Turn off child lock", "Use your phone's screen lock", ScreenLockProof.SCREEN_LOCK_ONLY, ::finished)
    }

    private fun finished(outcome: UnlockOutcome) {
        asking = false
        notice = lock.finishUnlock(outcome)
        if (!lock.locked) finish()
    }

    private fun openSecuritySettings() {
        try { startActivity(Intent(Settings.ACTION_SECURITY_SETTINGS)) }
        catch (_: ActivityNotFoundException) { notice = UnlockOutcome.UNAVAILABLE }
    }

    companion object {
        /** Covers Raticode with the lock. Safe to call when it already shows. */
        fun show(context: Context) {
            context.startActivity(Intent(context, ChildLockActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NO_ANIMATION))
        }
    }
}
