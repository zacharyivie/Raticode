# Incident review: DNS outage

For 47 minutes customers could not resolve the API domain. A change to the DNS provider's records removed the apex record during a migration. Monitoring checked the load balancer directly, so alerts did not fire. Actions: external DNS probes, a peer-reviewed record change process, and a lower TTL during migrations.
