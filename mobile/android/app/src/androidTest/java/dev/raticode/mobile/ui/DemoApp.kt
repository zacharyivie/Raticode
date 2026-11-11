package dev.raticode.mobile.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import dev.raticode.mobile.data.*
import dev.raticode.mobile.R

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun DemoApp(model: RemViewModel = viewModel(), notificationThread: String? = null, consumeNotification: () -> Unit = {}) {
    var live by rememberSaveable { mutableStateOf(false) }
    var scanOnOpen by remember { mutableStateOf(false) }
    LaunchedEffect(notificationThread) { if (notificationThread?.startsWith("live:") == true) live = true }
    if (live) {
        BackHandler { live = false; consumeNotification() }
        if (android.os.Build.VERSION.SDK_INT >= 29) LiveScreen(onBack = { live = false; consumeNotification() }, initialThread = notificationThread?.takeIf { it.startsWith("live:") }?.removePrefix("live:"), consumeInitialThread = consumeNotification, scanOnOpen = scanOnOpen, consumeScan = { scanOnOpen = false })
        else Column(Modifier.padding(24.dp)) { Text("Live TLS requires Android 10 or newer."); TextButton(shape = MaterialTheme.shapes.small, onClick = { live = false }) { Text("Back") } }
        return
    }
    var tab by rememberSaveable { mutableStateOf("Chat") }
    var reset by remember { mutableStateOf(false) }
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    val backToThreads: () -> Unit = {
        keyboard?.hide()
        focusManager.clearFocus()
        model.flushDraft()
        tab = "Threads"
    }
    val imeVisible = WindowInsets.isImeVisible
    BackHandler(enabled = model.paired && !model.storageLocked && tab == "Chat" && model.state.active != null,
        onBack = { if (imeVisible) { keyboard?.hide(); focusManager.clearFocus() } else backToThreads() })
    val appLifecycle = LocalLifecycleOwner.current
    DisposableEffect(appLifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) model.flushDraft() }
        appLifecycle.lifecycle.addObserver(observer)
        onDispose { appLifecycle.lifecycle.removeObserver(observer) }
    }
    LaunchedEffect(notificationThread, model.busy) {
        if (notificationThread != null && !notificationThread.startsWith("live:") && !model.busy) {
            model.selectThread(notificationThread); tab = "Chat"; consumeNotification()
        }
    }
    val windowSize = LocalWindowInfo.current.containerSize
    val compact = with(LocalDensity.current) { windowSize.height.toDp() < 480.dp } && windowSize.width > windowSize.height
    val compactTyping = compact && WindowInsets.isImeVisible && tab == "Chat"
    val destinations = listOf("Threads" to R.drawable.ic_threads, "Chat" to R.drawable.ic_chat, "Activity" to R.drawable.ic_activity, "Desktop" to R.drawable.ic_desktop)
    Scaffold(
        modifier = Modifier.fillMaxSize().imePadding(),
        topBar = {
            if (compact && model.paired) Column(Modifier.windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal))) {
                if (!compactTyping) Row(Modifier.fillMaxWidth().heightIn(min = 40.dp).padding(horizontal = 16.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(if (tab == "Chat") "Rem" else if (tab == "Threads") "Conversations" else tab,
                        style = MaterialTheme.typography.titleMedium)
                    Text(if (tab == "Chat") model.state.active?.title.orEmpty() else "",
                        modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.bodySmall)
                    if (tab == "Threads") TextButton(shape = MaterialTheme.shapes.small, modifier = Modifier.testTag("newConversation"), onClick = { model.newThread(); tab = "Chat" },
                        enabled = !model.busy && model.state.threads.size < LocalSession.MAX_THREADS) { Text("New conversation") }
                    Text("Local demo · No desktop or E2EE", color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.labelSmall)
                    if (tab == "Chat" && model.state.active != null) ThreadBackButton(backToThreads)
                }
                if (model.busy) LinearProgressIndicator(Modifier.fillMaxWidth().testTag("busy"))
            } else Column {
                StudioHeader(if (model.paired) "Local demo · No desktop or E2EE" else "Mobile companion", Modifier.statusBarsPadding())
                if (model.busy) LinearProgressIndicator(Modifier.fillMaxWidth().testTag("busy"))
            }
        },
        bottomBar = {
            if (model.paired && !compact) NavigationBar(containerColor = LocalStudioColors.current.navigation, tonalElevation = 0.dp) {
                destinations.forEach { (name, symbol) ->
                    NavigationBarItem(selected = tab == name, enabled = !model.busy,
                        colors = NavigationBarItemDefaults.colors(selectedIconColor = LocalStudioColors.current.onNavigation,
                            selectedTextColor = LocalStudioColors.current.onNavigation, indicatorColor = LocalStudioColors.current.navigationSelected,
                            unselectedIconColor = LocalStudioColors.current.navigationMuted, unselectedTextColor = LocalStudioColors.current.navigationMuted),
                        onClick = { tab = name }, icon = { Icon(painterResource(symbol), contentDescription = null) },
                        label = { Text(name) }, modifier = Modifier.testTag("tab$name"))
                }
            }
        },
    ) { padding ->
        Row(Modifier.padding(padding).consumeWindowInsets(padding).fillMaxSize()) {
            if (compact && model.paired) Surface(color = MaterialTheme.colorScheme.surfaceContainer) {
                Column(Modifier.width((96 * LocalDensity.current.fontScale.coerceAtLeast(1f)).dp).fillMaxHeight().verticalScroll(rememberScrollState()).padding(4.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (compactTyping) Text("Local demo\nNo desktop\nor E2EE",
                        modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelSmall)
                    destinations.forEach { (name, _) ->
                        TextButton(shape = MaterialTheme.shapes.small, onClick = { tab = name }, enabled = !model.busy,
                            colors = ButtonDefaults.textButtonColors(
                                containerColor = if (tab == name) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surfaceContainer,
                                contentColor = if (tab == name) MaterialTheme.colorScheme.onSecondaryContainer else MaterialTheme.colorScheme.onSurfaceVariant),
                            modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).semantics { selected = tab == name }.testTag("tab$name")) { Text(name) }
                    }
                }
            }
            Box(Modifier.weight(1f).fillMaxHeight()) {
                if (model.storageLocked) Column(Modifier.padding(24.dp)) {
                    Text("Local data is locked", style = MaterialTheme.typography.headlineMedium)
                    Text("The saved workspace could not be read. Reset deletes it from this device.")
                    Button(shape = MaterialTheme.shapes.small, onClick = { reset = true }, enabled = !model.busy) { Text("Reset local data") }
                } else if (!model.paired) PairingScreen(model, onConnect = { live = true }, onScan = { scanOnOpen = true; live = true })
                else when (tab) {
                    "Threads" -> ThreadsScreen(model, compact) { tab = "Chat" }
                    "Chat" -> model.state.active?.let { key(it.id) { Surface(color = LocalStudioColors.current.chat, contentColor = LocalStudioColors.current.onChat) { ChatScreen(model, it, compact, compactTyping, backToThreads) } } } ?: EmptyThreads(model) { tab = "Threads" }
                    "Activity" -> ActivityScreen(model)
                    else -> Column {
                        TextButton(shape = MaterialTheme.shapes.small, onClick = { live = true }) { Text("Connect to live desktop") }
                        DesktopScreen(model, onReset = { reset = true })
                    }
                }
            }
        }
    }
    if (model.importing) AlertDialog(onDismissRequest = model::cancelImport,
        title = { Text("Importing attachment") }, text = { Text("Waiting for the document provider. Imports time out after 30 seconds.") },
        confirmButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = model::cancelImport) { Text("Cancel import") } })
    model.notice?.let { text -> AlertDialog(onDismissRequest = model::clearNotice, title = { Text("Raticode") }, text = { Text(text) },
        confirmButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = model::clearNotice) { Text("Got it") } }) }
    if (reset) AlertDialog(onDismissRequest = { reset = false }, title = { Text("Delete local workspace?") },
        text = { Text("This deletes every conversation, draft, and imported file on this phone. Exported copies are kept. Live desktop pairing is separate and is not deleted here.") },
        confirmButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { reset = false; model.reset(); tab = "Chat" }) { Text("Delete local data") } },
        dismissButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { reset = false }) { Text("Keep data") } })
}

@Composable
private fun PairingScreen(model: RemViewModel, onConnect: () -> Unit, onScan: () -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Rem, on your phone", style = MaterialTheme.typography.headlineLarge)
        Text("Continue a desktop conversation wherever you are.", style = MaterialTheme.typography.bodyLarge)
        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        Text("Connect to your desktop", style = MaterialTheme.typography.titleLarge)
        Text("Open Device settings in Raticode Desktop and create an invitation. Scan it here, then compare the desktop identity before pairing.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Button(shape = MaterialTheme.shapes.small, enabled = !model.busy, onClick = onScan, modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp)) { Text("Scan QR code") }
        OutlinedButton(shape = MaterialTheme.shapes.small, onClick = onConnect, modifier = Modifier.fillMaxWidth()) { Text("Connect to live desktop") }
        TextButton(shape = MaterialTheme.shapes.small, onClick = onConnect, enabled = !model.busy) { Text("Paste invitation") }
        Text("Your desktop confirms the phone and chooses which threads it can access.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        Text("Explore without a desktop", style = MaterialTheme.typography.titleMedium)
        Text("The local demo has sample replies and activity. It does not connect to a desktop.", style = MaterialTheme.typography.bodySmall)
        OutlinedButton(shape = MaterialTheme.shapes.small, onClick = model::pair, enabled = !model.busy, modifier = Modifier.fillMaxWidth().testTag("pairDemo")) { Text("Use local demo") }
    }
}

@Composable
private fun EmptyThreads(model: RemViewModel, openThreads: () -> Unit) {
    Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("A new conversation with Rem", style = MaterialTheme.typography.headlineMedium)
        Text("Each conversation keeps its own project, resources, draft, and history.")
        Button(shape = MaterialTheme.shapes.small, enabled = !model.busy, onClick = { model.newThread(); openThreads() }) { Text("New conversation") }
    }
}

@Composable
private fun ThreadsScreen(model: RemViewModel, compact: Boolean, openChat: () -> Unit) {
    var edit by remember { mutableStateOf<Conversation?>(null) }
    var delete by remember { mutableStateOf<Conversation?>(null) }
    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
        if (!compact) {
            Text("Conversations", style = MaterialTheme.typography.headlineLarge, modifier = Modifier.padding(vertical = 12.dp))
            Button(shape = MaterialTheme.shapes.small, modifier = Modifier.testTag("newConversation"), onClick = { model.newThread(); openChat() }, enabled = !model.busy && model.state.threads.size < LocalSession.MAX_THREADS) { Text("New conversation") }
        }
        LazyColumn(Modifier.weight(1f).testTag("threads"), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(vertical = 16.dp)) {
            items(model.state.threads, key = { it.id }) { thread ->
                Column {
                    TextButton(shape = MaterialTheme.shapes.small, onClick = { model.selectThread(thread.id); openChat() }, enabled = !model.busy) {
                        Column(Modifier.fillMaxWidth()) {
                            Text(thread.title, style = MaterialTheme.typography.titleLarge)
                            Text(thread.project.ifBlank { "No project selected" }, fontSize = 12.sp)
                        }
                    }
                    Text("${thread.messages.size} messages • ${thread.messages.count { it.delivery == Delivery.QUEUED }} queued", fontSize = 12.sp)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(shape = MaterialTheme.shapes.small, onClick = { edit = thread }, enabled = !model.busy) { Text("Edit context") }
                        TextButton(shape = MaterialTheme.shapes.small, onClick = { delete = thread }, enabled = !model.busy) { Text("Delete") }
                    }
                    HorizontalDivider()
                }
            }
        }
    }
    edit?.let { t -> EditContext(t, close = { edit = null }) { title, project, resources, provider, engine ->
        model.editThread(t.id, title, project, resources, provider, engine); edit = null
    } }
    delete?.let { t -> AlertDialog(onDismissRequest = { delete = null }, title = { Text("Delete conversation?") }, text = { Text("Delete ${t.title} and its local files? Exported copies are kept.") },
        confirmButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { model.deleteThread(t.id); delete = null }) { Text("Delete") } }, dismissButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { delete = null }) { Text("Keep") } }) }
}

@Composable
private fun EditContext(thread: Conversation, close: () -> Unit, save: (String, String, String, String, String) -> Unit) {
    var title by remember { mutableStateOf(thread.title) }; var project by remember { mutableStateOf(thread.project) }
    var resources by remember { mutableStateOf(thread.resources) }; var provider by remember { mutableStateOf(thread.provider) }; var engine by remember { mutableStateOf(thread.model) }
    AlertDialog(onDismissRequest = close, title = { Text("Conversation context") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Local labels for now. Desktop will supply authorized project and resource IDs. Changing provider or model keeps this conversation.")
            OutlinedTextField(title, { title = it.take(80) }, label = { Text("Title") })
            OutlinedTextField(project, { project = it.take(300) }, label = { Text("Project") })
            OutlinedTextField(resources, { resources = it.take(1000) }, label = { Text("Resources") }, maxLines = 3)
            OutlinedTextField(provider, { provider = it.take(80) }, label = { Text("Provider") }, modifier = Modifier.testTag("providerField"))
            OutlinedTextField(engine, { engine = it.take(80) }, label = { Text("Model") })
        }
    }, confirmButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { save(title, project, resources, provider, engine) }) { Text("Save context") } }, dismissButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = close) { Text("Cancel") } })
}

@Composable
private fun ThreadBackButton(onClick: () -> Unit) {
    IconButton(onClick = onClick, modifier = Modifier.size(48.dp).testTag("backToThreads")) {
        Icon(painterResource(R.drawable.ic_arrow_back), contentDescription = "Back to threads")
    }
}

@Composable
private fun ChatScreen(model: RemViewModel, thread: Conversation, compact: Boolean, compactTyping: Boolean, backToThreads: () -> Unit) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current
    var voiceConsent by remember { mutableStateOf(false) }
    var listening by remember { mutableStateOf(false) }
    var dictation by remember { mutableStateOf<OnDeviceDictation?>(null) }
    var pendingExportId by rememberSaveable { mutableStateOf<String?>(null) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> uri?.let { model.attach(thread.id, it) } }
    val exporter = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        val file = model.state.files().find { it.id == pendingExportId }; pendingExportId = null
        if (uri != null && file != null) model.export(file, uri)
    }
    fun startDictation() {
        dictation?.close()
        dictation = try {
            OnDeviceDictation(context, result = { transcript -> model.appendTranscript(thread.id, transcript) },
                ended = { error -> listening = false; error?.let(model::inform) })
        } catch (_: Exception) { null }
        listening = dictation?.start() == true
        if (!listening) model.inform("On-device dictation is unavailable here. Install an offline speech model on an Android 12 or newer device, or type your message.")
    }
    val microphone = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startDictation() else model.inform("Microphone access was denied. You can keep typing.")
    }
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) { dictation?.close(); dictation = null; listening = false } }
        lifecycle.lifecycle.addObserver(observer)
        onDispose { lifecycle.lifecycle.removeObserver(observer); dictation?.close() }
    }
    val scroll = rememberLazyListState()
    LaunchedEffect(thread.messages.size) { if (thread.messages.isNotEmpty()) scroll.animateScrollToItem(thread.messages.lastIndex) }
    Row(Modifier.fillMaxSize()) {
        Column(Modifier.weight(1f).fillMaxHeight()) {
            if (!compact) Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Rem", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                    Text(thread.title, maxLines = 1, fontSize = 13.sp)
                    if (model.state.simulateOffline) Text("Offline simulation • messages queue locally", color = MaterialTheme.colorScheme.secondary, fontSize = 12.sp)
                }
                ThreadBackButton(backToThreads)
            }
            if (compact && model.state.simulateOffline) Text("Offline simulation • messages queue locally",
                modifier = Modifier.padding(horizontal = 16.dp), color = MaterialTheme.colorScheme.secondary, fontSize = 12.sp)
            LazyColumn(state = scroll, modifier = Modifier.weight(1f).fillMaxWidth().testTag("messages"), contentPadding = PaddingValues(20.dp, if (compact) 4.dp else 12.dp), verticalArrangement = Arrangement.spacedBy(if (compact) 12.dp else 18.dp)) {
                items(thread.messages, key = { it.id }) { message ->
                    Column(Modifier.fillMaxWidth(), horizontalAlignment = if (message.fromRem) Alignment.Start else Alignment.End) {
                        Text(if (message.fromRem) "Rem / sample reply" else when (message.delivery) {
                            Delivery.QUEUED -> "You / queued locally"; Delivery.FAILED -> "You / simulated failure"; Delivery.CANCELLED -> "You / cancelled locally"; Delivery.SAMPLE_REPLY -> "You / local only"
                        }, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Surface(color = if (message.fromRem) LocalStudioColors.current.reply else MaterialTheme.colorScheme.primaryContainer, contentColor = if (message.fromRem) LocalStudioColors.current.onChat else MaterialTheme.colorScheme.onPrimaryContainer, shape = MaterialTheme.shapes.medium, modifier = Modifier.widthIn(max = 480.dp)) {
                            Column(Modifier.padding(horizontal = 14.dp, vertical = if (compact) 8.dp else 14.dp)) {
                                if (message.text.isNotEmpty()) SelectionContainer { Text(message.text, lineHeight = 23.sp) }
                                message.file?.let { file ->
                                    Text(file.name, fontWeight = FontWeight.Medium)
                                    Text("${file.size} bytes • encrypted on this phone", fontSize = 11.sp)
                                    TextButton(shape = MaterialTheme.shapes.small, colors = ButtonDefaults.textButtonColors(contentColor = LocalContentColor.current), enabled = !model.busy, onClick = { pendingExportId = file.id; exporter.launch(file.name) }) { Text("Save a copy") }
                                }
                                if (!message.fromRem && message.delivery in setOf(Delivery.QUEUED, Delivery.FAILED)) Row {
                                    if (message.delivery == Delivery.FAILED) TextButton(shape = MaterialTheme.shapes.small, colors = ButtonDefaults.textButtonColors(contentColor = LocalContentColor.current), enabled = !model.busy, onClick = { model.retry(thread.id, message.id) }) { Text("Retry") }
                                    TextButton(shape = MaterialTheme.shapes.small, colors = ButtonDefaults.textButtonColors(contentColor = LocalContentColor.current), enabled = !model.busy, onClick = { model.cancel(thread.id, message.id) }) { Text("Cancel send") }
                                }
                            }
                        }
                    }
                }
            }
            Column(Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) {
                thread.attachment?.let { file -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(file.name, Modifier.weight(1f), maxLines = 1, fontSize = 12.sp)
                    TextButton(shape = MaterialTheme.shapes.small, enabled = !model.busy, onClick = { model.removeAttachment(thread.id) }) { Text("Remove") }
                } }
                val composer: @Composable (Modifier) -> Unit = { modifier ->
                    OutlinedTextField(value = thread.draft, onValueChange = { model.draft(thread.id, it) }, modifier = modifier.testTag("composer"), enabled = (!model.busy || model.savingDraft) && !model.storageLocked && !listening,
                        placeholder = { Text(if (listening) "Listening on this device…" else "Message Rem…") }, maxLines = if (compact) 1 else 4,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                        keyboardActions = KeyboardActions(onSend = { if (!model.busy && !listening && (thread.draft.isNotBlank() || thread.attachment != null)) model.send(thread.id, thread.draft) }))
                }
                val actions: @Composable RowScope.() -> Unit = {
                    TextButton(shape = MaterialTheme.shapes.small, enabled = !model.busy && !listening, onClick = { picker.launch(arrayOf("*/*")) }) { Text("Attach") }
                    TextButton(shape = MaterialTheme.shapes.small, enabled = !model.busy, onClick = { if (listening) { dictation?.close(); listening = false } else voiceConsent = true }) { Text(if (listening) "Stop" else "Voice") }
                    if (!compact) Spacer(Modifier.weight(1f))
                    Button(shape = MaterialTheme.shapes.small, onClick = { model.send(thread.id, thread.draft) }, enabled = !model.busy && !listening && (thread.draft.isNotBlank() || thread.attachment != null), modifier = Modifier.testTag("send")) { Text("Send") }
                }
                if (compact) Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    composer(Modifier.weight(1f))
                    actions()
                } else {
                    composer(Modifier.fillMaxWidth())
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, content = actions)
                }
            }
        }
        if (compactTyping) ThreadBackButton(backToThreads)
    }
    if (voiceConsent) AlertDialog(onDismissRequest = { voiceConsent = false }, title = { Text("On-device dictation") }, text = { Text("Audio stays with Android's on-device speech recognizer. Review the transcript before sending. No online speech fallback is used.") },
        confirmButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = {
            voiceConsent = false
            if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) startDictation() else microphone.launch(Manifest.permission.RECORD_AUDIO)
        }) { Text("Start dictation") } }, dismissButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { voiceConsent = false }) { Text("Cancel") } })
}

@Composable
private fun ActivityScreen(model: RemViewModel) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
        Text("While you're away", style = MaterialTheme.typography.headlineLarge)
        Text("Sample desktop activity. No agents are running through this app.")
        model.swarms.forEach { swarm ->
            Text(swarm.status, color = MaterialTheme.colorScheme.secondary)
            Text(swarm.name, style = MaterialTheme.typography.titleLarge)
            Text(swarm.detail)
            LinearProgressIndicator(progress = { swarm.progress }, modifier = Modifier.fillMaxWidth())
            HorizontalDivider()
        }
        Text("Local outbox", style = MaterialTheme.typography.titleLarge)
        Text("${model.state.threads.sumOf { t -> t.messages.count { it.delivery == Delivery.QUEUED } }} queued • ${model.state.threads.sumOf { t -> t.messages.count { it.delivery == Delivery.FAILED } }} failed simulations")
        Button(shape = MaterialTheme.shapes.small, enabled = !model.busy && !model.state.simulateOffline, onClick = model::flush) { Text("Process local queue") }
        Text("Real swarm updates, approval requests, and incoming files require the desktop adapter.", color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun DesktopScreen(model: RemViewModel, onReset: () -> Unit) {
    val context = LocalContext.current
    fun notifySample() {
        val id = model.state.activeId
        if (id == null) model.inform("Create a conversation first.")
        else model.inform(if (LocalNotifications.show(context, id)) "A generic local demo notification was posted. It contains no message text or filename." else "Notifications are disabled. Enable them in Android settings to test a local update.")
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) notifySample() else model.inform("Notifications were denied. Chat remains available.")
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
        Text("Your desktop", style = MaterialTheme.typography.headlineLarge)
        Text("Not connected", style = MaterialTheme.typography.titleLarge)
        Text("This workspace runs on your phone. Demo pairing never grants desktop access.")
        HorizontalDivider()
        Text("Local storage", style = MaterialTheme.typography.titleMedium)
        Text("Conversations and files use Android Keystore encryption. Backups are disabled. Uninstalling loses the local workspace.")
        Text("${model.state.files().sumOf { it.size } / 1024} KiB of 50 MiB attachment space used")
        Text("Relay and E2EE", style = MaterialTheme.typography.titleMedium)
        Text("No relay configured. Network messaging is disabled until the desktop can complete authenticated encrypted pairing.")
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Simulate offline", Modifier.weight(1f)); Switch(checked = model.state.simulateOffline, onCheckedChange = model::offline, enabled = !model.busy, modifier = Modifier.testTag("offline"))
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Fail next local send", Modifier.weight(1f)); Switch(checked = model.state.failNext, onCheckedChange = model::failNext, enabled = !model.busy, modifier = Modifier.testTag("failNext"))
        }
        Text("Use these controls to exercise queued messages, cancellation, and retries. Nothing is sent to a desktop.", fontSize = 12.sp)
        Text("Notifications", style = MaterialTheme.typography.titleMedium)
        Text("Test a generic local notification. Desktop push and background delivery are not connected.")
        OutlinedButton(shape = MaterialTheme.shapes.small, enabled = !model.busy, onClick = {
            if (android.os.Build.VERSION.SDK_INT >= 33) permission.launch(Manifest.permission.POST_NOTIFICATIONS) else notifySample()
        }) { Text("Test local notification") }
        OutlinedButton(shape = MaterialTheme.shapes.small, onClick = onReset, enabled = !model.busy, modifier = Modifier.fillMaxWidth().testTag("resetDemo")) { Text("Reset local workspace") }
    }
}
