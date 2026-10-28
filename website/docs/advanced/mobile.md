---
title: Mobile and device pairing
description: Understand the coming mobile app and the experimental trusted-device connection.
pageClass: docs-layout
---

# Mobile and device pairing

The mobile app is being built as a controller for Rem on your desktop, with trusted pairing between your own devices.

::: info Coming soon
Raticode Mobile does not yet have a public download on the website. Install the [desktop app](../installation.md) today. Development capabilities described here are not a promise that every release package has completed device acceptance testing.
:::

## What the mobile controller does

The Android client connects to a desktop that remains running. It shares the user's Rem thread history, pinned and archived threads, provider/model choices, project scope, and permission controls. It does not run desktop coding-provider CLIs on the phone.

The desktop renderer must remain available for history synchronization. Dedicated remote approval and swarm consoles are not yet complete. Use the desktop for those workflows.

## Enable experimental networking

Quit and relaunch the desktop with `RATICODE_DEVICE_EXPERIMENTAL_NETWORK=1` in its environment. Use matching, compatible builds on both sides. Open **Settings > Devices** to configure transport and inspect paired devices.

Start on a private network using the device listener's configured host and port. Do not expose the ordinary desktop HTTP API as a substitute for the device protocol. Relay and notification options have their own configuration and acceptance checks.

## Pair with mutual confirmation

1. Generate a pairing invitation on the desktop.
2. Scan its QR code or use the supported invitation flow on the other device.
3. Compare the displayed identity fingerprint on both devices.
4. Confirm on both sides before accepting trust.

An invitation expires after five minutes. Merely scanning it does not grant a permanent trusted connection. Device identities and protected credentials use the platform's credential storage.

The connection uses pinned device identities and encrypted transport. A relay transports encrypted messages; it is not trusted to impersonate the endpoint. A successful local connection is still separate from proving two-host internet connectivity and sustained release behavior.

## Review scope and revoke access

Treat a shared-user phone as another interface for the same user, rather than an untrusted guest with automatically reduced authority. Review the project's scope, shell and web settings, and any paired-desktop execution grants deliberately.

Use device management to revoke trust when a device is lost or no longer yours. Removing a saved device from a list is not a substitute for revoking its credential. Establish a fresh pairing when a previously revoked device needs access again.

See [Desktop fleet](./fleet.md), [Resources](../rem/resources.md), and [Permissions and local data](./security.md).
