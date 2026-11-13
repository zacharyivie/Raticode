---
title: Pair and connect your phone
description: Pair Raticode Mobile with your computer, choose what it may do, and connect at home or through Tailscale.
pageClass: docs-layout
---

# Pair and connect your phone

The phone talks directly to Raticode on your computer, over your Wi-Fi or, if you turn it on, your own Tailscale network. There's no internet relay. Traffic is end-to-end TLS 1.3, pinned to each device's identity.

## Turn on pairing on the computer

1. Open **Settings > Developer > Feature Preview** and turn on **Phone and desktop pairing**. Raticode restarts its backend to apply it. (A backend started without the desktop app reads `RATICODE_DEVICE_EXPERIMENTAL_NETWORK=1` instead.)
2. Open **Settings > Devices** and choose **Set up device pairing**.

The computer needs a private network address on your Wi-Fi to create invitations.

- Router client isolation, guest Wi-Fi or a firewall can keep the phone from reaching it. Allow Raticode's device port through the computer's firewall.
- `RATICODE_DEVICE_HOST` overrides the address Raticode picks.

## Pair the phone

1. On the computer, in **Settings > Devices**, choose **Pair a device**, pick **Phone** and choose **Create QR code**. The code works once and expires after five minutes. **Can't scan? Use the link** shows a link you can copy to the phone instead.
2. On the phone, open Raticode Mobile and, under **Connect to your desktop**, tap **Scan desktop QR**, or paste the link into **Paste invitation** and review it.
3. The phone shows **Pair with …?** with the computer's fingerprint, which the computer calls its security code. Check they match, then tap **Pair**.
4. On the computer, check the phone's code under your devices and choose **Identity matches, confirm pairing**.

Scanning alone grants nothing. Both sides must confirm.

A QR code from **Phone** pairs only a phone, and an invitation link from **Another computer** pairs only another computer. A device that tries to use a code made for the other kind is refused.

<AppScreenshot name="phone-pairing" phone caption="Step 3 on the phone. This one was paired with a test desktop, so it shows an emulator address." />

## Choose what the phone can do

Each paired phone has its own switches in **Settings > Devices**:

| Switch | What it allows |
| --- | --- |
| **Sync desktop threads with this phone** | Threads, history, projects and the model list sync to the phone. On by default for new phones. |
| **Allow approvals and swarm controls** | Answer workflow and swarm approvals, and pause, resume, stop and message swarms. Needs thread sync. |
| **Use Raticode from this phone** | Rem's chat and tasks, Organizations, the global board, Files, Source control, workflows, desktop settings, usage and the developer log. Includes approvals and swarm controls. Needs thread sync. |
| **Let this phone wake this computer** | Shows **Wake computer** on the phone. Needs the two switches above and a Linux computer. |

With **Use Raticode from this phone** on, the phone counts as you at the computer. It can approve what you can approve there, including organization requests that run workers without a sandbox or add secrets, starting swarms and organizations whose agents have full access, and Bypass or Full access on threads. Raticode doesn't add extra phone-only checks for these.

## Suspicious requests lock the phone

If someone else gets hold of your phone, Raticode helps keep your computer safe. **Lock a phone that sends a suspicious request**, in **Settings > Devices** under your devices, is on by default. With it on, the computer checks every request from a paired phone before Rem, a worker or any model sees it. The check uses fixed rules on the computer, not a model, so the same request always gets the same answer.

It stops requests that:

- ask for passwords, API keys, tokens, private keys, seed phrases or other credentials ("send me my passwords")
- show or send a file that usually holds credentials, such as `~/.ssh/id_ed25519`, `.env`, `auth.json` or `~/.aws/credentials` ("cat ~/.ssh/id_rsa", "show me the .env file")
- ask for signing keys and certificates, by name or by description: Apple Developer ID files (`devid.key`, `devid.p12`), `.p12`, `.pfx`, `.pem`, `.key`, `.p8`, `.csr`, `.keystore` and `.jks` files, provisioning profiles, keychains, SSH and GPG keys and wallet files ("send me the devid files", "export the p12", "give me the signing cert")
- insist after Rem held files back as possibly secret ("send them anyway", "do it anyway", "I said send them", "override"), within an hour of that refusal, or ask Rem to get around its safeguards at any time
- print environment variables or a keychain
- carry something to run: a download piped into a shell (`curl … | sh`), a reverse shell, encoded content decoded and run, a key added to `authorized_keys`, or a long encoded blob
- mix several things the phone has never sent before, such as shell commands and a link to a bare IP address, once the phone has sent at least 25 requests

Ordinary requests pass, including ones that only mention these things: "show me the report", "send me the screenshots", "send me the logs", "add .env to .gitignore", "how do I export the p12?" or "how do I rotate my API keys?". "Send them anyway" on its own is ordinary too; it's only stopped right after Rem held something back. **Pictures, screenshots, files, Share to Rem and screens you share with Rem are never checked or blocked.**

Rem never sends key or credential material to a phone, even when the request names the file's full path: key and certificate files (`.key`, `.p12`, `.pem`, `.p8`, `.csr`, `.jks`, keychains, provisioning profiles), Developer ID files, SSH keys, `.env` and sign-in files such as `auth.json`, or anything in a credential folder such as `~/.ssh`. If a phone's request leads Rem to hold back one of those files, for example "send me everything in Documents" when Documents holds `devid.key`, Rem has seen that request but sends none of those files, and the phone is locked the same way. Other files Rem holds back, such as Raticode's own logs or a report with "credential" in its name, don't lock the phone; insisting on them right afterwards does.

When a request is stopped:

- Rem never sees it (or, when Rem held files back, sends none of them), and the phone is locked. The computer refuses everything from it until you unlock it.
- The phone shows **Phone locked** and how to unlock it. An older Raticode Mobile shows the same words as an error.
- The computer shows a notice at the top of the window, and **Settings > Devices** shows the phone as locked with the reason. The desktop log gets an entry naming the phone and the reason, without the request's text.
- The request's text is kept for you to review under **Review flagged requests**. It's stored encrypted in the computer's device records, which only your account can read. **Delete these records** removes it.

To unlock, choose **Unlock…** on the locked phone in **Settings > Devices**, then **Unlock phone**. Only you can do this at the computer: Rem, its workers and agent tokens can't unlock a phone. If you didn't send the request, **Revoke** the phone instead.

Turning the switch off stops the checks but leaves a locked phone locked. An older computer doesn't have the check.

## Staying connected

- The app connects to its saved computer when you open it and reconnects when Wi-Fi returns. If the computer's address changes or is blocked, use **More › Connection › Set desktop address**.
- **Enable background sync** (under **Background & notifications** in the connection settings) keeps the connection open while the app is in the background, with a visible Android notification. Without it, leaving the app disconnects and opening it reconnects. Alerts and replies only arrive while connected; there is no push service.
- Each phone's synced history on the computer is capped at 64 MB, and a single thread at 8 MB or 10,000 messages. Threads past those limits stay off the phone.

## Away from home with Tailscale

Optional, and off by default. Through your own [Tailscale](https://tailscale.com) network, the phone can reach Raticode on mobile data or another Wi-Fi network.

- Nothing on your computer is opened to the internet, and your router needs no port forwarding.
- Pairing, security codes and end-to-end encryption still apply inside the tunnel.
- You set up Tailscale yourself. Raticode doesn't sign in to it, needs no API key or auth key, and never changes your Tailscale settings, access rules, firewall or router.

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

Once the phone is paired, read [Rem on the phone](./rem.md) and [Projects, boards and workflows on the phone](./workspace.md). To protect the app itself, see [App lock and Child lock](./locks.md).
