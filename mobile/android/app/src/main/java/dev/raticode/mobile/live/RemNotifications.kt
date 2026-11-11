package dev.raticode.mobile.live

import android.Manifest
import android.app.KeyguardManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.core.app.NotificationCompat
import androidx.core.app.Person
import androidx.core.app.RemoteInput
import androidx.core.content.ContextCompat
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import dev.raticode.mobile.R
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Rem's chat as an Android conversation: alerts for Rem's replies and task updates on this phone's own
 * requests, and with their own switches for tasks needing the user, tasks finishing and self-work's proposals from
 * any source, with an inline Reply. Off until the user turns it on. Unless they also turn on "Show Rem's words",
 * alerts stay generic, such as "New message" or "A task needs you"; the lock screen always gets the generic public version.
 * During the user's quiet hours alerts still arrive, without sound or a heads-up.
 * Replying needs an unlocked phone ([NotificationCompat.Action.Builder.setAuthenticationRequired] on Android 12+,
 * and [RemReplyReceiver] checks again on every version).
 */
object RemNotifications {
    const val CHANNEL = "rem-messages"
    const val REPLY_KEY = "rem_reply"
    private const val ID = 2004
    private const val TAG = "rem"
    private const val SHORTCUT = "rem-chat"
    private const val PREFS = "rem-notifications"
    /** Android mutes a silent alert only when it belongs to a group, and the library doesn't reliably set one. */
    private const val QUIET_GROUP = "rem-quiet"

    /** The opt-ins, kept in this app's private preferences; [RemAlertRules.Switches] decides what changing them does. */
    private fun switches(context: Context): RemAlertRules.Switches {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return RemAlertRules.Switches(object : RemAlertRules.Flags {
            override fun get(key: String) = prefs.getBoolean(key, false)
            override fun put(values: Map<String, Boolean>) { prefs.edit().apply { values.forEach { (key, value) -> putBoolean(key, value) } }.apply() }
            override fun number(key: String, default: Int) = prefs.getInt(key, default)
            override fun putNumber(key: String, value: Int) { prefs.edit().putInt(key, value).apply() }
        }, object : RemAlertRules.Surface {
            override fun clear() { clear(context); TaskNotification.hideWords(context) }
            override fun forgetConversation() { runCatching { ShortcutManagerCompat.removeLongLivedShortcuts(context, listOf(SHORTCUT)) } }
            // The channel exists from the start, so Android's settings can show it before the first alert.
            override fun createChannel() = channel(context)
        })
    }
    fun enabled(context: Context): Boolean = switches(context).enabled
    fun showText(context: Context): Boolean = switches(context).showText
    fun setEnabled(context: Context, enabled: Boolean) = switches(context).setEnabled(enabled)
    fun setShowText(context: Context, show: Boolean) = switches(context).setShowText(show)
    /** The alert switches that are on, as `rem.notices` names them. */
    fun include(context: Context): List<String> = switches(context).include
    fun setFilter(context: Context, name: String, on: Boolean) = switches(context).setFilter(name, on)
    fun settling(context: Context): List<String> = switches(context).settling
    fun settled(context: Context, names: Collection<String>) = switches(context).settled(names)
    fun quietHours(context: Context): RemAlertRules.QuietHours = switches(context).quietHours
    fun setQuietHours(context: Context, hours: RemAlertRules.QuietHours) = switches(context).setQuietHours(hours)
    /** True once after the user turns alerts on; the client then forgets its message position. */
    fun takeBaseline(context: Context): Boolean = switches(context).takeBaseline()

    /** What the settings say, from Android's permission, app and Rem-category switches and the desktop's answer. */
    fun state(context: Context, desktopControl: Boolean = true, supported: Boolean? = null): RemAlertRules.State {
        val permission = Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        val manager = context.getSystemService(NotificationManager::class.java)
        val channelBlocked = manager.getNotificationChannel(CHANNEL)?.importance == NotificationManager.IMPORTANCE_NONE
        return RemAlertRules.state(permission, manager.areNotificationsEnabled(), channelBlocked, desktopControl, supported)
    }

    /** Android lets this app post Rem's alerts: notification permission, app notifications and the Rem channel. */
    fun allowed(context: Context): Boolean = state(context).androidAllows

    fun active(context: Context): Boolean = enabled(context) && allowed(context)

    private fun channel(context: Context) = context.getSystemService(NotificationManager::class.java).createNotificationChannel(
        NotificationChannel(CHANNEL, "Rem", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Rem's replies and task updates, and the alerts you chose in Raticode's settings"
            lockscreenVisibility = Notification.VISIBILITY_PRIVATE
        })

    /** Rem's new messages, added to the alert already showing. */
    fun post(context: Context, notices: List<RemNotice>, skipped: Int) {
        if (!active(context) || (notices.isEmpty() && skipped == 0)) return
        val lines = RemAlertRules.lines(notices, skipped, showText(context), System.currentTimeMillis())
        val time = java.time.LocalTime.now()
        notify(context, lines.map { message(context, it) }, silent = RemAlertRules.quiet(quietHours(context), time.hour * 60 + time.minute))
    }

    /** The user's reply, shown in the alert at once so Android stops its progress spinner. */
    fun replied(context: Context, text: String) =
        notify(context, listOf(message(context, RemAlertRules.replyLine(text, showText(context), System.currentTimeMillis()))), silent = true)

    /** A short status from the app, such as a reply that wasn't sent. It never repeats the reply's text. */
    fun note(context: Context, text: String) =
        notify(context, listOf(message(context, RemAlertRules.Line(RemAlertRules.Sender.RATICODE, text, System.currentTimeMillis()))), silent = true)

    private fun message(context: Context, line: RemAlertRules.Line) = NotificationCompat.MessagingStyle.Message(line.text, line.at,
        when (line.sender) { RemAlertRules.Sender.REM -> rem(context); RemAlertRules.Sender.RATICODE -> raticode(); RemAlertRules.Sender.YOU -> null })

    fun clear(context: Context) = context.getSystemService(NotificationManager::class.java).cancel(TAG, ID)

    private fun notify(context: Context, added: List<NotificationCompat.MessagingStyle.Message>, silent: Boolean) {
        if (!active(context)) return
        val manager = context.getSystemService(NotificationManager::class.java)
        channel(context)
        val me = Person.Builder().setKey("me").setName("You").build()
        val style = NotificationCompat.MessagingStyle(me)
        val shown = manager.activeNotifications.firstOrNull { it.id == ID && it.tag == TAG }?.notification
            ?.let { NotificationCompat.MessagingStyle.extractMessagingStyleFromNotification(it)?.messages }.orEmpty()
        (shown + added).takeLast(RemNoticeText.HISTORY).forEach { style.addMessage(it) }
        conversationShortcut(context)
        val open = PendingIntent.getActivity(context, 4, dev.raticode.mobile.childlock.NotificationOpenActivity.intent(context).setAction(Intent.ACTION_VIEW)
            .putExtra(LiveNotifications.THREAD_EXTRA, "live:rem").setData(Uri.parse("raticode-live://rem")), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        // RemoteInput fills in the reply, so this one PendingIntent is mutable; its target is this app's private receiver.
        val reply = PendingIntent.getBroadcast(context, 5, Intent(context, RemReplyReceiver::class.java).setPackage(context.packageName),
            PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val action = NotificationCompat.Action.Builder(R.drawable.ic_chat, "Reply", reply)
            .addRemoteInput(RemoteInput.Builder(REPLY_KEY).setLabel("Reply to Rem").build())
            .setAllowGeneratedReplies(false).setSemanticAction(NotificationCompat.Action.SEMANTIC_ACTION_REPLY)
            .setShowsUserInterface(false).setAuthenticationRequired(true).build()
        val public = NotificationCompat.Builder(context, CHANNEL).setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(RemAlertRules.LOCK_SCREEN_TITLE).setContentText(RemAlertRules.LOCK_SCREEN_TEXT).build()
        manager.notify(TAG, ID, NotificationCompat.Builder(context, CHANNEL).setSmallIcon(R.drawable.ic_launcher)
            .setStyle(style).setCategory(NotificationCompat.CATEGORY_MESSAGE).setShortcutId(SHORTCUT)
            .setContentIntent(open).addAction(action).setAutoCancel(true).setSilent(silent).setGroup(if (silent) QUIET_GROUP else null)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE).setPublicVersion(public).build())
    }

    private fun rem(context: Context) = Person.Builder().setKey("rem").setName("Rem").setImportant(true)
        .setIcon(IconCompat.createWithResource(context, R.drawable.raticode_roundel)).build()

    private fun raticode() = Person.Builder().setKey("raticode").setName("Raticode").build()

    /** The long-lived shortcut Android needs to file Rem under Conversations. It only opens Rem's chat. */
    private fun conversationShortcut(context: Context) {
        // One saved by an earlier version is replaced, because Android keeps a long-lived shortcut across updates.
        val opener = dev.raticode.mobile.childlock.NotificationOpenActivity::class.java.name
        if (ShortcutManagerCompat.getDynamicShortcuts(context).any { it.id == SHORTCUT && it.intent.component?.className == opener }) return
        val intent = dev.raticode.mobile.childlock.NotificationOpenActivity.intent(context).setAction(Intent.ACTION_VIEW)
            .putExtra(LiveNotifications.THREAD_EXTRA, "live:rem").setData(Uri.parse("raticode-live://rem"))
        runCatching {
            ShortcutManagerCompat.pushDynamicShortcut(context, ShortcutInfoCompat.Builder(context, SHORTCUT)
                .setShortLabel("Rem").setLongLived(true).setPerson(rem(context)).setIntent(intent)
                .setIcon(IconCompat.createWithResource(context, R.drawable.raticode_roundel)).build())
        }
    }
}

/**
 * Receives a reply typed into Rem's alert. Not exported: only the alert's own PendingIntent reaches it.
 * The reply goes into the encrypted outbox like one typed in Rem's chat and is sent when the phone next
 * reaches the desktop, which still decides whether this phone may use Rem.
 */
class RemReplyReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext as android.app.Application
        // setAuthenticationRequired covers Android 12 and newer; the lock check covers older phones and any skipped prompt.
        val outcome = RemAlertRules.reply(RemNotifications.enabled(app), Build.VERSION.SDK_INT,
            app.getSystemService(KeyguardManager::class.java).isDeviceLocked,
            RemoteInput.getResultsFromIntent(intent)?.getCharSequence(RemNotifications.REPLY_KEY),
            dev.raticode.mobile.childlock.ChildLockRuntime.locked(app))
        when (outcome) {
            RemAlertRules.Reply.Withdraw -> RemNotifications.clear(app)
            is RemAlertRules.Reply.Refuse -> RemNotifications.note(app, outcome.note)
            is RemAlertRules.Reply.Send -> if (Build.VERSION.SDK_INT >= 29) {
                RemNotifications.replied(app, outcome.text)
                // Keep the process until the reply is saved in the encrypted outbox, within Android's time for a broadcast.
                val pending = goAsync()
                val done = AtomicBoolean()
                val finish = { if (done.compareAndSet(false, true)) pending.finish() }
                Handler(Looper.getMainLooper()).postDelayed({ finish() }, 8_000)
                LiveRuntime.client(app).replyToRem(outcome.text, onSaved = finish)
            }
        }
    }
}
