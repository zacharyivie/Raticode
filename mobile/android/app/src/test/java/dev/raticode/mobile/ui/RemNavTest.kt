package dev.raticode.mobile.ui

import org.junit.Assert.*
import org.junit.Test

class RemNavTest {
    @Test fun theRemTabStartsOnTheChat() {
        assertTrue(RemNav().chatShown)
        assertFalse(RemNav(tasks = true).chatShown)
    }

    @Test fun backFromTheTaskListReturnsToTheChat() {
        assertEquals(RemNav(), RemNav(tasks = true).back())
    }

    @Test fun aTaskReturnsToWhereItWasOpened() {
        // From the task list, back goes to the list, then to the chat.
        val fromList = RemNav(tasks = true, taskId = "t1")
        assertEquals(RemNav(tasks = true), fromList.back())
        assertEquals(RemNav(), fromList.back().back())
        // From a task card in the chat, back goes straight to the chat.
        assertEquals(RemNav(), RemNav(taskId = "t1").back())
    }

    @Test fun aWorkThreadReturnsToItsTaskThenTheList() {
        val nav = RemNav(tasks = true, taskId = "t1", threadId = "th1")
        assertEquals(RemNav(tasks = true, taskId = "t1"), nav.back())
        assertEquals(RemNav(), RemNav(threadId = "th1").back())
    }

    @Test fun memoriesCloseFirst() {
        assertEquals(RemNav(), RemNav(memories = true).back())
    }

    @Test fun backAtTheChatStaysOnTheChat() {
        assertEquals(RemNav(), RemNav().back())
    }

    @Test fun taskCountsReadNaturally() {
        assertEquals("", RemNav.tasksCount(null))
        assertEquals("1 task", RemNav.tasksCount(1))
        assertEquals("6 tasks", RemNav.tasksCount(6))
    }
}
