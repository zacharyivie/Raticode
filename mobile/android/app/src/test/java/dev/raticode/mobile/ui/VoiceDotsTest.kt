package dev.raticode.mobile.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceDotsTest {
    private fun run(meter: DotMeter, level: Float, ms: Int, from: Int = 0, calm: Boolean = false) {
        for (t in from until from + ms step 16) meter.step(16f, level, t.toFloat(), calm)
    }

    @Test fun silenceKeepsEveryColumnOnTheLine() {
        val meter = DotMeter(48)
        run(meter, 0f, 2000)
        assertTrue(meter.heights.all { it == 0f })
        assertTrue(meter.peaks.all { it == 0f })
    }

    @Test fun aLoudVoiceRaisesTheMiddleMostAndNeverPastTheTop() {
        val meter = DotMeter(48)
        run(meter, 1f, 1500, calm = true)
        val middle = meter.heights.slice(20..27).average()
        val edges = (meter.heights.take(4) + meter.heights.takeLast(4)).average()
        assertTrue("middle $middle, edges $edges", middle > edges * 1.8)
        assertTrue(meter.heights.all { it in 0f..DotMeter.ROWS.toFloat() })
        assertTrue("a loud voice fills most of the middle", middle > DotMeter.ROWS * 0.5)
    }

    @Test fun columnsFollowTheVoiceUpAndDown() {
        val meter = DotMeter(32)
        run(meter, 0.3f, 600, calm = true)
        val soft = meter.heights.sum()
        run(meter, 0.9f, 600, from = 600, calm = true)
        val loud = meter.heights.sum()
        assertTrue(loud > soft * 2)
        run(meter, 0f, 1500, from = 1200, calm = true)
        assertTrue("back on the line after the voice stops", meter.heights.all { it < 0.05f })
    }

    @Test fun peaksHangThenFall() {
        val meter = DotMeter(16)
        run(meter, 1f, 800, calm = true)
        val top = meter.peaks.copyOf()
        // Just after the voice stops the columns drop but their peak dots stay put.
        run(meter, 0f, 300, from = 800, calm = true)
        for (i in top.indices) assertEquals(top[i], meter.peaks[i], 0.001f)
        assertTrue((0 until 16).any { meter.peaks[it] - meter.heights[it] > 0.6f })
        // A little later they fall back to the line.
        run(meter, 0f, 1500, from = 1100, calm = true)
        assertTrue(meter.peaks.all { it < 0.05f })
    }

    @Test fun resizingStartsTheLineAfresh() {
        val meter = DotMeter(10)
        run(meter, 1f, 500)
        meter.resize(40)
        assertEquals(40, meter.columns)
        assertTrue(meter.heights.all { it == 0f })
        meter.resize(40)
        run(meter, 1f, 200)
        assertTrue(meter.heights.any { it > 0f })
    }

    @Test fun theScatterIsFixedAndInRange() {
        val first = (0 until 200).map { DotMeter.noise(it) }
        assertEquals(first, (0 until 200).map { DotMeter.noise(it) })
        assertTrue(first.all { it in 0f..1f })
        assertTrue("not all the same", first.toSet().size > 150)
    }
}
