package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertThrows
import org.junit.Test

class ConnectionSnapshotTest {
    private fun snapshot(host: String? = null): JSONObject {
        val endpoint = if (host == null) JSONObject.NULL else JSONObject().put("host", host).put("port", 18766)
        return JSONObject().put("type", "sync.snapshot").put("thread_id", JSONObject.NULL).put("request_id", JSONObject.NULL)
            .put("payload", JSONObject().put("revision", 1).put("through_sequence", 0).put("threads", org.json.JSONArray())
                .put("connection", JSONObject().put("lan_endpoint", endpoint).put("rem_ready", true)))
    }
    @Test fun acceptsPrivateRouteUpdatesAndLegacySnapshots() {
        IncomingValidation.validate(snapshot("192.168.1.20"))
        IncomingValidation.validate(snapshot())
        val legacy = snapshot(); legacy.getJSONObject("payload").remove("connection")
        IncomingValidation.validate(legacy)
    }
    @Test fun acceptsTailscaleAddressesOnlyInTailscaleRanges() {
        IncomingValidation.validate(snapshot("100.101.102.103"))
        IncomingValidation.validate(snapshot("fd7a:115c:a1e0::9"))
        val both = snapshot("192.168.1.20")
        both.getJSONObject("payload").getJSONObject("connection").put("tailnet_endpoint", JSONObject().put("host", "100.101.102.103").put("port", 44233))
        IncomingValidation.validate(both)
        both.getJSONObject("payload").getJSONObject("connection").put("tailnet_endpoint", JSONObject.NULL)
        IncomingValidation.validate(both)
        for (host in listOf("192.168.1.21", "8.8.8.8", "desktop.tail1234.ts.net")) {
            both.getJSONObject("payload").getJSONObject("connection").put("tailnet_endpoint", JSONObject().put("host", host).put("port", 44233))
            assertThrows(Exception::class.java) { IncomingValidation.validate(both) }
        }
    }
    @Test fun rejectsHostnamesPublicAddressesAndExtraAuthority() {
        for (host in listOf("8.8.8.8", "example.com", "0.0.0.0"))
            assertThrows(Exception::class.java) { IncomingValidation.validate(snapshot(host)) }
        val added = snapshot("192.168.1.20")
        added.getJSONObject("payload").getJSONObject("connection").put("execute", true)
        assertThrows(Exception::class.java) { IncomingValidation.validate(added) }
    }
}
