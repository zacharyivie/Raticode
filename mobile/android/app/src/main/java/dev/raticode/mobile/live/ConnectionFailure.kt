package dev.raticode.mobile.live

import java.io.IOException
import java.net.SocketTimeoutException
import java.net.ConnectException
import java.net.NoRouteToHostException
import javax.net.ssl.SSLException

/** Only fixed diagnostics reach UI; exception messages may contain private transport data. */
internal class ConnectionFailure(val guidance: String) : IOException(guidance)

internal enum class ConnectionStage { CONNECTING, AUTHENTICATION, BINDING, CONFIRMATION, READY }

internal const val NO_ADDRESS_GUIDANCE = "No desktop Wi-Fi address is saved. Enter the address and port shown in desktop Paired devices."

internal fun connectionGuidance(stage: ConnectionStage, failure: Exception, path: DevicePath = DevicePath.WIFI): String =
    if (path == DevicePath.TAILSCALE) tailscaleGuidance(stage, failure) else when {
    failure is ConnectionFailure -> failure.guidance
    failure is SSLException -> "Desktop TLS authentication failed over Wi-Fi. Check both devices' clocks and scan a fresh invitation."
    stage == ConnectionStage.CONNECTING && failure is SocketTimeoutException ->
        "Could not reach the desktop over Wi-Fi. Check that both devices use the same Wi-Fi network, that the computer's firewall allows the device port shown in Paired devices, and that the network does not isolate devices. Pairing has not reached desktop."
    failure is ConnectException || failure is NoRouteToHostException ->
        "Desktop address could not be reached over Wi-Fi. Check the address and port in desktop Paired devices, the computer's firewall, and whether Wi-Fi isolates devices."
    stage == ConnectionStage.CONFIRMATION -> "Desktop confirmation did not finish. Check Paired devices on desktop and confirm this phone's identity."
    failure is SocketTimeoutException && stage == ConnectionStage.AUTHENTICATION ->
        "Connected over Wi-Fi, but desktop TLS authentication timed out. Check the device port in Paired devices and that desktop device networking is responding."
    failure is SocketTimeoutException -> "Desktop pairing response timed out. Check Paired devices on desktop and use a fresh invitation if it expired."
    failure is IOException -> "The Wi-Fi connection to desktop was interrupted. Check that Raticode is running and both devices are on the same Wi-Fi."
    else -> "Desktop rejected or could not complete pairing. Check Paired devices and scan a fresh invitation."
}

private fun tailscaleGuidance(stage: ConnectionStage, failure: Exception): String = when {
    failure is ConnectionFailure -> failure.guidance
    failure is SSLException -> "Desktop TLS authentication failed over Tailscale. The address may belong to another device; check the desktop's Tailscale address and both devices' clocks."
    stage == ConnectionStage.CONNECTING && failure is SocketTimeoutException ->
        "Could not reach the desktop through Tailscale. Check that Tailscale is on for both devices, that your Tailscale access rules let this phone reach the desktop's device port, and that Raticode Desktop listens on its Tailscale address."
    failure is ConnectException || failure is NoRouteToHostException ->
        "The desktop's Tailscale address could not be reached. Check the address and port, that Raticode Desktop is running and listens on its Tailscale address, and your Tailscale access rules."
    stage == ConnectionStage.CONFIRMATION -> "Desktop confirmation did not finish. Check Paired devices on desktop and confirm this phone's identity."
    failure is SocketTimeoutException && stage == ConnectionStage.AUTHENTICATION ->
        "Connected through Tailscale, but desktop TLS authentication timed out. Check the device port and that desktop device networking is responding."
    failure is SocketTimeoutException -> "Desktop pairing response timed out. Check Paired devices on desktop and use a fresh invitation if it expired."
    failure is IOException -> "The Tailscale connection to desktop was interrupted. Check that Tailscale is on for both devices and Raticode is running."
    else -> "Desktop rejected or could not complete pairing. Check Paired devices and scan a fresh invitation."
}


/** Why saved pairing data could not be opened. Only a missing key or damaged data needs a reset. */
internal fun loadFailureGuidance(failure: Exception): String = when (failure) {
    is java.io.IOException -> "Raticode couldn't read or save its protected data on this phone. Free some storage, then close and reopen Raticode. Your pairing is kept."
    else -> "Protected device data is locked. Reset pairing to recover."
}

/** Network and disk I/O failures reconnect through the sync loop, and interruption stops it. */
internal fun reachesSyncLoop(failure: Exception) = failure is IOException || failure is InterruptedException

/** A command failed on this phone, not on the network; the connection and queued messages are kept. */
internal fun commandFailureGuidance(failure: Exception): String = when (failure) {
    is java.security.GeneralSecurityException -> "Raticode couldn't use its protected storage on this phone. Close and reopen Raticode; your queued messages are kept."
    else -> "That didn't work on this phone. Try again; your draft and queued messages are kept."
}
