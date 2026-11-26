package dev.raticode.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.ThreadAccounts
import kotlinx.coroutines.delay

/**
 * Signing an account in from this phone, for the Account menu and the Usage cards. [available] is false for a desktop
 * that doesn't take `provider.account.sign_in`; [states] holds the sign-ins this phone started, by
 * [ThreadAccounts.signInId]: read one with [state].
 */
internal data class PhoneSignIn(
    val available: Boolean,
    val states: Map<String, ThreadAccounts.SignIn>,
    val start: (provider: String, name: String) -> Unit,
    val cancel: (provider: String, name: String) -> Unit,
    val dismiss: (provider: String, name: String) -> Unit,
) {
    fun state(provider: String, name: String): ThreadAccounts.SignIn? = states[ThreadAccounts.signInId(provider, name)]
}

@RequiresApi(29)
internal fun phoneSignIn(client: LiveClient) = PhoneSignIn(client.accountSignInSync, client.accountSignIns,
    client::signInAccount, client::cancelAccountSignIn, client::dismissAccountSignIn)

/**
 * One account's sign-in: while the desktop starts it, the link to open and the one-time code to enter there; then
 * Signed in, or the provider's own reason it failed and, when device code sign-in is off, where to turn it on.
 */
@Composable
internal fun AccountSignInPanel(provider: String, name: String, signIn: ThreadAccounts.SignIn, actions: PhoneSignIn) {
    val context = LocalContext.current
    val scheme = MaterialTheme.colorScheme
    var copied by remember(signIn.code) { mutableStateOf(false) }
    LaunchedEffect(copied) { if (copied) { delay(2000); copied = false } }
    OutlinedCard(Modifier.fillMaxWidth().testTag("accountSignIn")) {
        Column(Modifier.padding(12.dp).semantics { liveRegion = LiveRegionMode.Polite }, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            when {
                signIn.waiting && signIn.code.isBlank() -> {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                        Text("Starting the sign-in for $name on your computer…", style = MaterialTheme.typography.bodyMedium)
                    }
                    TextButton(onClick = { actions.cancel(provider, name) }, Modifier.testTag("accountSignInCancel")) { Text("Cancel") }
                }
                signIn.waiting -> {
                    Text("Sign in $name", style = MaterialTheme.typography.titleSmall)
                    Text("1. Open the sign-in page and sign in to the account you want here.", style = MaterialTheme.typography.bodyMedium)
                    Button(onClick = { openInBrowser(context, signIn.url) }, enabled = signIn.url.isNotBlank(),
                        modifier = Modifier.testTag("accountSignInOpen")) { Text("Open sign-in page") }
                    Text(ThreadAccounts.codeStep(signIn), style = MaterialTheme.typography.bodyMedium)
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        SelectionContainer {
                            Text(signIn.code, style = MaterialTheme.typography.headlineSmall, fontFamily = FontFamily.Monospace,
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.testTag("accountSignInCode").semantics { contentDescription = "One-time code ${signIn.code.toList().joinToString(" ")}" })
                        }
                        OutlinedButton(onClick = {
                            val clip = ClipData.newPlainText("One-time code", signIn.code)
                            // A sign-in code: keep it out of the clipboard preview and keyboard suggestions.
                            clip.description.extras = android.os.PersistableBundle().apply {
                                putBoolean(if (android.os.Build.VERSION.SDK_INT >= 33) android.content.ClipDescription.EXTRA_IS_SENSITIVE else "android.content.extra.IS_SENSITIVE", true)
                            }
                            context.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(clip)
                            copied = true
                        }, modifier = Modifier.testTag("accountSignInCopy")) { Text(if (copied) "Copied" else "Copy") }
                    }
                    ThreadAccounts.expires(signIn).takeIf { it.isNotBlank() }?.let {
                        Text("$it. Only enter it on the page you opened from here.", style = MaterialTheme.typography.bodySmall, color = scheme.onSurfaceVariant)
                    }
                    if (signIn.help.isNotBlank()) Text("If the page says device code sign-in is off: ${signIn.help}",
                        style = MaterialTheme.typography.bodySmall, color = scheme.onSurfaceVariant)
                    TextButton(onClick = { actions.cancel(provider, name) }, Modifier.testTag("accountSignInCancel")) { Text("Cancel") }
                }
                signIn.done -> {
                    Text("$name is signed in.", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.testTag("accountSignInDone"))
                    TextButton(onClick = { actions.dismiss(provider, name) }) { Text("Done") }
                }
                else -> {
                    Text(signIn.reason.ifBlank { "The sign-in didn't finish." }, style = MaterialTheme.typography.bodyMedium,
                        color = scheme.error, modifier = Modifier.testTag("accountSignInFailed"))
                    if (signIn.refused && signIn.help.isNotBlank()) Text(signIn.help, style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.testTag("accountSignInRefusedHelp"))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { actions.start(provider, name) }, Modifier.testTag("accountSignInRetry")) { Text("Try again") }
                        TextButton(onClick = { actions.dismiss(provider, name) }) { Text("Close") }
                    }
                }
            }
        }
    }
}
