package dev.raticode.mobile.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp

/** Small outlined controls matching the desktop thread toolbar and activity rail. */
internal object ThreadIcons {
    private fun icon(name: String, path: String, width: Float = 1.6f) = ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f)
        .addPath(addPathNodes(path), fill = null, stroke = SolidColor(Color.Black), strokeLineWidth = width,
            strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round).build()

    /** Lucide icons as drawn by the desktop rail: 24-unit grid, 1.5 stroke. */
    private fun lucide(name: String, vararg paths: String, dots: List<Triple<Float, Float, Float>> = emptyList()) =
        ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).apply {
            paths.forEach { addPath(addPathNodes(it), fill = null, stroke = SolidColor(Color.Black), strokeLineWidth = 1.5f,
                strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round) }
            dots.forEach { (x, y, r) -> addPath(addPathNodes("M${x - r} $y a$r $r 0 1 0 ${2 * r} 0 a$r $r 0 1 0 ${-2 * r} 0Z"), fill = SolidColor(Color.Black)) }
        }.build()

    val ExpandMore = icon("ExpandMore", "M7 10 L12 15 L17 10")
    // Lucide Search, ScrollText (the Developer log) and Plus.
    val Search = lucide("Search", "m21 21-4.34-4.34", "M11 3a8 8 0 1 0 0 16a8 8 0 1 0 0-16z")
    val Log = lucide("Log", "M15 12h-5", "M15 8h-5", "M19 17V5a2 2 0 0 0-2-2H4",
        "M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3")
    val Plus = lucide("Plus", "M5 12h14", "M12 5v14")
    // Desktop rail: MessageSquare, RatFaceIcon, Building2 and RatSwarmIcon; More is Lucide's Ellipsis.
    val Threads = lucide("Threads", "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z")
    val Rem = lucide("Rem",
        "M12 7.4C15.7 7.4 18.2 9.7 18.2 12.6C18.2 15.2 14.6 18.9 12.9 20.3Q12 21 11.1 20.3C9.4 18.9 5.8 15.2 5.8 12.6C5.8 9.7 8.3 7.4 12 7.4Z",
        "M6.1 10.2A3.4 3.4 0 1 1 9.2 7.9", "M17.9 10.2A3.4 3.4 0 1 0 14.8 7.9",
        "M5.3 7.3A1.6 1.6 0 0 1 7 5.8M18.7 7.3A1.6 1.6 0 0 0 17 5.8",
        "M9.4 16.3 4.2 15.3M9.7 17.4 4.8 18.7M14.6 16.3l5.2-1M14.3 17.4l4.9 1.3",
        dots = listOf(Triple(9.6f, 12.6f, 1f), Triple(14.4f, 12.6f, 1f), Triple(12f, 19.4f, 1.05f)))
    val Organizations = lucide("Organizations", "M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z", "M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2",
        "M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2", "M10 6h4", "M10 10h4", "M10 14h4", "M10 18h4")
    // The desktop draws this trace on a 900-unit canvas offset by (180, 175) with a 45-unit stroke.
    val Swarms = ImageVector.Builder("Swarms", 24.dp, 24.dp, 900f, 900f).apply {
        addGroup(translationX = -180f, translationY = -175f)
        listOf(
            "M 655 606 C 660 553 631 518 584 498 C 544 481 496 449 524 440 C 583 424 612 388 665 411 C 653 359 714 344 736 390 C 754 424 726 439 707 439 C 751 459 740 478 796 486 C 903 505 968 614 919 734 C 898 776 974 790 1020 749 C 1058 715 1046 683 1018 655",
            "M 767 620 C 718 643 726 694 749 725 Q 771 749 770 756 M 738 757 C 790 755 820 766 865 779 C 913 793 977 784 1011 758",
            "M 322 848 C 405 861 390 745 354 697 C 330 664 313 660 277 649 C 236 636 202 613 226 604 C 281 585 310 540 370 561 C 359 516 408 497 434 525 C 460 554 438 582 412 587 C 451 609 462 614 510 610 C 624 596 704 667 696 778 Q 696 802 685 825 C 671 863 758 889 811 866",
            "M 500 738 C 455 751 453 794 480 825 L 511 856 M 428 869 C 453 846 554 856 609 872 C 686 896 774 891 850 852",
        ).forEach { addPath(addPathNodes(it), fill = null, stroke = SolidColor(Color.Black), strokeLineWidth = 45f,
            strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round) }
        clearGroup()
    }.build()
    val More = lucide("More", dots = listOf(Triple(5f, 12f, 1.6f), Triple(12f, 12f, 1.6f), Triple(19f, 12f, 1.6f)))
    // Other bar destinations. Source control and Workflows match the desktop rail (Lucide GitBranch and Waypoints).
    val SourceControl = lucide("SourceControl", "M6 3v12", "M21 6a3 3 0 1 1 -6 0a3 3 0 1 1 6 0", "M9 18a3 3 0 1 1 -6 0a3 3 0 1 1 6 0", "M18 9a9 9 0 0 1-9 9")
    val Workflows = lucide("Workflows", "M14.5 4.5a2.5 2.5 0 1 1 -5.0 0a2.5 2.5 0 1 1 5.0 0", "m10.2 6.3-3.9 3.9", "M7.0 12a2.5 2.5 0 1 1 -5.0 0a2.5 2.5 0 1 1 5.0 0", "M7 12h10", "M22.0 12a2.5 2.5 0 1 1 -5.0 0a2.5 2.5 0 1 1 5.0 0",
        "m13.8 17.7 3.9-3.9", "M14.5 19.5a2.5 2.5 0 1 1 -5.0 0a2.5 2.5 0 1 1 5.0 0")
    val Approvals = lucide("Approvals", "M22 12a10 10 0 1 1 -20 0a10 10 0 1 1 20 0", "m9 12 2 2 4-4")
    val Usage = lucide("Usage", "m12 14 4-4", "M3.34 19a10 10 0 1 1 17.32 0")
    val Settings = lucide("Settings", "M20 7h-9", "M14 17H5", "M20 17a3 3 0 1 1 -6 0a3 3 0 1 1 6 0", "M10 7a3 3 0 1 1 -6 0a3 3 0 1 1 6 0")
    // The browser: Lucide Globe, Star, RotateCw, Monitor, ChevronLeft and ChevronRight; it shares Lock with Files.
    val Browser = lucide("Browser", RING, "M12 2a14.5 14.5 0 0 0 0 20a14.5 14.5 0 0 0 0-20", "M2 12h20")
    val Extensions = lucide("Extensions", "M15.39 4.39a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68l1.683 1.682a2.414 2.414 0 0 1 0 3.414L19.61 15.39a1 1 0 0 1-1.68-.474 2.5 2.5 0 1 0-3.014 3.015 1 1 0 0 1 .474 1.68l-1.683 1.682a2.414 2.414 0 0 1-3.414 0L8.61 19.61a1 1 0 0 0-1.68.474 2.5 2.5 0 1 1-3.014-3.015 1 1 0 0 0 .474-1.68l-1.683-1.682a2.414 2.414 0 0 1 0-3.414L4.39 8.61a1 1 0 0 1 1.68.474 2.5 2.5 0 1 0 3.014-3.015 1 1 0 0 1-.474-1.68l1.683-1.682a2.414 2.414 0 0 1 3.414 0z")
    val Kanban = lucide("Kanban", "M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z M8 7v7 M12 7v4 M16 7v9")
    val Star = lucide("Star", "M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z")
    val Reload = lucide("Reload", "M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8", "M21 3v5h-5")
    val Computer = lucide("Computer", "M4 3h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "M8 21h8", "M12 17v4")
    val PageBack = lucide("PageBack", "m15 18-6-6 6-6")
    val PageForward = lucide("PageForward", "m9 18 6-6-6-6")
    val History = icon("History", "M3 11 A9 9 0 1 1 5 18 M3 4 L3 11 L10 11 M12 7 L12 12 L16 14")
    // Composer controls follow desktop ChatComposer: Mic, Paperclip and Send.
    val Mic = lucide("Microphone", "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z", "M19 10v2a7 7 0 0 1-14 0v-2", "M12 19v3")
    val AttachFile = lucide("AttachFile", "M13.234 20.252 21 12.3",
        "m16 6-8.414 8.586a2 2 0 0 0 0 2.828 2 2 0 0 0 2.828 0l8.414-8.586a4 4 0 0 0 0-5.656 4 4 0 0 0-5.656 0l-8.415 8.585a6 6 0 1 0 8.486 8.486")
    val Send = lucide("Send", "M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z",
        "m21.854 2.147-10.94 10.939")
    val FileText = lucide("File", "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z", "M14 2v4a2 2 0 0 0 2 2h4", "M10 9H8", "M16 13H8", "M16 17H8")
    val Copy = lucide("Copy", "M10 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2z", "M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2")
    val Download = lucide("Download", "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M7 10l5 5 5-5", "M12 15V3")
    val Image = lucide("Image", "M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21",
        "M7 9a2 2 0 1 0 4 0a2 2 0 1 0-4 0")
    // The paperclip's sources: Lucide Camera, Clock and Folder.
    val Camera = lucide("Camera", "M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z",
        "M9 13a3 3 0 1 0 6 0a3 3 0 1 0-6 0")
    val Recent = lucide("Recent", RING, "M12 6v6l4 2")
    val Folder = lucide("Folder", "M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z")
    val Close = lucide("Close", "M18 6 6 18", "m6 6 12 12")
    val Music = lucide("Music", "M9 18V5l12-2v13", "M9 18a3 3 0 1 1 -6 0a3 3 0 1 1 6 0", "M21 16a3 3 0 1 1 -6 0a3 3 0 1 1 6 0")
    val Link = lucide("Link", "M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71")
    val Lock = lucide("Lock", "M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z", "M7 11V7a5 5 0 0 1 10 0v4")
    val WrapText = lucide("WrapText", "M3 6h18", "M3 12h15a3 3 0 1 1 0 6h-4", "m16 16-2 2 2 2", "M3 18h7")
    val Code = lucide("Code", "m16 18 6-6-6-6", "m8 6-6 6 6 6")
    val Eye = lucide("Eye", "M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0", "M15 12a3 3 0 1 1 -6 0a3 3 0 1 1 6 0")
    val Archive = icon("Archive", "M3 3 L21 3 L21 7 L3 7 Z M5 7 L5 21 L19 21 L19 7 M9 11 L15 11")
    // Section and row icons follow desktop ThreadSections: Pin, Check, Trash2, ChevronRight.
    val Pin = icon("Pin", "M12 17 L12 22 M9 4 L15 4 M10 4 L10 10 L6 15 L6 17 L18 17 L18 15 L14 10 L14 4")
    val Check = icon("Check", "M20 6 L9 17 L4 12")
    // Lucide CheckCheck, the desktop's Mark as read icon.
    val CheckCheck = lucide("CheckCheck", "M18 6 7 17l-5-5", "m22 10-7.5 7.5L13 16")
    val Trash = icon("Trash", "M3 6 L21 6 M8 6 L8 4 L16 4 L16 6 M6 6 L7 21 L17 21 L18 6 M10 11 L10 17 M14 11 L14 17")
    val ChevronRight = icon("ChevronRight", "M9 6 L15 12 L9 18")
    // Show more and Show less on a long Rem message, as on the desktop.
    val ChevronDown = icon("ChevronDown", "M6 9 L12 15 L18 9")
    val ChevronUp = icon("ChevronUp", "M18 15 L12 9 L6 15")
    // Workflow statuses and controls: Lucide Workflow, CircleCheck, CircleX, LoaderCircle, Clock, CircleAlert, CircleStop, CircleMinus, Circle, Play, Square, RotateCcw.
    private const val RING = "M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20"
    val Workflow = lucide("Workflow", "M5 3h4a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "M7 11v4a2 2 0 0 0 2 2h4",
        "M15 13h4a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2z")
    val Succeeded = lucide("Succeeded", RING, "m9 12 2 2 4-4")
    val Failed = lucide("Failed", RING, "m15 9-6 6", "m9 9 6 6")
    val Running = lucide("Running", "M21 12a9 9 0 1 1-6.219-8.56")
    val Queued = lucide("Queued", RING, "M12 6v6l4 2")
    val Waiting = lucide("Waiting", RING, "M12 8v4", "M12 16h.01")
    val Stopped = lucide("Stopped", RING, "M9 9h6v6H9z")
    val Skipped = lucide("Skipped", RING, "M8 12h8")
    val NotStarted = lucide("NotStarted", RING)
    // Board ticket statuses on ticket pills (lucide CircleDot, CircleSlash, CircleDashed), with Succeeded, Waiting and NotStarted.
    val CircleDot = lucide("CircleDot", RING, "M13 12a1 1 0 1 1 -2 0a1 1 0 1 1 2 0")
    val CircleSlash = lucide("CircleSlash", RING, "M9 15 15 9")
    val CircleDashed = lucide("CircleDashed", "M10.1 2.182a10 10 0 0 1 3.8 0", "M13.9 21.818a10 10 0 0 1-3.8 0", "M17.609 3.721a10 10 0 0 1 2.69 2.7",
        "M2.182 13.9a10 10 0 0 1 0-3.8", "M20.279 17.609a10 10 0 0 1-2.7 2.69", "M21.818 10.1a10 10 0 0 1 0 3.8", "M3.721 6.391a10 10 0 0 1 2.7-2.69",
        "M6.391 20.279a10 10 0 0 1-2.69-2.7")
    val PauseCircle = lucide("PauseCircle", RING, "M10 15V9", "M14 15V9")
    // Rem task types, as the desktop's task list draws them (lucide Telescope, Wrench, Hammer, MousePointerClick, BellRing).
    val Research = lucide("Research", "m10.065 12.493-6.18 1.318a.934.934 0 0 1-1.108-.702l-.537-2.15a1.07 1.07 0 0 1 .691-1.265l13.504-4.44",
        "m13.56 11.747 4.332-.924", "m16 21-3.105-6.21",
        "M16.485 5.94a2 2 0 0 1 1.455-2.425l1.09-.272a1 1 0 0 1 1.212.727l1.515 6.06a1 1 0 0 1-.727 1.213l-1.09.272a2 2 0 0 1-2.425-1.455z",
        "m6.158 8.633 1.114 4.456", "m8 21 3.105-6.21", "M14 13a2 2 0 1 1 -4 0a2 2 0 1 1 4 0")
    val Maintain = lucide("Maintain", "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z")
    val Develop = lucide("Develop", "m15 12-8.373 8.373a1 1 0 1 1-3-3L12 9", "m18 15 4-4",
        "m21.5 11.5-1.914-1.914A2 2 0 0 1 19 8.172V7l-2.26-2.26a6 6 0 0 0-4.202-1.756L9 2.96l.92.82A6.18 6.18 0 0 1 12 8.4V10l2 2h1.172a2 2 0 0 1 1.414.586L18.5 14.5")
    val Operations = lucide("Operations", "M14 4.1 12 6", "m5.1 8-2.9-.8", "m6 12-1.9 2", "M7.2 2.2 8 5.1",
        "M9.037 9.69a.498.498 0 0 1 .653-.653l11 4.5a.5.5 0 0 1-.074.949l-4.349 1.041a1 1 0 0 0-.74.739l-1.04 4.35a.5.5 0 0 1-.95.074z")
    val Reminder = lucide("Reminder", "M10.268 21a2 2 0 0 0 3.464 0", "M22 8c0-2.3-.8-4.3-2-6",
        "M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326", "M4 2C2.8 3.7 2 5.7 2 8")
    val Play = lucide("Play", "M6 3 20 12 6 21 6 3z")
    val Pause = lucide("Pause", "M7 4h2a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z", "M15 4h2a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-2a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z")
    // The media bar: Lucide SkipBack and SkipForward.
    val SkipBack = lucide("SkipBack", "M17.971 4.285A2 2 0 0 1 21 6v12a2 2 0 0 1-3.029 1.715l-9.997-5.998a2 2 0 0 1-.003-3.432z", "M3 20V4")
    val SkipForward = lucide("SkipForward", "M21 4v16", "M6.029 4.285A2 2 0 0 0 3 6v12a2 2 0 0 0 3.029 1.715l9.997-5.998a2 2 0 0 0 .003-3.432z")
    val Video = lucide("Video", "m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5", "M4 6h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z")
    val Stop = lucide("Stop", "M7 5h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z")
    val Resume = lucide("Resume", "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5")
}
