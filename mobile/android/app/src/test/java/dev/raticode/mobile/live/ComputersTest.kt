package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** Computers: the desktop's `computers.list` answer, the overview gate and the limit the phone sends. */
class ComputersTest {
    /** The body of src/gofer/devices/protocol/v2/fixtures/desktop.result.computers-list.json; a desktop test keeps them equal. */
    private fun sample() = JSONObject(javaClass.getResource("/desktop/computers-list.json")!!.readText())

    @Test fun readsEachComputerFromTheDesktopsAnswer() {
        val list = DesktopViews.parse(Computers.READ, sample()) as ComputerList
        assertEquals(listOf("Studio desktop", "Workstation", "Laptop"), list.computers.map { it.name })
        assertEquals(60, list.rejoinSeconds)
        val here = list.thisComputer!!
        assertTrue(here.online)
        assertEquals(4, here.maxThreads)
        assertEquals(0, here.maxThreadsSetting)
        assertEquals(2, here.runningThreads)
        assertEquals(35.0, here.cpuPressure!!, 0.0)
        assertEquals(3, here.effectiveThreads)
        assertEquals(1, here.capacity)
        // A paired desktop that reports its reading has a limit, pressure and room.
        val paired = list.computers[1]
        assertFalse(paired.thisComputer)
        assertEquals(8, paired.maxThreads)
        assertEquals(5, paired.capacity)
        assertNull(paired.memoryPressure)
        // An offline one has no room and no limit to show.
        val offline = list.computers[2]
        assertFalse(offline.online)
        assertEquals(0, offline.capacity)
        assertNull(offline.maxThreads)
    }

    @Test fun describesStateLimitRoomAndPressure() {
        val list = Computers.parse(sample())
        val (here, paired, offline) = list.computers
        assertEquals("4 (automatic)", Computers.limitText(here))
        assertEquals("1: pressure lowers its limit to 3", Computers.roomText(here))
        assertEquals("35%", Computers.pressureText(here.cpuPressure))
        assertEquals("Online", Computers.stateText(paired))
        assertEquals("8", Computers.limitText(paired))
        assertEquals("5", Computers.roomText(paired))
        assertEquals("Not reported", Computers.pressureText(paired.memoryPressure))
        assertEquals("Offline", Computers.stateText(offline))
        assertEquals("Not reported", Computers.limitText(offline))
        assertEquals("None while offline", Computers.roomText(offline))
        val back = paired.copy(rejoining = true, capacity = 1)
        assertEquals("Back online", Computers.stateText(back))
        assertEquals("1: one at a time just after it came back", Computers.roomText(back))
        assertEquals("Unknown", Computers.roomText(paired.copy(capacity = null)))
        assertEquals(null, here.memoryHold)
        val held = paired.copy(capacity = 0, memoryHold = "0.9 GB available")
        assertEquals("0: waiting for free memory (0.9 GB available)", Computers.roomText(held))
    }

    @Test fun readsRunningWorkersByKind() {
        val (here, paired, offline) = Computers.parse(sample()).computers
        assertEquals(mapOf("rem" to 1, "phone" to 0, "organizations" to 1, "swarms" to 0), here.runningByKind)
        assertEquals("2: 1 Rem task, 1 organization worker", Computers.runningText(here))
        // A paired desktop shares counts only, never what its workers are.
        assertEquals("3: 2 organization workers, 1 Rem task", Computers.runningText(paired))
        assertEquals("None", Computers.runningText(offline))
        val full = here.copy(runningThreads = 4, runningByKind = mapOf("rem" to 1, "phone" to 0, "organizations" to 2, "swarms" to 1))
        assertEquals("2 organization workers, 1 Rem task, 1 swarm turn", Computers.breakdownText(full))
        // An older desktop that shares only its total, or counts that disagree with it, show the total alone.
        assertEquals("3", Computers.runningText(paired.copy(runningByKind = null)))
        assertEquals("4", Computers.runningText(full.copy(runningByKind = mapOf("rem" to 1))))
    }

    @Test fun skipsBadEntriesAndClampsPressure() {
        val json = JSONObject("""{"computers":[{"id":"","name":"No id"},{"id":"a","state":"online","cpu_pressure":250,
            "max_threads":null,"capacity":null}],"checked_at":5}""")
        val list = Computers.parse(json)
        assertEquals(1, list.computers.size)
        val only = list.computers.single()
        assertEquals("Desktop", only.name)
        assertEquals(100.0, only.cpuPressure!!, 0.0)
        assertNull(only.maxThreads)
        assertNull(list.thisComputer)
    }

    @Test fun onlyADesktopThatListsComputersGetsTheReadOrTheChange() {
        assertTrue(Computers.READ in DesktopViews.READS)
        assertTrue(Computers.SET in DesktopViews.ACTIONS)
        assertTrue(Computers.blocked(Computers.READ, desktopListsComputers = false))
        assertTrue(Computers.blocked(Computers.SET, desktopListsComputers = false))
        assertFalse(Computers.blocked(Computers.READ, desktopListsComputers = true))
        assertFalse(Computers.blocked("usage.overview", desktopListsComputers = false))
        val overview = { json: JSONObject -> DesktopViews.parse("overview", json) as DesktopOverview }
        assertTrue(overview(JSONObject().put("computers", true)).computers)
        assertFalse(overview(JSONObject()).computers)
        // A change answers with the whole list, so the open screen refreshes.
        assertEquals(Computers.READ, DesktopViews.key(Computers.SET, Computers.setParams(3)))
    }

    @Test fun sendsOnlyALimitTheDesktopTakes() {
        // src/gofer/devices/protocol/v2/fixtures/desktop.action.computers-set.json, without the optional computer_id.
        assertEquals("""{"max_threads":6}""", Computers.setParams(6).toString())
        assertEquals("""{"max_threads":0}""", Computers.setParams(0).toString())
        assertThrows(IllegalArgumentException::class.java) { Computers.setParams(17) }
        assertThrows(IllegalArgumentException::class.java) { Computers.setParams(-1) }
    }

    /** The body of src/gofer/devices/protocol/v2/fixtures/desktop.result.computers-placement.json; a desktop test keeps them equal. */
    private fun placementSample() = JSONObject(javaClass.getResource("/desktop/computers-placement.json")!!.readText())

    @Test fun readsRemsAndEachOrganizationsPlacement() {
        val view = DesktopViews.parse(Computers.PLACEMENT, placementSample()) as PlacementView
        assertEquals(listOf("Rem", "Acme", "Beta"), view.consumers.map { it.name })
        val (rem, acme, beta) = view.consumers
        assertTrue(rem.isRem)
        assertEquals("balanced", rem.strategy)
        assertEquals("Spread evenly", Computers.STRATEGIES.toMap().getValue(rem.strategy))
        assertEquals("Any of your computers", Computers.assignedText(rem, view))
        assertFalse(acme.isRem)
        assertEquals("manual", acme.strategy)
        assertEquals("Only computers I pick", Computers.STRATEGIES.toMap().getValue(acme.strategy))
        assertEquals(listOf("00000000-0000-4000-8000-000000000005"), acme.shared)
        assertEquals(listOf("00000000-0000-4000-8000-000000000006"), acme.missing)
        assertEquals("Laptop (shared), Unpaired computer 00000000 (missing)", Computers.assignedText(acme, view))
        // Acme picked no computer work can start on yet, so the desktop warns; the others are fine.
        assertEquals("Organization workers start only on this computer for now, so with only Laptop picked, " +
            "its tickets wait unless they run through a fleet execution target there.", acme.notice)
        assertEquals("", rem.notice)
        assertFalse(beta.saved)
        assertEquals("Fill one computer, then the next", Computers.STRATEGIES.toMap().getValue(beta.strategy))
        assertEquals("Any of your computers (default)", Computers.assignedText(beta, view))
        assertEquals("", beta.notice)
        // Computers come in the user's order, which overflow fills in turn.
        assertEquals(listOf("Laptop", "Studio desktop"), view.computers.map { it.name })
        assertEquals(view.computers.map { it.id }, view.order)
        assertTrue(view.computers[1].thisComputer)
        assertFalse(view.computers[0].online)
    }

    @Test fun skipsBadConsumersAndUnknownStrategiesReadAsTheDefault() {
        val json = JSONObject("""{"consumers":[{"consumer":"../etc","strategy":"manual"},{"consumer":"org:x","strategy":"nearest",
            "computers":["../a","b"]}],"order":["z","b"],"computers":[{"id":"a","name":"A"},{"id":"b","name":"B"},{"id":""}]}""")
        val view = Computers.parsePlacement(json)
        val only = view.consumers.single()
        assertEquals("org:x", only.consumer)
        assertEquals("overflow", only.strategy)
        assertEquals("Organization", only.name)
        assertEquals(listOf("b"), only.computers)
        assertEquals(listOf("b", "a"), view.order)
        assertEquals(listOf("B", "A"), view.computers.map { it.name })
    }

    @Test fun onlyADesktopThatPlacesWorkGetsThePlacementReadOrChange() {
        assertTrue(Computers.PLACEMENT in DesktopViews.READS)
        assertTrue(Computers.PLACE in DesktopViews.ACTIONS)
        assertTrue(Computers.placementBlocked(Computers.PLACEMENT, desktopPlacesWork = false))
        assertTrue(Computers.placementBlocked(Computers.PLACE, desktopPlacesWork = false))
        assertFalse(Computers.placementBlocked(Computers.PLACE, desktopPlacesWork = true))
        assertFalse(Computers.placementBlocked(Computers.READ, desktopPlacesWork = false))
        val overview = { json: JSONObject -> DesktopViews.parse("overview", json) as DesktopOverview }
        assertTrue(overview(JSONObject().put("computer_placement", true)).computerPlacement)
        assertFalse(overview(JSONObject().put("computers", true)).computerPlacement)
        // A change answers with every placement, so the open tab refreshes.
        assertEquals(Computers.PLACEMENT, DesktopViews.key(Computers.PLACE, Computers.placeParams("rem", "balanced")))
    }

    @Test fun sendsOnlyAPlacementTheDesktopTakes() {
        // The desktop reads fields by name, so compare them that way.
        fun fields(o: JSONObject) = o.keys().asSequence().associateWith { o.get(it).toString() }
        fun same(expected: String, actual: JSONObject) = assertEquals(fields(JSONObject(expected)), fields(actual))
        // src/gofer/devices/protocol/v2/fixtures/desktop.action.computers-place.json
        val laptop = "00000000-0000-4000-8000-000000000005"
        same("""{"consumer":"org:00000000-0000-4000-8000-0000000000a1","strategy":"manual","computers":["$laptop"],"shared":["$laptop"]}""",
            Computers.placeParams("org:00000000-0000-4000-8000-0000000000a1", "manual", listOf(laptop), listOf(laptop)))
        same("""{"consumer":"rem","strategy":"overflow"}""", Computers.placeParams("rem", "overflow", listOf(laptop)))
        // Shared computers must be picked ones; repeats go once.
        same("""{"consumer":"rem","strategy":"manual","computers":["a"]}""", Computers.placeParams("rem", "manual", listOf("a", "a"), listOf("b")))
        assertThrows(IllegalArgumentException::class.java) { Computers.placeParams("rem", "manual") }
        assertThrows(IllegalArgumentException::class.java) { Computers.placeParams("rem", "nearest") }
        assertThrows(IllegalArgumentException::class.java) { Computers.placeParams("/etc", "balanced") }
        assertThrows(IllegalArgumentException::class.java) { Computers.placeParams("rem", "manual", listOf("../a")) }
    }

    @Test fun everyComputerThePhoneOffersCanBeSentBackAndTheLimitStaysInRange() {
        val listed = org.json.JSONArray()
        listOf("a_b", "a.b", "../x").forEach { listed.put(JSONObject().put("id", it)) }
        (1..80).forEach { listed.put(JSONObject().put("id", "pc-$it")) }
        val view = Computers.parsePlacement(JSONObject().put("computers", listed).put("order", org.json.JSONArray(listOf("a.b", "pc-2"))))
        assertEquals(64, view.order.size)
        assertEquals("pc-2", view.order.first())
        assertTrue(view.computers.none { it.id == "a_b" || it.id == "a.b" || it.id == "../x" })
        // Ticking every offered computer is a placement the phone can send; before, a 65th or "a_b" ended the app.
        assertEquals(64, Computers.placeParams("rem", "manual", view.order).getJSONArray("computers").length())
        val limits = Computers.parse(JSONObject().put("computers", org.json.JSONArray()
            .put(JSONObject().put("id", "a").put("max_threads_setting", 40))
            .put(JSONObject().put("id", "b").put("max_threads_setting", -3))
            .put(JSONObject().put("id", "c").put("max_threads_setting", 16))))
        assertEquals(listOf(null, null, 16), limits.computers.map { it.maxThreadsSetting })
    }
}
