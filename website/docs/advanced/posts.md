---
title: Social posts
description: Rem, tasks and workflows propose social posts; you approve each one; Raticode publishes it to Discord, X, LinkedIn, a Facebook Page or YouTube at its time.
pageClass: docs-layout
---

# Social posts

**Posts** is a review queue for social media. Rem, Rem's tasks and your workflows can draft posts and propose a time for them. Nothing goes out until you approve the exact text in the Posts plugin. At the scheduled time Raticode sends it to every account the post targets.

::: warning Experimental, new in 0.6.0
Posts is experimental and hasn't been fully tested yet, so how it works may change in a later release. The app marks it **Experimental** in the sidebar, the Posts header, Settings and the phone. Its approval rules are the same as described here: nothing goes out until you approve it.

Posts is off by default. Turn it on in **Settings > Developer > Feature Preview > Posts** to show **Posts** in the sidebar. Turning it off hides the plugin and keeps your posts and accounts. It doesn't stop publishing: posts you already approved still go out at their times, and Rem can still propose. To stop sends, use **Pause all publishing** before you turn Posts off.
:::

## What it can post to

| Platform | How Raticode posts | What it sends |
| --- | --- | --- |
| **Discord** | A channel webhook | Text, plus images or videos up to 10 MB in total |
| **X** | The X API with your own developer app's API keys (or an OAuth 2.0 token) | Text and links. X charges per post. |
| **LinkedIn** (your personal profile) | Share on LinkedIn with your own developer app | Text and links. You tap **Publish** at the scheduled time, see [below](#linkedin-tap-to-publish). |
| **Facebook Page** | The Pages API with a Page access token | Text and links |
| **YouTube** | A video upload with your own Google Cloud app | One video, with its title and description |
| **Patreon**, **Buy Me a Coffee**, **TikTok** | No API. Raticode writes the draft and you paste it. | Whatever you paste |

Patreon and Buy Me a Coffee have no API for creating posts. TikTok keeps posts from unaudited apps private, and its audit turns down tools that post to your own accounts. For these three, a post's card has **Copy text**, **Open Patreon**, **Open Buy Me a Coffee** or **Open TikTok** (the site's post page) and **Mark posted**, where you paste the link to the post you made.

LinkedIn Company Page posts need LinkedIn's Community Management API, which only accepts registered organizations, so they aren't supported. Bluesky and Mastodon aren't supported.

## Connect an account

Each platform needs a developer app or webhook that **you** create. Raticode doesn't ship its own app for any platform: a client secret inside a desktop app can't stay secret, and several platforms only let an app post to its owner's accounts without a review.

Open **Posts**, then **Accounts**. With no accounts yet, the page starts with **Connect Discord**, the quickest one to set up. Otherwise pick a platform in the **Connect an account** panel. Each platform shows:

1. Where there's more than one way, a **Connect with** choice. YouTube, LinkedIn and Facebook Page offer **Sign in** (the default) or **Paste a token**. X offers **API keys** (the default), **Sign in (OAuth 2.0)** or **Paste OAuth 2.0 token**. Discord has one way, a webhook. Then numbered steps for creating the login on that platform, a tip, and a link to the platform's developer docs.
2. A **Name it** field for what Raticode calls the account (only you see it), and one single-line field per login detail. Tokens and secrets are masked; the eye button shows what you pasted. Optional fields are marked.
3. **Connect and test**. Raticode checks the fields first (for example, that a Discord address is a real webhook address and a Page ID is only numbers) and tells you what to fix. Then it saves the login to the system keychain under **Raticode social accounts**, one entry per account, and asks the platform who the login belongs to. Testing never posts anything.

If the platform accepts the login, the panel says so and names the account the platform reported. If the test fails, the account is still saved: paste a corrected login and save again, choose **Test again**, or fix it later from the account's card. Raticode never shows a saved login again.

None of the ways needs a website or web server of yours. Discord takes a webhook address and X's API keys come from its developer console, so neither involves a sign-in page. LinkedIn, Facebook and YouTube only hand out posting tokens through an OAuth sign-in: **Sign in** runs it for you in your browser (below), and **Paste a token** keeps the older way of signing in with the platform's own tool (LinkedIn's Token Generator, Meta's Graph API Explorer, Google's OAuth 2.0 Playground) and pasting the result.

### Sign in through your browser

**Sign in** works for YouTube, LinkedIn, Facebook Page and X's OAuth 2.0. Discord has no sign-in: it posts through a channel webhook, so there's no account to sign in to.

You still create your own developer app on the platform (Raticode ships no client ID or secret) and paste its client ID, plus the client secret where the platform needs one. Then:

1. Click **Sign in with Google**, **Sign in with X**, **Sign in with LinkedIn** or **Sign in with Facebook**. Raticode starts listening on `127.0.0.1` on this computer, and opens the platform's consent page in your default browser.
2. Sign in and allow access on the platform's page. Your password only ever goes to that page.
3. The platform sends your browser back to the local address with a one-time code. The page says you can close the tab. Raticode checks the reply, trades the code for tokens and saves them in the keychain entry a pasted login uses, then tests the account like **Connect and test** does.

While it waits, the panel shows the time left, **Open the page again**, **Copy sign-in link** (for when the browser didn't open; use a browser on the same computer) and **Cancel sign-in**.

How it stays safe:

- The listener binds the loopback address only, so other computers can't reach it. It takes one sign-in reply, then closes. It also closes when you cancel, leave the panel, or after 5 minutes.
- Each sign-in uses PKCE and a random `state` value. A reply whose `state` doesn't match ends the sign-in with nothing saved, and a code taken from the address can't be redeemed without the PKCE verifier only Raticode holds.
- Only you can start a sign-in. Rem, tasks, workflows and the phone can't, and Raticode never shows the saved tokens or client secret again.
- Signing in connects an account. It doesn't approve or publish anything: every post still waits for your approval.

The redirect address depends on the platform:

| Platform | Redirect address to register in your app | Why |
| --- | --- | --- |
| YouTube | None | Google Desktop app clients accept a loopback address on any port, so Raticode picks a free one each time. |
| X | `http://127.0.0.1:47689/callback` | X matches callback URLs exactly and wants `127.0.0.1`, not `localhost`. |
| LinkedIn | `http://127.0.0.1:47689/callback` | LinkedIn matches redirect URLs exactly. |
| Facebook Page | `http://localhost:47689/callback` | Meta matches redirect URIs exactly, and accepts `localhost` addresses while the app is in development mode. Raticode holds the port on both `127.0.0.1` and `[::1]`, so no other program can take the callback on the address your browser tries first. |

`47689` is the default port for the platforms that need a fixed one. If another program already uses it, the sign-in says so; choose another **Port** in the form and register that address instead. The panel shows the exact address with a copy button. The account remembers the port, the client ID and the secret, so signing in again later is one click.

::: warning New in 0.6.0, and not yet tried against the live platforms
**Sign in** is new in 0.6.0. Like the rest of Posts, it's experimental. It was built and tested against stand-in platforms only. Whether LinkedIn and Facebook accept these loopback addresses for your app hasn't been confirmed: LinkedIn's docs say redirect URLs are added "via HTTPS", and Meta's `localhost` exemption is for apps in development mode. If a platform refuses the address, use **Paste a token**. For the same reason, X, Google and Facebook may name their settings differently from the steps here.
:::

| Platform | Sign in with | Or paste | Lasts |
| --- | --- | --- | --- |
| Discord | No sign-in | Webhook URL | Until you delete the webhook |
| X | Client ID, and the client secret for a confidential client (OAuth 2.0) | API key, API secret, access token and access token secret (the default) | Keys: until you regenerate them. OAuth 2.0: about 2 hours, and Raticode renews it with the refresh token |
| LinkedIn | Client ID and client secret | Access token from the Token Generator | 60 days |
| Facebook Page | App ID and app secret | Page access token and Page ID | No expiry, when made from an extended user token (sign-in does this for you) |
| YouTube | Client ID and client secret of a Desktop app client | Refresh token, client ID and client secret | Until revoked, with the consent screen **In production** |

### Discord

1. In Discord, open the channel's **Edit Channel > Integrations > Webhooks** and create a webhook. Use a private test channel first.
2. Copy the webhook URL and paste it into the account form.

The URL contains the webhook's token, so treat it like a password. See Discord's [Execute Webhook](https://discord.com/developers/docs/resources/webhook) reference.

### X

X has two ways in. **API keys** is the default and the simplest: it needs no sign-in page and no callback address.

**API keys (default)**

1. In the [X developer console](https://docs.x.com/fundamentals/developer-apps), create an app and buy post credits.
2. In the app's **User authentication settings**, set **App permissions** to **Read and write**. A read-only app can't post. If X asks for a callback URI and website, `http://127.0.0.1` and your own site will do; Raticode doesn't use them.
3. Open **Keys and tokens**. Copy the **API key** and **API secret** (X also calls them the consumer key and secret), then generate the **access token** and **access token secret** for your account. If you changed the permissions, generate the access token and secret again, because tokens keep the permissions they were made with.
4. Paste all four. Raticode signs each request with them (OAuth 1.0a). They don't expire; if you regenerate them in the console, the account shows **Needs reconnect** until you paste the new ones.

If X refuses a post with "not permitted", the post's problem says to switch the app to **Read and write** and regenerate the access token and secret. These keys post as the account that owns the developer app.

**Sign in (OAuth 2.0)**

1. In the app's **User authentication settings**, turn on OAuth 2.0. Choose **Native App** for a public client (no secret), or **Web App** if you want a client secret, and set **App permissions** to **Read and write**.
2. Add `http://127.0.0.1:47689/callback` as a callback URL (or the address the panel shows for your port).
3. Paste the OAuth 2.0 **client ID**, and the client secret for a Web App, and click **Sign in with X**. Raticode asks for the `tweet.read`, `tweet.write`, `users.read` and `offline.access` scopes and keeps the refresh token, so it renews the two-hour token on its own.

**Paste OAuth 2.0 token**

Choose **Paste OAuth 2.0 token** if you already run X's OAuth 2.0 sign-in yourself. X's console doesn't generate these tokens.

1. In the app's **User authentication settings**, turn on OAuth 2.0 and add a callback URL. X needs an exact match; for a tool on your computer use `http://127.0.0.1` with a port, not `localhost`.
2. Run X's [authorization code flow with PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code) with the `tweet.read`, `tweet.write`, `users.read` and `offline.access` scopes, for example with an OAuth tool or script.
3. Paste the access token, and the refresh token and client ID so Raticode can renew the token when it expires (about every two hours). Add the client secret if your app is a confidential client.

X accounts connected with a token before API keys were added stay on OAuth 2.0. **Replace login** opens on the account's current way; picking another way replaces the whole login. The account card names the way it uses.

X bills per post: $0.015 a post and $0.20 a post with a link, according to [X's pricing page](https://docs.x.com/x-api/getting-started/pricing) as of November 2026. X says prices can change. Each card shows the cost, and the account has a monthly cap on posts. Set a spending limit in X's console as well.

### LinkedIn

1. Create an app in the [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) and add the **Share on LinkedIn** and **Sign in with LinkedIn using OpenID Connect** products. LinkedIn may ask you to link the app to a LinkedIn Page.
2. **Sign in** (default): on the app's **Auth** tab, add `http://127.0.0.1:47689/callback` under **Authorized redirect URLs**. Paste the client ID and primary client secret and click **Sign in with LinkedIn**. Raticode asks for the `openid profile w_member_social` scopes.
3. **Paste a token**: get an access token with the same scopes from the portal's Token Generator, paste it and click **Connect and test**.

Either way, Raticode reads who you are from LinkedIn and saves your member ID as the post author.

LinkedIn's tokens last 60 days and self-serve apps get no refresh token. The account shows **Reconnect soon** 14 days before the token expires and **Needs reconnect** after. An account you signed in with shows **Sign in again** on its card, which reuses the saved app; otherwise paste a new token. LinkedIn's [Share on LinkedIn](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin) page describes the product.

### Facebook Page

1. Create a Meta app with Facebook Login and the Pages API permissions (`pages_show_list`, `pages_manage_posts` and `pages_read_engagement`). People with a role on your app can use it without App Review. See Meta's [Pages API](https://developers.facebook.com/docs/pages-api) docs.

**Sign in** (default): in **Facebook Login > Settings**, add `http://localhost:47689/callback` to **Valid OAuth Redirect URIs**. Paste the **App ID** and **App secret**, optionally the Page ID, and click **Sign in with Facebook**. Raticode trades the code for a user token, extends it with your app secret (Meta only allows that step with the secret), and reads your Pages. If you manage more than one Page and didn't give a Page ID, the panel asks which one to post to. It saves only that Page's token, which doesn't expire, plus the app ID and secret for signing in again. Meta's [manual login flow](https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow) describes the steps.

**Paste a token**:

2. In Meta's Graph API Explorer, generate a user token for your app with those permissions and extend it in the Access Token Debugger. Then request `me/accounts` and copy your Page's `access_token`. Meta says a Page token made from a long-lived user token has no expiration date ([long-lived tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived)).
3. Paste the token with the Page ID.

Meta says data an app creates in Development mode can be visible only to people with a role on the app. Check that a test post shows up for a logged-out visitor before you rely on it.

### YouTube

1. In Google Cloud, create a project and turn on the YouTube Data API v3. Set the consent screen to **In production**: in **Testing**, Google cancels your sign-in after seven days.

**Sign in** (default): create an OAuth client of type **Desktop app**. Desktop clients accept a loopback address on any port, so there's no redirect URI to add ([OAuth 2.0 for desktop apps](https://developers.google.com/identity/protocols/oauth2/native-app)). Paste its client ID and client secret, choose whether videos are public or unlisted, and click **Sign in with Google**. Raticode asks for the `youtube.upload` scope and keeps the refresh token.

**Paste a token**:

2. Create an OAuth client of type **Web application** and add `https://developers.google.com/oauthplayground` as an authorized redirect URI. Google's OAuth 2.0 Playground needs that address; it's Google's page, not a server of yours.
3. In the [OAuth 2.0 Playground](https://developers.google.com/oauthplayground), open the settings, tick **Use your own OAuth credentials** and enter your client ID and secret. Authorize the `youtube.upload` scope, exchange the code for tokens, and paste the refresh token, client ID and client secret.
4. Choose whether videos are **public** or **unlisted**.

YouTube uploads need a Google OAuth sign-in: Google says apps "must use the OAuth 2.0 protocol" to create resources such as videos ([Moving to OAuth 2.0](https://developers.google.com/youtube/v3/guides/moving_to_oauth)), so there's no API-key way.

Raticode uploads videos up to 256 MB. Titles are up to 100 characters and descriptions up to 5,000 bytes. See the [YouTube Data API](https://developers.google.com/youtube/v3) docs.

### Account cards

Each connected account is a card with the platform's mark, its name (click the pencil to rename it), the name the platform reported, a status in words (**Connected**, **Reconnect soon**, **Needs reconnect** or **Off**) and when it last posted. The card has:

- **Posting on**: off means nothing is sent to it. At their time, its posts move to **Problems** as **Held at a cap** with "NAME is off." Turning it back on doesn't release them: choose **Publish now** on each.
- **Only my own posts**: the account only sends posts you wrote yourself. Proposals from Rem, tasks and workflows can't target it. This suits a Discord server you aren't announcing yet. (The `/api/social` accounts data calls this `quiet`.)
- **Posts a day, at most** and **Posts a month, at most**: steppers for the account's caps, 10 a day and 100 a month to start, counted over a rolling 24 hours and 30 days. A post past a cap is **held** until you choose what to do.
- **Posts may ping** (Discord): **People**, **Roles** and **@everyone** are all off by default.
- **New videos are** (YouTube): **Public** or **Unlisted**.
- **Send without a tap** (LinkedIn): off by default, see [below](#linkedin-tap-to-publish).
- **Test**: asks the platform who the login belongs to and shows the answer on the card.
- **Replace login**: opens the Connect panel for this account. Fields you leave empty keep their saved values. An account you signed in with opens on **Sign in**, and when it shows **Reconnect soon** or **Needs reconnect** the button reads **Sign in again**: one click reuses the saved client ID, secret and port.
- **Disconnect**: asks first, then deletes the account and its keychain entry. Posts scheduled for it fail at their time with "The account was removed.", and a waiting proposal for it can't be approved. **Duplicate** a post to send it to another account.

### Publishing rules

Under the accounts, **Publishing rules** apply to every post and save as you change them:

- **Trusted link sites**: links to these sites go through without a warning; links anywhere else get a "check this link" warning on the post. Type a site and press Enter to add it, or click × to remove one. `raticode.dev` and `github.com` are there to start.
- **If a post runs late**: how late a post may still go out if your computer was asleep or Raticode was closed at its time: 15 minutes, 30 minutes, 1 hour, 2 hours (the default), 6 hours, 1 day, or an exact number of minutes up to 7 days. Later than that, the post becomes **missed**. Each post can change this in its editor.
- **Pause everything**: the same switch as **Pause all publishing** at the top of Posts.

## Write a post

**New post** opens the editor. **Duplicate**, in a card's **More** menu, opens it with a copy of that post's accounts, text, links and attached files, with no time set. Either way, saving puts the post in **Needs review**.

- **Start from a template**: a new post's editor lists your [templates](#templates) and the starters. Pick one, fill in its blanks, and **Use this template** fills in the title and each platform's text. Platforms you have no account for are left out, and the editor names them. Blanks you leave empty stay in the text as `{name}`.

- **Post to**: click the accounts the post goes to. Patreon, Buy Me a Coffee and TikTok are marked **paste**: Raticode writes the draft and you post it yourself.
- **Text for each platform**: one tab per account, each with its own text, link and options (a video title for YouTube; who can see it and a link preview title for LinkedIn). A new account starts with the text of the tab you were on, and **Use this text and link for every platform** copies it to the rest. A bar under the text counts characters against the platform's limit (X counts each link as 23), and a tab whose text is too long says so.
- **Images or video** (**Video file** on YouTube): type a file's path and click **Attach**. Each platform's text takes up to 10 files: JPEG, PNG, GIF or WebP pictures up to 10 MB, and MP4, MOV or WebM videos up to 256 MB. Today only Discord and YouTube send media; a post with media for X, LinkedIn or Facebook can't be approved until you remove it, and a YouTube post needs a video.
- **Preview**: how the post reads on the selected platform, with the account name, line breaks, link and attachments. For LinkedIn, Facebook and YouTube it marks where the feed cuts the text with **see more** (about 3 lines or 210 characters on LinkedIn, 5 lines or 480 on Facebook, 3 lines or 150 on YouTube), so you can put the hook before it. It's a guide, not an exact copy of the platform's layout.
- **When**: **As soon as I approve it**, or **At a set time** with a date, time and time zone. The time is read in the zone you pick, and the editor shows it in your own time when the zones differ. Quick picks offer in an hour, tomorrow at 9 AM and Monday at 9 AM.
- **Good times**: with **At a set time**, the editor offers up to three of the next [good times](#best-times) for the chosen accounts in the chosen time zone, at most one a day, skipping times within 3 hours of your other planned posts to the same accounts. Click one to use it; hover it to see why. It's general advice, not based on your audience, and the editor says so.
- **If Raticode is late, still post up to**: this post's late limit, see [Publishing rules](#publishing-rules).

- **Save as template**: keeps the title and each platform's text and link, with any `{blanks}` you wrote, as a template. It doesn't save or change the post.

**Note to yourself** (optional) is shown on the card as the reason for the post. **Save for review** saves the post into **Needs review**. `Ctrl+Enter` (`Cmd+Enter` on a Mac) saves; `Escape` closes without saving. Once you've changed anything, `Escape` asks first (**Keep editing** goes back to the draft as it was) and a click outside the editor leaves it open. New in 0.6.1.

## Review and approve

**Posts** shows the next seven days at the top, then five lanes:

| Lane | Holds |
| --- | --- |
| **Needs review** | Proposals waiting for you |
| **Scheduled** | Approved posts waiting for their time |
| **Due now** | LinkedIn posts waiting for your **Publish** tap |
| **Published** | Posts that went out, with links to them |
| **Problems** | Posts that failed, were missed, need a reconnect, hit a cap or have an unknown result |

Each card shows a preview per account with a character count and a **see more** marker where the feed cuts the text, every link's full address, media, who proposed it and why, the pages it drew on, and warnings (see [Safety](#safety)). Notes about its time say when another post goes to the same account within 3 hours, or when it's set for midnight to 6 AM in its time zone. Rem's revisions show what changed since you last saw the post.

**Columns** shows the lanes side by side (stacked on a narrow window); **List** shows one list with a filter for each lane and **Closed** (declined, cancelled and expired posts). Raticode remembers your choice. **Search posts** filters by title, text and account.

A card in **Needs review** has **Approve and schedule** (or **Approve and publish now** when it has no time) and **Edit**. Its **More** menu has **Approve and publish now**, **Request changes**, **Duplicate** and **Decline**. An approved card has **Edit or change time**, and **More** has **Duplicate** and **Cancel post**.

To approve several posts at once, tick each one's checkbox in **Needs review**. The bar that appears offers **Approve**, which lists the posts, their times and accounts and asks you to confirm, and **Decline**. There is no select-all: you tick each post yourself. Posts with a warning or with text over a platform's limit are left out of a group approval, so each gets its own look.

How approval works:

- Only you can approve, by clicking in Posts. Rem, tasks, workflows, agent tokens and the CLI can't. A group approval sends one approval per post, each tied to the version on your screen.
- An approval covers the exact content: every account's text, link, media, options, and the time. If anything changes, even one character, the post goes back to **Needs review**. Your own edits do too, so you approve what you finally see.
- Every option a post sends is on its card: a LinkedIn link preview title and who can see the post, a Discord thread, a video or Patreon title. Raticode doesn't accept options that no screen shows, so a LinkedIn link preview carries only its title, never a hidden description.
- The publisher checks the approval again at the moment it sends. If you edit or cancel a post while it's about to go out, it isn't sent. While a send is actually in flight, **Cancel** is refused with "A send is in progress. Try again in a moment."
- Once any part of a post has gone out, the post can't change: an edit is refused with "Part of this post already went out, so it can't change."
- Telling Rem "okay" in chat doesn't approve a post, even though it can approve Organizations requests. Self-work's **auto-approve proposals** never applies to posts.
- On a paired phone with **Use Raticode from this phone** on, **More › Posts** shows the same queue and takes the same decisions, each with a second tap. That is still your approval, pinned to the version the phone showed. The phone doesn't show media or options such as titles, so for a post that has them the confirmation warns that part of the post isn't shown on the phone. See [Social posts on the phone](../mobile/workspace.md#social-posts).

## Templates

A template is a post you reuse: a title and a text per platform, with blanks in braces such as `{version}` or `{what changed}`. Filling one in only makes a proposal or a draft. It goes through review and your approval like any other post.

Three starters come with Raticode:

| Starter | For | Platforms |
| --- | --- | --- |
| **New release** (`starter-release`) | Announcing a version and what's in it | X, LinkedIn, Discord |
| **How-to or blog post** (`starter-how-to`) | Sharing a guide or article you wrote | X, LinkedIn, Facebook |
| **Weekly update** (`starter-weekly`) | A short build-in-public update | X, LinkedIn, Discord |

**Posts > Templates** lists your templates, then the starters, with each one's platforms, blanks and first text. **Use** opens a new post from it. **Delete** removes one of yours after asking; starters can't be deleted. To make one, write a post with blanks in braces and choose **Save as template** in the editor, or use a starter and save your own version.

Only you save and delete your own templates. Rem, tasks and workflows can read them and propose from them. A blank starts with a letter and has up to 40 letters, digits, spaces, `_` or `-`, so `{}` and code like `{ x }` stay as they are. You can keep up to 100 templates.

## Best times

The editor, Rem's proposals and `Raticode social times` suggest the next good times to post: at most one a day, in your time zone, skipping times within 3 hours of another post planned for the same account. A time that suits every platform in the post comes first.

| Platform | Usually best (your audience's local time) |
| --- | --- |
| LinkedIn | 10 AM to 1 PM, Tuesday to Thursday |
| X | 9 AM to noon, Tuesday to Thursday |
| Facebook | 9 AM to noon on weekdays |
| YouTube | Noon to 3 PM on weekdays |
| TikTok | Noon to 6 PM on weekdays |
| Discord, Patreon, Buy Me a Coffee | 10 AM to 2 PM on weekdays |

These windows are general guidance from published engagement studies, such as [Sprout Social's best times to post](https://sproutsocial.com/insights/best-times-to-post-on-social-media/), not your own audience's data. Try a few times and keep what works.

Cards in Posts and on the [phone](../mobile/workspace.md#social-posts) also note timing problems before you approve: a post going to the same account within 3 hours of another planned post, and a post set for midnight to 6 AM in its time zone.

## How Rem, tasks and workflows propose

**Rem** (in chat and in its tasks) has a `social` tool set with `social_rules`, `propose_post`, `revise_post`, `list_posts` and `read_post`. It can read your enabled accounts, their limits and rules, propose a post, and revise its own proposals until you approve them. It can't approve, publish, delete, change accounts, save templates or see any login.

`social_rules` also gives Rem your [templates](#templates) and the next [good times](#best-times) for your accounts in this computer's time zone. Unless you ask for a time, Rem picks one of those. Ask "announce 0.6 with my release template" and Rem proposes from the template, filling in each blank; the proposal waits for you like any other.

**Workflows and scripts** propose with the CLI, for example from a `bash-command` node:

```bash
Raticode social propose --file post.json
```

```json
{
  "title": "0.6.0 is out",
  "scheduled_at": "2026-12-01T10:00:00-07:00",
  "timezone": "America/Denver",
  "late_window_minutes": 120,
  "rationale": "Release notes for 0.6.0",
  "sources": [{"url": "https://github.com/zacharyivie/Raticode/releases", "title": "Releases"}],
  "variants": [
    {"platform": "x", "text": "Raticode 0.6.0 is out: https://raticode.dev"},
    {"platform": "discord", "text": "0.6.0 is out. Notes: https://raticode.dev"},
    {"platform": "linkedin", "text": "Raticode 0.6.0 is out.", "link": "https://raticode.dev",
     "options": {"article_title": "Raticode 0.6.0"}}
  ]
}
```

Times need an offset or `Z` for UTC; `timezone` only sets how the time is shown. A variant names an `account_id`, or a `platform` when you have exactly one enabled account there (with more than one, the proposal is refused and asks you to name the `account_id`). `media` takes a list of `{"path": "..."}` files, which Raticode copies into its data folder when you propose, so changing the file later can't change what you approved. Rem, tasks, workflows and the CLI can attach files only from a project open in Raticode, outside hidden folders such as `.git`; anything else is refused, because the review card shows a file's name, type and size rather than the picture. Raticode reads a file's type from its contents, not its name, and removes location and other EXIF data from JPEG, PNG and WebP pictures (not from videos).

To fill in a template instead, send `template` and `fields` in place of `variants`:

```json
{
  "template": "starter-release",
  "fields": {"product": "Raticode", "version": "0.6.0", "headline": "Posts got templates.",
             "change one": "Templates", "change two": "Best-time hints", "link": "https://raticode.dev"},
  "scheduled_at": "2026-12-01T10:00:00-07:00"
}
```

Every blank needs a value. Platforms in the template with no enabled account are left out.

`Raticode social list`, `Raticode social show ID`, `Raticode social accounts`, `Raticode social templates` and `Raticode social times --for x,linkedin` read the queue, accounts, templates and suggested times. There is no command to approve or publish.

Limits on each source: 10 open proposals at a time and 20 a day. Rem's chat, all of Rem's tasks (self-work included) and Organizations workers count as one source, `rem`. The CLI counts as `cli`, or as the name you give with `--source NAME`; inside a Rem turn it counts as `rem`. A proposal that repeats an open one from the same source is refused.

## When posts go out

The publisher runs in the same background process as [workflow triggers](../workflows/triggers.md): the app's backend while Raticode is open, or `Raticode rattish triggers start` from the login entry when it's closed. Only one process owns it at a time, so a post can't go out twice. It runs no AI model and sends the approved bytes as they are.

- **Late window.** A post sent after its time says so. Past the late window (2 hours by default, set in **Posts > Accounts > Publishing rules** or per post), it becomes **missed** and waits for you instead of going out days late.
- **Computer off.** Nothing local runs. A Facebook post scheduled more than about 11 minutes ahead, and a YouTube video scheduled at least 15 minutes ahead, is handed to that platform's own scheduler right after you approve, so it goes out with the computer off. **Cancel** deletes the held copy.
- **Not approved in time.** A proposal still waiting when its time and its late window have both passed becomes **expired**.
- **Pause all publishing** (top of Posts, or **Pause everything** in Publishing rules). Stops every scheduled send and every due prompt. Times that pass while paused are skipped, not caught up. Two things still go out: an account's post you send yourself with **Publish** or **Publish now**, and Facebook posts or YouTube videos already handed to that platform's own scheduler (cancel those to stop them).

### LinkedIn: tap to publish

LinkedIn's API can't schedule posts, and its [API Terms](https://www.linkedin.com/legal/l/api-terms-of-use) list using the APIs "to automate posting on the LinkedIn Services" as a prohibited use. So a LinkedIn post works differently:

1. You approve it ahead of time like any other.
2. At its time it moves to **Due now**.
3. You click **Publish**, and Raticode sends exactly the approved text.

If you don't publish it within the late window, it becomes **missed**. Each LinkedIn account has a **Send without a tap** setting, off by default. Only turn it on if you're satisfied it fits LinkedIn's terms.

A post's accounts are independent: a LinkedIn variant waiting for you doesn't hold back the X and Discord variants of the same post.

## Problems

| What happened | What Raticode does |
| --- | --- |
| Network error, server error or rate limit | Tries again after 1, 5, 15 and 60 minutes, or when the platform says to, until the late window ends. Then **failed**. |
| The login stopped working | The account shows **Needs reconnect**, and at their time its posts move to **Problems** as **Reconnect the account**. Other accounts keep going. Reconnecting doesn't resend them: duplicate or edit and approve them again. |
| The platform refused the post | **Failed**, with the platform's message. Fix it and approve again. |
| No answer after sending | **Result unknown.** Raticode never sends it again by itself, because that could post twice. Check the platform, then choose **It posted** (paste the link) or **Send again**. |
| A cap or LinkedIn's daily limit | **Held at a cap** until you choose **Publish now** or **Edit or change time**. Caps count a rolling 24 hours and 30 days, so **Publish now** holds again until the window has room. LinkedIn's limit resets at midnight UTC. |
| A deleted Discord webhook | The account is turned off. Add a new webhook to post again. |

## Safety

- Proposers can only propose. A prompt injection in a page Rem read can change a draft, which you then read. It can't publish anything.
- Logins live in their own keychain service, **Raticode social accounts**, separate from [workflow secrets](../workflows/inputs.md#secrets). No `secret["NAME"]` in a workflow can read them, and they never go into environment variables, command lines, logs, phone messages or a model's context.
- Agent tokens can read the queue, posts, rules, times and templates, and can propose and revise their own proposals through `/api/social`. They can't read the accounts list, and every other change is refused.
- Discord posts don't ping anyone by default: `allowed_mentions` allows nothing unless you change the account's setting.
- LinkedIn text is escaped, so pasted mention syntax stays plain text and can't tag anyone.
- Cards warn about links outside your allowlist (`raticode.dev` and `github.com` to start, edit it in **Posts > Accounts > Publishing rules**), international-character or look-alike domains, @mentions, hashtags you haven't used before, and drafts built from outside pages. Invisible and direction-changing characters are removed from the text, and the card says so.
- Every proposal, edit, approval, publish and failure goes into a hash-linked activity log for the post.

## Planned

These aren't built yet:

- A phone notification when a LinkedIn post is due, and composing or editing posts on the phone.
- Best times from your own posts' engagement. Today's hints are general guidance.
- Images on X, LinkedIn and Facebook.
- A `social-proposal` workflow node.
- Sharing an approved video to TikTok from the phone.
- An inbox of replies and mentions that Rem drafts answers for.
