package dev.raticode.mobile.protocol.v2

/** Mobile remains a controller even when the remote peer claims it can grant worker authority. */
object MobileCapabilities {
    const val ROLE = "controller"
    private val allowedControllerCapabilities = setOf("chat", "status", "files", "fleet.read", "remote_approval")

    fun validateGrant(role: String, capabilities: Set<String>, remoteApprovalPolicy: Boolean) {
        require(role == ROLE)
        require(capabilities.all { it in allowedControllerCapabilities })
        require(remoteApprovalPolicy || "remote_approval" !in capabilities)
    }

    fun validateIncomingType(type: String) {
        require(type in setOf("chat.accepted", "chat.message", "request.status", "thread.snapshot",
            "thread.search.snapshot", "thread.history.snapshot", "catalog.snapshot", "sync.snapshot", "fleet.snapshot", "swarm.snapshot", "approval.required", "approval.snapshot",
            "file.offer", "file.request", "file.chunk", "file.status", "error", "job.status", "desktop.result")) { "Unsupported mobile event or worker request" }
    }
}
