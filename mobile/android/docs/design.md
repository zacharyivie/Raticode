# Mobile design

Match Raticode Desktop's `frontend/src/styles/index.css` and system sans-serif typography. Light mode uses stone workspace #E4DFD5, raised surface #F4F0E7, ink #30343B and muted text #62635F. Navigation is deep indigo #2B3149 with #F0EFF9 text. Actions and outgoing chat use #4F46E5 with white text. The Rem pane uses sage #DCE5DF, replies #EEF3EC and ink #2A3D36. Reuse the desktop logo. Dark mode follows desktop's #0B0B0C canvas, #18181A surfaces, #F3F3F5 text and #9D9DA5 secondary text; lighter indigo action text preserves contrast. Follow Android system appearance, without dynamic wallpaper colors.

Use left-aligned, semibold headings and compact 4–12 dp corner radii instead of pill-shaped controls. Body text is 14–16 sp; secondary text is 12 sp. Retain Android's minimum interactive touch targets. Flat boundaries separate regions; status has a text label. Rem replies and outgoing messages have explicit content colors so nested actions remain readable.

Welcome prioritizes actual desktop enrollment. Scan and paste both enter the same live v2 fingerprint-review flow; the local demo is a secondary, explicitly labeled action. The live screen has a persistent desktop-style brand header, wrapping thread controls and a sage conversation region. Notification and reset controls follow the main pairing/conversation content.


Layout: persistent demo status above the current workspace; thread selector above chat; messages occupy remaining space; attachments and a bounded composer sit above the keyboard. Threads, Chat, Activity, and Desktop are separate destinations. Small screens use scrolling forms and wrapped action rows. Controls have labels and at least standard Material touch targets.

In short landscape windows, remove the stacked brand and screen headings. Put the current destination and thread title in one compact header alongside the local-demo warning. Move labeled navigation to a narrow, scrollable left column and keep composer controls inline. Threads puts New conversation in the header so its list gets the remaining height. Preserve Material touch targets and system/cutout insets.

While typing in landscape Chat, the main header collapses and the local-demo warning moves into the side navigation. This leaves room for messages above tall software keyboards.

Keep pairing instructions focused on the desktop-to-phone relationship. Avoid turning conversation details into a dashboard of identical cards. Use simple rows for threads and activity. The durable outbox exposes honest states: queued locally, sample reply, failed simulation, and cancelled locally. No demo state says delivered or securely paired.

Navigation uses Lucide/Feather-style line icons consistent with desktop. The license is bundled in `app/src/main/assets/licenses/lucide.txt`. The logo asset is copied from desktop's 128 px application icon.
