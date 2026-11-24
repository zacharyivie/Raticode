package dev.raticode.mobile.ui

import java.time.LocalDate
import java.time.ZoneOffset
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MemoryEndDayTest {
    @Test fun aMemoryEndingInAFarOffYearOpensThePickerWithoutAnEndDay() {
        assertEquals(LocalDate.of(2026, 12, 1), endLocalDay("2026-12-01T10:00:00Z", ZoneOffset.UTC))
        // The picker turns the day into epoch milliseconds, which overflow for this year and ended the app.
        assertNull(endLocalDay("+999999999-12-31T00:00:00Z", ZoneOffset.UTC))
        assertNull(endLocalDay("-999999999-01-01T00:00:00Z", ZoneOffset.UTC))
        assertNull(endLocalDay("not a date", ZoneOffset.UTC))
    }
}
