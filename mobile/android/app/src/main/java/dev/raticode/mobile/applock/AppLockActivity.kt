package dev.raticode.mobile.applock

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.os.Build
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
import dev.raticode.mobile.childlock.ChildLockRuntime
import dev.raticode.mobile.childlock.ScreenLockPrompt
import dev.raticode.mobile.childlock.ScreenLockProof
import dev.raticode.mobile.childlock.UnlockOutcome
import dev.raticode.mobile.share.ShareActivity
import dev.raticode.mobile.ui.AppLockScreen
import dev.raticode.mobile.ui.RaticodeTheme

/**
 * Covers Raticode until Android confirms the phone's owner. Being its own window above the app, it also covers dialogs,
 * sheets and full-screen video. It draws an opaque page on a see-through window, so nothing underneath shows but the
 * screens below stay started: the connection, Rem's alerts and the outbox carry on as if the app were open.
 */
@RequiresApi(29)
class AppLockActivity : ComponentActivity() {
    private val lock by lazy { AppLockRuntime.get(this) }
    private var notice by mutableStateOf<UnlockOutcome?>(null)
    private var asking by mutableStateOf(false)
    private var counted = false
    /** Android was already asked for this visit, so a cancelled prompt doesn't come straight back. */
    private var offered = false
    private val confirmation: ActivityResultLauncher<Intent> = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { prompt.confirmationFinished(it.resultCode) }
    private val prompt: ScreenLockPrompt = ScreenLockPrompt(this) { confirmation.launch(it) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!lock.locked) { finish(); return }
        lock.lockScreens++
        counted = true
        enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        // Back leaves without unlocking, like Home.
        onBackPressedDispatcher.addCallback(this) {
            if (intent.getBooleanExtra(OVER_SHARE, false)) finishAffinity() else moveTaskToBack(true)
        }
        setContent { RaticodeTheme { AppLockScreen(notice?.appLockText(), asking, notice?.offerSettings == true, onUnlock = ::ask, onOpenSettings = ::openSecuritySettings) } }
    }

    override fun onResume() {
        super.onResume()
        // Unlocked from another Raticode window, such as the share sheet's.
        if (!lock.locked) finish()
    }

    // The window has to be showing before Android will put its prompt over it.
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && !offered && !isFinishing) { offered = true; ask() }
    }

    override fun onStop() {
        // Android 10's confirmation is another screen, which stops this one while the question is still open.
        if (Build.VERSION.SDK_INT >= 30 || !asking) offered = false
        // Android 11 and newer close their prompt when the app leaves; this makes sure of it.
        if (Build.VERSION.SDK_INT >= 30) prompt.cancel()
        super.onStop()
    }

    override fun onDestroy() {
        prompt.cancel()
        if (counted) { counted = false; lock.lockScreens-- }
        super.onDestroy()
    }

    private fun ask() {
        if (asking) return
        if (!ChildLockRuntime.hasScreenLock(this)) { notice = UnlockOutcome.NO_SCREEN_LOCK; return }
        notice = null
        asking = true
        lock.beginPrompt()
        prompt.ask("Unlock Raticode", "Use your fingerprint, face or screen lock", ScreenLockProof.ANY_PHONE_UNLOCK, ::finished)
    }

    private fun finished(outcome: UnlockOutcome) {
        asking = false
        lock.endPrompt()
        notice = lock.finishUnlock(outcome)
        if (lock.locked) return
        AppLockRuntime.takeHeldOpen()?.let(::startActivity)
        finish()
    }

    private fun openSecuritySettings() {
        try { startActivity(Intent(Settings.ACTION_SECURITY_SETTINGS)) }
        catch (_: ActivityNotFoundException) { notice = UnlockOutcome.UNAVAILABLE }
    }

    companion object {
        private const val OVER_SHARE = "over_share"
        /** Covers [context]'s Raticode screen with the lock. Safe to call when it already shows. */
        fun show(context: Context) {
            context.startActivity(Intent(context, AppLockActivity::class.java).putExtra(OVER_SHARE, context is ShareActivity)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NO_ANIMATION))
        }
    }
}
