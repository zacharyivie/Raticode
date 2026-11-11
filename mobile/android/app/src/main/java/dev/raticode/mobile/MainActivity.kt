package dev.raticode.mobile

import android.os.Bundle
import android.content.Intent
import android.os.Build
import android.view.MotionEvent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import dev.raticode.mobile.live.LiveNotifications
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.clearAndSetSemantics
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.childlock.ChildLockActivity
import dev.raticode.mobile.childlock.ChildLockRuntime
import dev.raticode.mobile.ui.AppLockCover
import dev.raticode.mobile.ui.ChildLockControl
import dev.raticode.mobile.ui.ChildLockRefusal
import dev.raticode.mobile.ui.RaticodeApp
import dev.raticode.mobile.ui.RaticodeTheme

class MainActivity : ComponentActivity() {
    private var notificationThread by mutableStateOf<String?>(null)
    /** True for a launch from the app icon; cleared once the voice setup offer is settled. */
    private var offerVoiceSetup by mutableStateOf(false)
    private val childLock by lazy { ChildLockRuntime.get(this) }
    private val appLock by lazy { AppLockRuntime.get(this) }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // A notification tapped while locked doesn't change the page underneath.
        if (childLock.locked) return
        setIntent(intent)
        notificationThread = intent.getStringExtra(LiveNotifications.THREAD_EXTRA)
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (childLock.locked) intent.removeExtra(LiveNotifications.THREAD_EXTRA)
        notificationThread = intent.getStringExtra(LiveNotifications.THREAD_EXTRA)
        offerVoiceSetup = !childLock.locked && (savedInstanceState?.getBoolean(VOICE_OFFER) ?: (intent.action == Intent.ACTION_MAIN && notificationThread == null))
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )
        setContent { RaticodeTheme {
            val appLocked = appLock.locked
            val keepOutOfRecents = appLock.enabled
            // With a lock on, the recent apps preview mustn't show what the lock hides.
            LaunchedEffect(keepOutOfRecents) { if (Build.VERSION.SDK_INT >= 33) setRecentsScreenshotEnabled(!keepOutOfRecents) }
            Box(Modifier.fillMaxSize()) {
                Box(Modifier.fillMaxSize().then(if (appLocked) Modifier.clearAndSetSemantics { } else Modifier)) {
                    RaticodeApp(notificationThread = notificationThread, consumeNotification = {
                        notificationThread = null
                        intent.removeExtra(LiveNotifications.THREAD_EXTRA)
                        intent.data = null
                    }, offerVoiceSetup = offerVoiceSetup, consumeVoiceOffer = { offerVoiceSetup = false })
                    if (Build.VERSION.SDK_INT >= 29) {
                        ChildLockControl(Modifier.align(Alignment.CenterEnd))
                        ChildLockRefusal()
                    }
                }
                if (appLocked) AppLockCover()
            }
        } }
    }
    /**
     * Every way back to this screen while locked, such as a new launch or a notification, ends under the lock again. An app
     * lock goes first; this screen resumes again once it is passed.
     */
    override fun onResume() {
        super.onResume()
        if (childLock.locked && !appLock.locked && Build.VERSION.SDK_INT >= 29) ChildLockActivity.show(this)
    }
    /** Closes the moment between this screen appearing and a lock covering it. */
    override fun dispatchTouchEvent(ev: MotionEvent): Boolean = childLock.locked || appLock.locked || super.dispatchTouchEvent(ev)
    /**
     * Raticode left the screen: browser pages stop, wherever in Raticode the user was, except a page playing sound,
     * which keeps playing with a notification (BrowserPlaybackService). Moving between Raticode's own pages leaves
     * them running, so a page's music goes on (see BrowserPage).
     */
    override fun onStop() {
        dev.raticode.mobile.browser.BrowserRuntime.peek()?.appInFront(false)
        dev.raticode.mobile.browser.GeckoBrowser.peek()?.appInFront(false)
        super.onStop()
    }
    override fun onStart() {
        super.onStart()
        dev.raticode.mobile.browser.BrowserRuntime.peek()?.appInFront(true)
        dev.raticode.mobile.browser.GeckoBrowser.peek()?.appInFront(true)
    }
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(VOICE_OFFER, offerVoiceSetup)
    }
    private companion object { const val VOICE_OFFER = "voice_setup_offer" }
}
