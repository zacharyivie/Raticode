package dev.raticode.mobile.live

import org.json.JSONObject

/**
 * The thread model picker's Account menu, read from the desktop with `provider.accounts`. A thread keeps one choice per
 * provider: blank follows Rem's setting on the desktop, [DEFAULT_SIGN_IN] is the provider's default sign-in, and anything
 * else names one of the desktop's accounts. Only desktops whose overview has `thread_accounts` answer the read or take
 * an `account` with `thread.create` and `thread.context.update`.
 */
object ThreadAccounts {
    const val OP = "provider.accounts"
    const val DEFAULT_SIGN_IN = "@default"
    const val REM_SETTING = ""
    const val UPDATE_DESKTOP = "Update Raticode on your computer to choose a thread's account from the phone."
    const val SIGN_IN_HELP = "Sign in on your computer: Settings › Usage › Sign in on this account's card. You can pick it here after that."
    /** Starts or cancels an account's sign-in on the desktop, finished on this phone with a link and a one-time code. */
    const val SIGN_IN = "provider.account.sign_in"
    /** The screen key of a sign-in's answer: one per account. */
    const val SIGN_IN_KEY = "$SIGN_IN:"
    const val SIGN_IN_UPDATE_DESKTOP = "Update Raticode on your computer to sign accounts in from the phone."

    /**
     * An account's sign-in that this phone finishes: the desktop runs the provider's device sign-in (`codex login
     * --device-auth`, `grok login --device-auth`) and passes on the [url] to open and the one-time [code] to enter there.
     * [refused] means the provider refused device code sign-in; [help] says where to turn it on or what to do instead.
     */
    data class SignIn(val state: String, val url: String = "", val code: String = "", val reason: String = "",
                      val help: String = "", val refused: Boolean = false, val secondsLeft: Int = 0) {
        val waiting get() = state == "waiting"
        val done get() = state == "signed_in"
    }

    data class Account(val name: String, val state: String, val needsSignIn: Boolean,
                       val weekUsed: Double? = null, val fiveHourUsed: Double? = null, val observedAt: String = "",
                       /** The desktop can sign this account in from the phone (Codex, Grok); Claude Code accounts sign in on the computer. */
                       val phoneSignIn: Boolean = false, val signIn: SignIn? = null)
    data class Menu(val provider: String, val label: String, val remSetting: String, val remSettingLabel: String,
                    val choice: String, val accounts: List<Account>)
    /** [thread] is the shared thread the menus' choices belong to, or null for a new thread. */
    data class View(val thread: String?, val menus: Map<String, Menu>)
    data class Option(val id: String, val label: String, val meta: String, val enabled: Boolean, val needsSignIn: Boolean = false)

    fun key(thread: String?) = OP + ":" + thread.orEmpty()
    fun params(thread: String?): JSONObject = JSONObject().apply { if (!thread.isNullOrBlank()) put("thread_id", thread) }

    fun parse(key: String, json: JSONObject): View {
        val thread = key.substringAfter(':', "").ifBlank { null }
        val list = json.optJSONArray("providers")
        val menus = (0 until (list?.length() ?: 0)).mapNotNull { list!!.optJSONObject(it) }.map { p ->
            val items = p.optJSONArray("accounts")
            Menu(p.optString("provider"), p.optString("label").ifBlank { p.optString("provider") }, p.optString("rem_setting"),
                p.optString("rem_setting_label").ifBlank { "Default sign-in" }, p.optString("choice"),
                (0 until (items?.length() ?: 0)).mapNotNull { items!!.optJSONObject(it) }.map { a ->
                    Account(a.optString("name"), a.optString("state"), a.optBoolean("needs_sign_in"),
                        a.optDouble("week_used").takeUnless { it.isNaN() || !a.has("week_used") },
                        a.optDouble("five_hour_used").takeUnless { it.isNaN() || !a.has("five_hour_used") },
                        a.optString("observed_at"), a.optBoolean("phone_sign_in"), parseSignIn(a.optJSONObject("sign_in")))
                })
        }.filter { it.provider.isNotBlank() && it.accounts.isNotEmpty() }
        return View(thread, menus.associateBy { it.provider })
    }

    /** A device sign-in from the desktop; only an https link is kept. */
    fun parseSignIn(json: JSONObject?): SignIn? {
        if (json == null) return null
        val state = json.optString("state").take(40).ifBlank { return null }
        val url = json.optString("url").takeIf { it.startsWith("https://") && it.length <= 2048 }.orEmpty()
        return SignIn(state, url, json.optString("code").take(64), json.optString("reason").take(600), json.optString("help").take(400),
            json.optBoolean("refused"), json.optInt("seconds_left").coerceIn(0, 86400))
    }

    fun signInParams(provider: String, name: String, cancel: Boolean = false): JSONObject = JSONObject().put("name", name).apply {
        if (provider.isNotBlank()) put("provider", provider)
        if (cancel) put("cancel", true)
    }

    /** Every account's sign-in in [view], by account name. */
    fun signIns(view: View): Map<String, SignIn> =
        view.menus.values.flatMap { menu -> menu.accounts.mapNotNull { a -> a.signIn?.let { a.name to it } } }.toMap()

    /**
     * What to do with the code on the sign-in page. Grok's link can already hold the code (xAI's verification link with
     * the code filled in), and Grok then asks to confirm it rather than type it.
     */
    fun codeStep(signIn: SignIn): String =
        if (signIn.code.isNotBlank() && signIn.url.contains(signIn.code)) "2. Check that the page shows this code, then confirm:"
        else "2. Enter this one-time code there:"

    /** "Expires in 14 min" for a waiting code, or blank. */
    fun expires(signIn: SignIn): String = when {
        !signIn.waiting || signIn.code.isBlank() || signIn.secondsLeft <= 0 -> ""
        signIn.secondsLeft < 60 -> "Expires in under a minute"
        else -> "Expires in ${signIn.secondsLeft / 60} min"
    }

    /** "Week 42% · 5h 10% used", or blank without a reading. */
    fun readingLabel(account: Account): String {
        val parts = listOfNotNull(account.weekUsed?.let { "Week ${Math.round(it)}%" }, account.fiveHourUsed?.let { "5h ${Math.round(it)}%" })
        return if (parts.isEmpty()) "" else parts.joinToString(" · ") + " used"
    }

    fun options(menu: Menu, choice: String): List<Option> {
        val options = mutableListOf(
            Option(REM_SETTING, "Rem's setting", menu.remSettingLabel, true),
            Option(DEFAULT_SIGN_IN, "Default sign-in", "The provider's usual sign-in", true),
        )
        for (account in menu.accounts) {
            val state = when {
                account.needsSignIn -> "Needs sign-in"
                account.state == "signed_in" -> "Signed in"
                account.state == "unavailable" -> "CLI not found"
                else -> "Not checked"
            }
            val reading = readingLabel(account)
            options += Option(account.name, account.name, if (reading.isNotBlank() && !account.needsSignIn) "$state · $reading" else state,
                !account.needsSignIn, account.needsSignIn)
        }
        if (options.none { it.id == choice }) options += Option(choice, choice, "Removed", false)
        return options
    }

    fun label(menu: Menu, choice: String): String = when (choice) {
        REM_SETTING -> "Rem's setting · ${menu.remSettingLabel}"
        DEFAULT_SIGN_IN -> "Default sign-in"
        else -> choice
    }

    /** The account a choice runs on, for telling whether a change starts a new provider session. */
    fun effective(menu: Menu, choice: String): String = when (choice) {
        DEFAULT_SIGN_IN -> ""
        REM_SETTING -> menu.remSetting
        else -> choice
    }

    fun warning(providerLabel: String, accountLabel: String) =
        "Your next message starts a new $providerLabel session on $accountLabel, as switching providers does. Rem gives it the conversation so far."
}
