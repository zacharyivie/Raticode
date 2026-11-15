package dev.raticode.mobile.ui

import android.content.SharedPreferences
import dev.raticode.mobile.live.BoardFilters
import dev.raticode.mobile.live.GlobalBoardFilters
import dev.raticode.mobile.live.Kanban
import dev.raticode.mobile.live.OrganizationBoardFilters
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** What the board pages save and read back: a new page, or the app after a restart, reads the same preferences. */
class BoardFilterPrefsTest {
    /** Stands in for the phone's SharedPreferences file, which outlives the page and the app. */
    private class MemoryPrefs : SharedPreferences {
        val values = mutableMapOf<String, Any?>()
        override fun getAll(): Map<String, *> = values.toMap()
        override fun getString(key: String, defValue: String?) = values[key] as? String ?: defValue
        override fun getStringSet(key: String, defValues: Set<String>?) = defValues
        override fun getInt(key: String, defValue: Int) = defValue
        override fun getLong(key: String, defValue: Long) = defValue
        override fun getFloat(key: String, defValue: Float) = defValue
        override fun getBoolean(key: String, defValue: Boolean) = defValue
        override fun contains(key: String) = key in values
        override fun edit(): SharedPreferences.Editor = Editor()
        override fun registerOnSharedPreferenceChangeListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) {}
        override fun unregisterOnSharedPreferenceChangeListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) {}
        inner class Editor : SharedPreferences.Editor {
            private val puts = mutableMapOf<String, Any?>(); private val removes = mutableSetOf<String>()
            override fun putString(key: String, value: String?) = apply { puts[key] = value }
            override fun putStringSet(key: String, values: Set<String>?) = apply { puts[key] = values }
            override fun putInt(key: String, value: Int) = apply { puts[key] = value }
            override fun putLong(key: String, value: Long) = apply { puts[key] = value }
            override fun putFloat(key: String, value: Float) = apply { puts[key] = value }
            override fun putBoolean(key: String, value: Boolean) = apply { puts[key] = value }
            override fun remove(key: String) = apply { removes += key }
            override fun clear() = apply { removes += values.keys }
            override fun commit(): Boolean { apply(); return true }
            override fun apply() { removes.forEach { values.remove(it) }; values.putAll(puts) }
        }
    }

    @Test fun theGlobalBoardOpensWithItsLastFiltersAndTab() {
        val prefs = MemoryPrefs()
        assertEquals(GlobalBoardFilters(), BoardFilterPrefs.global(prefs))
        assertEquals("board", BoardFilterPrefs.tab(prefs))
        val rem = GlobalBoardFilters(scope = Kanban.SCOPE_REM, assignee = Kanban.ANY, column = "running")
        BoardFilterPrefs.save(prefs, rem)
        BoardFilterPrefs.saveTab(prefs, KanbanTabs.PROPOSED)
        // Leaving the board and coming back, or reopening the app, reads the same file.
        assertEquals(rem, BoardFilterPrefs.global(prefs))
        assertEquals(KanbanTabs.PROPOSED, BoardFilterPrefs.tab(prefs))
        // Clearing everything leaves nothing behind.
        BoardFilterPrefs.save(prefs, GlobalBoardFilters())
        assertFalse(prefs.contains(BoardFilters.GLOBAL))
        assertEquals(GlobalBoardFilters(), BoardFilterPrefs.global(prefs))
    }

    @Test fun eachOrganizationKeepsItsOwnFilters() {
        val prefs = MemoryPrefs()
        BoardFilterPrefs.save(prefs, "acme", OrganizationBoardFilters(worker = "ana", needsMe = true))
        BoardFilterPrefs.save(prefs, "beta", OrganizationBoardFilters(project = "site", column = "review"))
        BoardFilterPrefs.save(prefs, GlobalBoardFilters(scope = "acme"))
        assertEquals(OrganizationBoardFilters(worker = "ana", needsMe = true), BoardFilterPrefs.organization(prefs, "acme"))
        assertEquals(OrganizationBoardFilters(project = "site", column = "review"), BoardFilterPrefs.organization(prefs, "beta"))
        assertEquals(OrganizationBoardFilters(), BoardFilterPrefs.organization(prefs, "never-opened"))
        assertEquals(GlobalBoardFilters(scope = "acme"), BoardFilterPrefs.global(prefs))
    }

    @Test fun manyOrganizationsDropTheOldestButNeverTheGlobalBoard() {
        val prefs = MemoryPrefs()
        BoardFilterPrefs.save(prefs, GlobalBoardFilters(scope = Kanban.SCOPE_REM), now = 0L)
        BoardFilterPrefs.saveTab(prefs, KanbanTabs.PEOPLE)
        for (i in 1..(BoardFilters.MAX_BOARDS + 5)) BoardFilterPrefs.save(prefs, "o$i", OrganizationBoardFilters(needsMe = true), now = 1000L + i)
        assertEquals(BoardFilters.MAX_BOARDS + 2, prefs.values.size) // the organizations, the global board and its tab
        assertEquals(GlobalBoardFilters(scope = Kanban.SCOPE_REM), BoardFilterPrefs.global(prefs))
        assertEquals(KanbanTabs.PEOPLE, BoardFilterPrefs.tab(prefs))
        assertTrue(prefs.contains(BoardFilters.organization("o${BoardFilters.MAX_BOARDS + 5}")))
        assertFalse(prefs.contains(BoardFilters.organization("o1")))
    }
}
