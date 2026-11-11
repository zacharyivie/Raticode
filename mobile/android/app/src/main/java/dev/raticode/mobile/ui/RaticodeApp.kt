package dev.raticode.mobile.ui

import android.os.Build
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Production entry point. Simulator state is never loaded into live conversations. */
@Composable
fun RaticodeApp(notificationThread: String? = null, consumeNotification: () -> Unit = {}, offerVoiceSetup: Boolean = false, consumeVoiceOffer: () -> Unit = {}) {
    if (Build.VERSION.SDK_INT >= 29) {
        LiveScreen(initialThread = notificationThread?.takeIf { it.startsWith("live:") }?.removePrefix("live:"),
            consumeInitialThread = consumeNotification, offerVoiceSetup = offerVoiceSetup, consumeVoiceOffer = consumeVoiceOffer)
    } else {
        Surface(Modifier.fillMaxSize()) {
            Column(Modifier.safeDrawingPadding().padding(24.dp)) {
                Text("Raticode Mobile", style = MaterialTheme.typography.headlineMedium)
                Text("Connect to Rem from a phone running Android 10 or newer.")
            }
        }
    }
}
