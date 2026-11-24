package dev.raticode.mobile.live

import org.json.JSONObject
import java.util.Locale

/** One provider account's estimated cost at API list prices. [costUsd] null means unknown, never $0. */
data class UsageCostProvider(val provider: String, val profile: String?, val costUsd: Double?, val lowerBound: Boolean,
                             val unsplitCalls: Int, val unpricedModels: List<String>)
data class UsageCost(
    val method: String, val pricesChecked: String, val costUsd: Double?, val lowerBound: Boolean,
    val calls: Int, val unsplitCalls: Int, val unpricedCalls: Int, val providers: List<UsageCostProvider>,
)

/**
 * The usage page's estimate of what Raticode's tokens would cost at API list prices.
 * The desktop works it out (gofer/core/usage_cost.py); the phone only shows it.
 */
object UsageCosts {
    private fun JSONObject.money(key: String): Double? = (opt(key) as? Number)?.toDouble()?.takeIf { it.isFinite() }
    private fun JSONObject.objects(key: String): List<JSONObject> = optJSONArray(key)?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
    private fun JSONObject.strings(key: String): List<String> = optJSONArray(key)?.let { a -> (0 until a.length()).mapNotNull { a.optString(it).takeIf { s -> s.isNotBlank() } } }.orEmpty()

    /** Null from a desktop without the estimate. */
    fun parse(json: JSONObject?): UsageCost? {
        if (json == null) return null
        val total = json.optJSONObject("total") ?: JSONObject()
        return UsageCost(
            json.optString("method"), json.optString("prices_checked"), total.money("cost_usd"), total.optBoolean("lower_bound"),
            total.optInt("calls"), total.optInt("unsplit_calls"), total.optInt("unpriced_calls"),
            json.objects("providers").map { p -> UsageCostProvider(p.optString("provider"), p.optString("profile").takeIf { it.isNotBlank() && it != "null" },
                p.money("cost_usd"), p.optBoolean("lower_bound"), p.optInt("unsplit_calls"), p.strings("unpriced_models")) },
        )
    }

    /** "$1,234.56", "<$0.01", or "Unknown" when the desktop couldn't price it. */
    fun usd(value: Double?): String {
        if (value == null || !value.isFinite()) return "Unknown"
        val size = kotlin.math.abs(value)
        if (size > 0 && size < 0.01) return if (value < 0) "−<$0.01" else "<$0.01"
        val text = String.format(Locale.US, "%,.2f", size)
        return if (value < 0) "−$$text" else "$$text"
    }

    fun label(value: Double?, lowerBound: Boolean): String = if (value == null) "Unknown" else (if (lowerBound) "at least " else "") + usd(value)
}
