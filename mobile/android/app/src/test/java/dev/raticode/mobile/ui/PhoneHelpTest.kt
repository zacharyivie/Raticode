package dev.raticode.mobile.ui

import java.net.URI
import java.net.URLDecoder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The More page's Help row: the phone guides, and an issue that carries only the versions. */
class PhoneHelpTest {
    @Test fun helpRowLinksToThePhoneGuidesAndAnIssueWithThisAppsVersion() {
        val links = PhoneHelp.links("0.34.0", "15", 35)
        assertEquals(listOf("Documentation", "Report a problem"), links.map { it.label })
        assertEquals("https://raticode.dev/docs/mobile/", links[0].url)

        val report = URI(links[1].url)
        assertEquals("https://github.com/zacharyivie/Raticode/issues/new", "${report.scheme}://${report.host}${report.path}")
        // Spaces are %20, as the desktop's encodeURIComponent sends them, never "+".
        assertFalse(report.rawQuery.contains('+'))
        val body = URLDecoder.decode(report.rawQuery.removePrefix("body="), "UTF-8")
        assertTrue(body, body.startsWith("### What happened\n\n\n\n### Raticode details"))
        assertTrue(body, body.contains("Raticode Mobile: 0.34.0\nAndroid: 15 (API 35)"))
    }
}
