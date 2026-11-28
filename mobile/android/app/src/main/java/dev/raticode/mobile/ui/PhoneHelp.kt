package dev.raticode.mobile.ui

import java.net.URLEncoder

/**
 * The Help row on the More page. Report a problem opens a new GitHub issue like the desktop's
 * Help › Report a Problem: a blank "What happened" and only the app and Android versions,
 * never paths, project names or settings.
 */
internal object PhoneHelp {
    const val DOCUMENTATION_URL = "https://raticode.dev/docs/mobile/"
    private const val ISSUE_URL = "https://github.com/zacharyivie/Raticode/issues/new"

    data class Link(val label: String, val url: String)

    private fun details(appVersion: String, androidRelease: String, sdk: Int) =
        "Raticode Mobile: $appVersion\nAndroid: $androidRelease (API $sdk)"

    private fun reportProblemUrl(appVersion: String, androidRelease: String, sdk: Int): String {
        val body = listOf("### What happened", "", "", "", "### Raticode details", "", "```text", details(appVersion, androidRelease, sdk), "```")
            .joinToString("\n")
        // encodeURIComponent's spaces, as the desktop sends them.
        return "$ISSUE_URL?body=" + URLEncoder.encode(body, "UTF-8").replace("+", "%20")
    }

    fun links(appVersion: String, androidRelease: String, sdk: Int) = listOf(
        Link("Documentation", DOCUMENTATION_URL),
        Link("Report a problem", reportProblemUrl(appVersion, androidRelease, sdk)),
    )
}
