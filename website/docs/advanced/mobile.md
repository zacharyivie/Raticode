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

## Turn on pairing

Open **Settings > Developer > Feature Preview** and turn on **Phone and desktop pairing**. Raticode restarts its backend to apply the change. Paired devices then appear in **Settings > Devices**, where you configure transport and inspect paired devices. Use matching, compatible builds on both sides. A backend started without the desktop app reads `RATICODE_DEVICE_EXPERIMENTAL_NETWORK=1` instead.

Start on a private network using the device listener's configured host and port. Do not expose the ordinary desktop HTTP API as a substitute for the device protocol. Relay and notification options have their own configuration and acceptance checks.

## Pair with mutual confirmation

1. Generate a pairing invitation on the desktop.
2. Scan its QR code or use the supported invitation flow on the other device.
3. Compare the displayed identity fingerprint on both devices.
4. Confirm on both sides before accepting trust.

An invitation expires after five minutes. Merely scanning it does not grant a permanent trusted connection. Device identities and protected credentials use the platform's credential storage.

The connection uses pinned device identities and encrypted transport. A relay transports encrypted messages; it is not trusted to impersonate the endpoint. A successful local connection is still separate from proving two-host internet connectivity and sustained release behavior.

## Use your phone away from home with Tailscale

This is optional and off by default. Your phone can reach Raticode on mobile data or another Wi-Fi network through your own [Tailscale](https://tailscale.com) network. Nothing on your computer is opened to the internet, and your router needs no port forwarding. The same pairing, security codes and end-to-end encryption still apply inside the Tailscale tunnel.

You set up Tailscale yourself. Raticode doesn't sign in to it, needs no API key or auth key, and never changes your Tailscale settings, access rules, firewall or router.

1. Create a Tailscale account, or use one you have. Sign in with a provider you trust and turn on two-step sign-in there, because anyone who controls the account can add devices to your network.
2. Install Tailscale on the desktop and sign in. On Linux, run `sudo tailscale up` and open the link it prints. On macOS and Windows, use the Tailscale app.
3. Install Tailscale on your phone and sign in with the same account. Pair the phone at home first; pairing still uses your Wi-Fi.
4. Restrict access. A new Tailscale network lets every device reach every port on every other device. In the Tailscale admin console under **Access controls**, remove the default rule that allows everything and add a grant that lets only your phone reach this computer's Raticode port. Settings shows the rule with this computer's address and port filled in.
5. Don't turn on Funnel, Serve, subnet routes or an exit node on the desktop.
6. In Raticode, open **Settings > Devices**, find **Away from home with Tailscale** and turn on **Also listen on this computer's Tailscale address**. When it shows **Listening**, turn on Tailscale mode in Raticode Mobile. This needs a Raticode Mobile version with Tailscale support.

To check it works, turn Wi-Fi off on your phone, keep Tailscale on, and send Rem a message. With Tailscale off on the phone, Raticode Mobile should not connect away from home.

## Review scope and revoke access

Treat a shared-user phone as another interface for the same user, rather than an untrusted guest with automatically reduced authority. Review the project's scope, shell and web settings, and any paired-desktop execution grants deliberately.

Use device management to revoke trust when a device is lost or no longer yours. Removing a saved device from a list is not a substitute for revoking its credential. Establish a fresh pairing when a previously revoked device needs access again.

See [Desktop fleet](./fleet.md), [Resources](../rem/resources.md), and [Permissions and local data](./security.md).
