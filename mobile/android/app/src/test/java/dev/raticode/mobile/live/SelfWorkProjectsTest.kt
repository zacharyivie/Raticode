package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Each of Rem's projects in self-work: its level of effort, threads and own objectives, read and changed from the phone. */
class SelfWorkProjectsTest {
    private fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())
    private val trading = "22222222-2222-5222-8222-222222222222"

    @Test fun readsEachProjectsLevelOfEffortThreadsAndOwnObjectives() {
        val view = DesktopViews.parse(Kanban.SELF_WORK, read("selfwork-read-projects.json")) as SelfWorkView
        assertTrue(Kanban.canChangeObjectives(view))
        val projects = view.projects!!
        assertEquals(listOf("Raticode", "agent-trading"), projects.map { it.name })
        assertEquals("Level of effort 70% · 7 of 7 threads", Kanban.projectLine(projects[0]))
        assertEquals("1 of 3 threads", Kanban.projectThreads(projects[1]))
        val objectives = view.objectives!!
        assertEquals(12L, objectives.revision)
        assertEquals(listOf("Improve reliability of the Second Brain tools"), objectives.items.map { it.text })
        val own = Kanban.projectObjectives(objectives, trading)
        assertEquals(listOf("t1", "t2"), own.items.map { it.id })
        assertEquals("1/2", Kanban.objectivesCount(own))
        // A project with none of its own yet still gets a section, with an empty list.
        assertTrue(Kanban.projectObjectives(objectives, projects[0].projectId).items.isEmpty())
    }

    @Test fun anOlderDesktopKeepsTheCardReadOnly() {
        val json = read("selfwork-read-projects.json")
        json.remove("projects")
        val view = DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView
        assertNull(view.projects)
        assertFalse(Kanban.canChangeObjectives(view))
    }

    @Test fun onlySeveralProjectsGetLevelOfEffortButtons() {
        // The levels total 100%, and the desktop refuses any other number for Rem's only project.
        val json = read("selfwork-read-projects.json")
        assertTrue(Kanban.canChangeEffort(DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView))
        val projects = json.getJSONArray("projects")
        while (projects.length() > 1) projects.remove(projects.length() - 1)
        val one = DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView
        assertEquals(1, one.projects!!.size)
        assertTrue(Kanban.canChangeObjectives(one))
        assertFalse(Kanban.canChangeEffort(one))
        projects.remove(0)
        assertFalse(Kanban.canChangeEffort(DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView))
        json.remove("projects")
        assertFalse(Kanban.canChangeEffort(DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView))
    }

    @Test fun changesNameTheRevisionAndTheProjectById() {
        val view = DesktopViews.parse(Kanban.SELF_WORK, read("selfwork-read-projects.json")) as SelfWorkView
        val objectives = view.objectives!!
        val added = Kanban.addObjectiveParams(objectives, "  Cut drawdowns in half  ", " ", trading)!!
        assertEquals("add", added.getString("action")); assertEquals("Cut drawdowns in half", added.getString("text"))
        assertEquals(trading, added.getString("project_id")); assertEquals(12L, added.getLong("expected_revision"))
        assertFalse(Kanban.addObjectiveParams(objectives, "Global goal", "", null)!!.has("project_id"))
        assertNull(Kanban.addObjectiveParams(objectives, "   ", "", trading))
        val done = Kanban.projectObjectives(objectives, trading).items.first { it.done }
        assertEquals("uncheck", Kanban.toggleObjectiveParams(objectives, done).getString("action"))
        assertEquals("remove", Kanban.removeObjectiveParams(objectives, done).getString("action"))
        val effort = Kanban.effortParams(view.settings, trading, 140)
        assertEquals(7L, effort.getLong("expected_revision"))
        assertEquals(100, effort.getJSONObject("project_effort").getInt(trading))
        assertTrue(Kanban.OBJECTIVES in Kanban.ACTIONS)
        assertEquals(Kanban.SELF_WORK, DesktopViews.key(Kanban.OBJECTIVES, JSONObject()))
    }

    @Test fun editObjectiveParamsSendTextDoneWhenAndWhereItApplies() {
        val objectives = (DesktopViews.parse(Kanban.SELF_WORK, read("selfwork-read-projects.json")) as SelfWorkView).objectives!!
        assertTrue(objectives.movable)
        val global = objectives.items.first()
        assertEquals(Kanban.EVERY_PROJECT, Kanban.objectiveScope(objectives, global))
        // To a project: its ID, with the text and finish line tidied and the revision the phone showed.
        val moved = Kanban.editObjectiveParams(objectives, global, "  Raise   returns ", " Sharpe above 1.5 ", trading)!!
        assertEquals("edit", moved.getString("action")); assertEquals("g1", moved.getString("id"))
        assertEquals("Raise   returns", moved.getString("text")); assertEquals("Sharpe above 1.5", moved.getString("done_when"))
        assertEquals(trading, moved.getString("project_id")); assertFalse(moved.has("global"))
        assertEquals(12L, moved.getLong("expected_revision"))
        // To the global ones: an explicit global, since the desktop maps project_id only when it is set.
        val own = Kanban.projectObjectives(objectives, trading).items.first()
        assertEquals(trading, Kanban.objectiveScope(objectives, own))
        val back = Kanban.editObjectiveParams(objectives, own, own.text, "", Kanban.EVERY_PROJECT)!!
        assertTrue(back.getBoolean("global")); assertFalse(back.has("project_id"))
        // No scope, or an older desktop that can't move them: only the text and Done when change.
        val kept = Kanban.editObjectiveParams(objectives, own, "Reworded", "", null)!!
        assertFalse(kept.has("global") || kept.has("project_id"))
        val older = objectives.copy(movable = false)
        val plain = Kanban.editObjectiveParams(older, own, "Reworded", "Done", Kanban.EVERY_PROJECT)!!
        assertFalse(plain.has("global") || plain.has("project_id"))
        assertEquals("Done", plain.getString("done_when"))
        assertNull(Kanban.editObjectiveParams(objectives, own, "   ", "", trading))
    }

    @Test fun anOlderDesktopsObjectivesAreNotMovable() {
        val json = read("selfwork-read-projects.json")
        json.getJSONObject("objectives").remove("movable")
        assertFalse((DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView).objectives!!.movable)
    }

    @Test fun askRemSendsWhatTheProtocolFixtureHoldsAndReadsEachProjectsAsk() {
        // src/gofer/devices/protocol/v2/fixtures/desktop.action.selfwork-objectives-ask.json
        val fixture = JSONObject("""{"action":"ask","project_id":"6f1c2a8e-3b4d-5e6f-8a9b-0c1d2e3f4a5b"}""")
        val params = Kanban.askObjectivesParams("6f1c2a8e-3b4d-5e6f-8a9b-0c1d2e3f4a5b")
        assertEquals(fixture.toString(), params.toString())
        val json = read("selfwork-read-projects.json")
        json.getJSONObject("objectives").put("askable", true).put("asking", JSONObject()
            .put(trading, JSONObject().put("state", "asking").put("message", "Rem is reading agent-trading to write objectives…").put("added", 0))
            .put("odd", JSONObject().put("state", "dancing")))
        val objectives = (DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView).objectives!!
        assertTrue(objectives.askable)
        assertEquals(setOf(trading), objectives.asking.keys)
        assertEquals(AskLine(true, false, "Rem is reading agent-trading to write objectives…"), Kanban.askLine(objectives.asking[trading]))
        assertEquals(AskLine(true, false, "Asking Rem…"), Kanban.askLine(null, sending = true))
        val failed = ObjectiveAsk("failed", "Rem's model couldn't answer.", 0)
        assertEquals(AskLine(false, true, "Rem's model couldn't answer."), Kanban.askLine(failed))
        // Pressed again after a failure: the new press shows until the desktop answers.
        assertEquals(AskLine(true, false, "Asking Rem…"), Kanban.askLine(failed, sending = true))
        assertEquals(AskLine(false, false, "Added 4 objectives for agent-trading."), Kanban.askLine(ObjectiveAsk("added", "Added 4 objectives for agent-trading.", 4)))
        assertEquals(AskLine(false, false, ""), Kanban.askLine(null))
        // An older desktop doesn't take the ask, and the phone shows no button.
        assertFalse((DesktopViews.parse(Kanban.SELF_WORK, read("selfwork-read-projects.json")) as SelfWorkView).objectives!!.askable)
    }
}
