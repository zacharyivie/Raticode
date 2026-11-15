package dev.raticode.mobile.ui

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit
import dev.raticode.mobile.live.BoardFilters
import dev.raticode.mobile.live.GlobalBoardFilters
import dev.raticode.mobile.live.OrganizationBoardFilters

/** Board filters saved on this phone, per board, so they survive leaving the board and closing the app. */
internal object BoardFilterPrefs {
    private fun prefs(context: Context) = context.getSharedPreferences("board_filters", Context.MODE_PRIVATE)

    fun global(context: Context) = global(prefs(context))
    fun save(context: Context, filters: GlobalBoardFilters) = save(prefs(context), filters)
    fun organization(context: Context, id: String) = organization(prefs(context), id)
    fun save(context: Context, id: String, filters: OrganizationBoardFilters) = save(prefs(context), id, filters)
    fun tab(context: Context) = tab(prefs(context))
    fun saveTab(context: Context, tab: String) = saveTab(prefs(context), tab)

    fun global(prefs: SharedPreferences) = BoardFilters.parseGlobal(prefs.getString(BoardFilters.GLOBAL, null))
    fun save(prefs: SharedPreferences, filters: GlobalBoardFilters, now: Long = System.currentTimeMillis()) =
        write(prefs, BoardFilters.GLOBAL, if (filters == GlobalBoardFilters()) null else BoardFilters.encode(filters, now))

    fun organization(prefs: SharedPreferences, id: String) = BoardFilters.parseOrganization(prefs.getString(BoardFilters.organization(id), null))
    fun save(prefs: SharedPreferences, id: String, filters: OrganizationBoardFilters, now: Long = System.currentTimeMillis()) =
        write(prefs, BoardFilters.organization(id), if (filters == OrganizationBoardFilters()) null else BoardFilters.encode(filters, now))

    fun tab(prefs: SharedPreferences) = BoardFilters.parseTab(prefs.getString(BoardFilters.GLOBAL_TAB, null))
    fun saveTab(prefs: SharedPreferences, tab: String) = prefs.edit { putString(BoardFilters.GLOBAL_TAB, BoardFilters.parseTab(tab)) }

    private fun write(prefs: SharedPreferences, key: String, value: String?) {
        val stale = if (value == null) emptyList() else BoardFilters.overflow(prefs.all.mapValues { it.value as? String }, key)
        prefs.edit {
            if (value == null) remove(key) else putString(key, value)
            stale.forEach { remove(it) }
        }
    }
}
