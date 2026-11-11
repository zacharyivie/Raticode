package dev.raticode.mobile.data

data class Swarm(val name: String, val detail: String, val progress: Float, val status: String)
object DemoFixtures {
    val swarms = listOf(
        Swarm("Mobile pairing", "2 sample agents / protocol sketch", .65f, "Running"),
        Swarm("Attachment previews", "3 sample agents / checks passed", 1f, "Ready for review"),
    )
}
