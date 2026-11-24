package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** The thread picker's Account menu: Rem's setting, the default sign-in, accounts and the ones that need sign-in. */
class ThreadAccountsTest {
    private val answer = JSONObject("""{"providers":[
        {"provider":"claude_code","label":"Claude Code","rem_setting":"auto","rem_setting_label":"Choose by usage","choice":"work",
         "accounts":[{"name":"work","state":"signed_in","needs_sign_in":false,"week_used":42.4,"five_hour_used":9.6,"observed_at":"2026-11-17T10:00:00+00:00"},
                     {"name":"old","state":"needs_sign_in","needs_sign_in":true}]},
        {"provider":"codex","label":"Codex","rem_setting":"","rem_setting_label":"Default sign-in","choice":"","accounts":[]}]}""")

    @Test fun parsesMenusForTheThreadTheReadNamed() {
        val view = ThreadAccounts.parse(ThreadAccounts.key("thread-1"), answer)
        assertEquals("thread-1", view.thread)
        // A provider without accounts gets no menu.
        assertEquals(setOf("claude_code"), view.menus.keys)
        val menu = view.menus.getValue("claude_code")
        assertEquals("work", menu.choice)
        assertEquals(42.4, menu.accounts.first().weekUsed!!, 0.001)
        assertNull(ThreadAccounts.parse(ThreadAccounts.key(null), answer).thread)
        assertFalse(ThreadAccounts.params(null).has("thread_id"))
        assertEquals("thread-1", ThreadAccounts.params("thread-1").getString("thread_id"))
    }

    @Test fun accountsThatNeedSignInAreShownButCannotBePicked() {
        val menu = ThreadAccounts.parse(ThreadAccounts.key(null), answer).menus.getValue("claude_code")
        val options = ThreadAccounts.options(menu, "")
        assertEquals(listOf("", ThreadAccounts.DEFAULT_SIGN_IN, "work", "old"), options.map { it.id })
        assertEquals("Choose by usage", options[0].meta)
        assertEquals("Signed in · Week 42% · 5h 10% used", options[2].meta)
        assertTrue(options[2].enabled)
        assertFalse(options[3].enabled)
        assertTrue(options[3].needsSignIn)
        assertEquals("Needs sign-in", options[3].meta)
        // A removed account stays visible as the current choice, but can't be picked again.
        assertFalse(ThreadAccounts.options(menu, "gone").last().enabled)
    }

    @Test fun labelsAndSessionChanges() {
        val menu = ThreadAccounts.parse(ThreadAccounts.key(null), answer).menus.getValue("claude_code")
        assertEquals("Rem's setting · Choose by usage", ThreadAccounts.label(menu, ""))
        assertEquals("Default sign-in", ThreadAccounts.label(menu, ThreadAccounts.DEFAULT_SIGN_IN))
        assertEquals("work", ThreadAccounts.label(menu, "work"))
        assertEquals("auto", ThreadAccounts.effective(menu, ""))
        assertEquals("", ThreadAccounts.effective(menu, ThreadAccounts.DEFAULT_SIGN_IN))
        assertTrue(ThreadAccounts.warning("Claude Code", "work").startsWith("Your next message starts a new Claude Code session on work"))
    }

    @Test fun aCodexAccountCarriesItsDeviceSignInLinkAndCode() {
        val view = ThreadAccounts.parse(ThreadAccounts.key(null), JSONObject("""{"providers":[
            {"provider":"codex","label":"Codex","rem_setting":"","choice":"","accounts":[
             {"name":"work","state":"needs_sign_in","needs_sign_in":true,"phone_sign_in":true,
              "sign_in":{"state":"waiting","url":"https://auth.openai.com/codex/device","code":"WXYZ-12345","reason":"",
                         "help":"Turn on device code sign-in for Codex in ChatGPT: Settings › Security","refused":false,"seconds_left":840}},
             {"name":"odd","state":"needs_sign_in","needs_sign_in":true,"phone_sign_in":true,
              "sign_in":{"state":"waiting","url":"http://example.com/","code":"","seconds_left":900}},
             {"name":"plain","state":"signed_in","needs_sign_in":false}]}]}"""))
        val accounts = view.menus.getValue("codex").accounts.associateBy { it.name }
        val work = accounts.getValue("work")
        assertTrue(work.phoneSignIn)
        assertEquals("https://auth.openai.com/codex/device", work.signIn!!.url)
        assertEquals("WXYZ-12345", work.signIn!!.code)
        assertTrue(work.signIn!!.waiting)
        assertEquals("Expires in 14 min", ThreadAccounts.expires(work.signIn!!))
        // Only an https link is kept, and no code means none to show yet.
        assertEquals("", accounts.getValue("odd").signIn!!.url)
        assertEquals("", ThreadAccounts.expires(accounts.getValue("odd").signIn!!))
        assertFalse(accounts.getValue("plain").phoneSignIn)
        assertNull(accounts.getValue("plain").signIn)
        assertEquals(setOf("work", "odd"), ThreadAccounts.signIns(view).keys)
    }

    @Test fun aRefusedSignInKeepsCodexsMessageAndWhereToTurnItOn() {
        val refused = ThreadAccounts.parseSignIn(JSONObject("""{"state":"failed","refused":true,
            "reason":"Error logging in with device code: device code login is not enabled for this Codex server.",
            "help":"Turn on device code sign-in for Codex in ChatGPT: Settings › Security"}"""))!!
        assertFalse(refused.waiting || refused.done)
        assertTrue(refused.refused)
        assertTrue(refused.reason.startsWith("Error logging in with device code"))
        assertNull(ThreadAccounts.parseSignIn(JSONObject("{}")))
        assertTrue(ThreadAccounts.parseSignIn(JSONObject("""{"state":"signed_in"}"""))!!.done)
    }

    @Test fun aLinkThatHoldsTheCodeAsksToConfirmItInsteadOfTypingIt() {
        // Grok's verification link can carry the code; Codex's page never does.
        val grok = ThreadAccounts.SignIn("waiting", url = "https://accounts.x.ai/device?user_code=ABCD-1234", code = "ABCD-1234")
        assertEquals("2. Check that the page shows this code, then confirm:", ThreadAccounts.codeStep(grok))
        val codex = ThreadAccounts.SignIn("waiting", url = "https://auth.openai.com/codex/device", code = "WXYZ-12345")
        assertEquals("2. Enter this one-time code there:", ThreadAccounts.codeStep(codex))
        assertEquals("2. Enter this one-time code there:", ThreadAccounts.codeStep(ThreadAccounts.SignIn("waiting")))
    }

    @Test fun signInParamsNameTheAccountAndCancel() {
        val start = ThreadAccounts.signInParams("codex", "work")
        assertEquals("work", start.getString("name"))
        assertEquals("codex", start.getString("provider"))
        assertFalse(start.has("cancel"))
        assertTrue(ThreadAccounts.signInParams("codex", "work", cancel = true).getBoolean("cancel"))
        assertEquals(ThreadAccounts.SIGN_IN_KEY + "work", DesktopViews.key(ThreadAccounts.SIGN_IN, start))
        assertTrue(ThreadAccounts.SIGN_IN in DesktopViews.ACTIONS)
    }
}
