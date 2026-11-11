---
title: Pair and connect your phone
description: Pair Raticode Mobile with your computer, choose what it may do, and connect at home or through Tailscale.
pageClass: docs-layout
---

# Pair and connect your phone

The phone talks directly to Raticode on your computer over end-to-end TLS 1.3, pinned to each device's identity. It connects over your Wi-Fi, or through your own Tailscale network if you turn that on. There is no internet relay.

## Turn on pairing on the computer

1. Open **Settings > Developer > Feature Preview** and turn on **Phone and desktop pairing**. Raticode restarts its backend to apply it. (A backend started without the desktop app reads `RATICODE_DEVICE_EXPERIMENTAL_NETWORK=1` instead.)
2. Open **Settings > Devices** and choose **Set up device pairing**.

The computer needs a private network address on your Wi-Fi to create invitations. Router client isolation, guest Wi-Fi or a firewall can keep the phone from reaching it; allow Raticode's device port through the computer's firewall. `RATICODE_DEVICE_HOST` overrides the address Raticode picks.

## Pair the phone

1. On the computer, in **Settings > Devices**, choose **Pair a device**, pick **Phone** and choose **Create QR code**. The code works once and expires after five minutes. **Can't scan? Use the link** shows a link you can copy to the phone instead.
2. On the phone, open Raticode Mobile and, under **Connect to your desktop**, tap **Scan desktop QR**, or paste the link into **Paste invitation** and review it.
3. The phone shows **Pair with …?** with the computer's security code. Check it matches the code on the computer, then tap **Pair**.
4. On the computer, check the phone's code under your devices and choose **Identity matches, confirm pairing**.

Scanning alone grants nothing. Both sides must confirm.

## Choose what the phone can do

Each paired phone has its own switches in **Settings > Devices**:

| Switch | What it allows |
| --- | --- |
| **Sync desktop threads with this phone** | Threads, history, projects and the model list sync to the phone. On by default for new phones. |
| **Allow approvals and swarm controls** | Answer workflow and swarm approvals, and pause, resume, stop and message swarms. Needs thread sync. |
| **Use Raticode from this phone** | Rem's chat and tasks, Organizations, the global board, Files, Source control, workflows, desktop settings, usage and the developer log. Includes approvals and swarm controls. Needs thread sync. |
| **Let this phone wake this computer** | Shows **Wake computer** on the phone. Needs the two switches above and a Linux computer. |

## Staying connected

- The app connects to its saved computer when you open it and reconnects when Wi-Fi returns. If the computer's address changes or is blocked, use **More › Connection › Set desktop address**.
- **Enable background sync** (under **Background & notifications** in the connection settings) keeps the connection open while the app is in the background, with a visible Android notification. Without it, leaving the app disconnects and opening it reconnects. Alerts and replies only arrive while connected; there is no push service.
- Each phone's synced history on the computer is capped at 64 MB, and a single thread at 8 MB or 10,000 messages. Threads past those limits stay off the phone.

## Away from home with Tailscale

This is optional and off by default. Your phone can reach Raticode on mobile data or another Wi-Fi network through your own [Tailscale](https://tailscale.com) network. Nothing on your computer is opened to the internet, and your router needs no port forwarding. Pairing, security codes and end-to-end encryption still apply inside the tunnel.

You set up Tailscale yourself. Raticode doesn't sign in to it, needs no API key or auth key, and never changes your Tailscale settings, access rules, firewall or router.

1. Create a Tailscale account, or use one you have. Sign in with a provider you trust and turn on two-step sign-in there, because anyone who controls the account can add devices to your network.
2. Install Tailscale on the computer and sign in. On Linux, run `sudo tailscale up` and open the link it prints. On macOS and Windows, use the Tailscale app.
3. Install Tailscale on your phone and sign in with the same account. Pair the phone at home first; pairing still uses your Wi-Fi.
4. Restrict access. A new Tailscale network lets every device reach every port on every other device. In the Tailscale admin console under **Access controls**, remove the default rule that allows everything and add a grant that lets only your phone reach this computer's Raticode port. Settings shows the rule with this computer's address and port filled in.
5. Don't turn on Funnel, Serve, subnet routes or an exit node on the computer.
6. In Raticode on the computer, open **Settings > Devices**, find **Away from home with Tailscale** and turn on **Also listen on this computer's Tailscale address**. When it shows **Listening**, turn on **Connect through Tailscale** in the phone's connection settings.

To check it works, turn Wi-Fi off on your phone, keep Tailscale on, and send Rem a message. On Wi-Fi the phone still tries the computer directly first. With Tailscale off on the phone, it doesn't connect away from home.

## Wake the computer

With **Let this phone wake this computer** on, **Wake computer** appears in the phone's connection page, and on other pages after a connection attempt fails. One tap sends a Wake-on-LAN signal over Wi-Fi; the phone then reconnects as usual. It only says the signal was sent, because nothing confirms it worked.

It needs a Linux computer whose firmware allows Wake-on-LAN, and the phone on the same Wi-Fi network. It doesn't work over Tailscale. The signal is unencrypted by design and holds only the network card's hardware address.

## Unpair, revoke or forget

- **Unpair** on the computer removes the pairing. The phone can pair again with a new QR code.
- **Revoke** also blocks the phone from reconnecting or pairing again until you choose **Revoked devices › Remove from revoked list**. Use it for a lost phone.
- **Forget this desktop** on the phone deletes the phone's keys and local history for that computer.

## Troubleshooting

- **Pairing waits at authentication:** check that **Phone and desktop pairing** is on and the computer's firewall allows the device port, before reinstalling anything.
- **Can't connect:** make sure both devices are on the same Wi-Fi (not a guest network), then try **Set desktop address**.
- **New threads don't appear:** the phone's 64 MB history share may be full; archive or delete old threads on the computer.
- **A feature says to update the computer:** update Raticode on the computer; the phone and desktop are released separately.
