import java.io.File
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermission
import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.compose.compiler)
}

// Unconfigured release builds are unsigned. Partial configuration is an error,
// never a reason to fall back to the debug key.
// CI sets the four RATICODE_RELEASE_* variables. A local build can instead point
// RATICODE_RELEASE_SIGNING_PROPERTIES at an owner-only properties file outside the
// repository (storeFile, keyAlias, passwordFile), so no password sits in the environment.
val releaseSigningNames = listOf(
    "RATICODE_RELEASE_STORE_FILE", "RATICODE_RELEASE_STORE_PASSWORD",
    "RATICODE_RELEASE_KEY_ALIAS", "RATICODE_RELEASE_KEY_PASSWORD",
)
val signingEnvironment = releaseSigningNames.associateWith { providers.environmentVariable(it).orNull }
val signingPropertiesPath = providers.environmentVariable("RATICODE_RELEASE_SIGNING_PROPERTIES").orNull

fun ownerOnly(file: File, label: String) {
    val posix: Set<PosixFilePermission> = runCatching { Files.getPosixFilePermissions(file.toPath()) }.getOrNull() ?: return
    require(posix.none { permission -> permission.name.startsWith("GROUP_") || permission.name.startsWith("OTHERS_") }) {
        "$label must be readable by its owner only (chmod 600)."
    }
}

val releaseSigning: Map<String, String?> = if (signingPropertiesPath == null) {
    signingEnvironment
} else {
    require(signingEnvironment.values.all { it == null }) {
        "Use RATICODE_RELEASE_SIGNING_PROPERTIES or the RATICODE_RELEASE_* variables, not both."
    }
    val propertiesFile = File(signingPropertiesPath)
    require(propertiesFile.isAbsolute && propertiesFile.isFile && propertiesFile.canRead()) {
        "RATICODE_RELEASE_SIGNING_PROPERTIES must be the absolute path of a readable file."
    }
    ownerOnly(propertiesFile, "The signing properties file")
    val values = Properties().apply { propertiesFile.reader().use { load(it) } }
    val folder = propertiesFile.parentFile
    fun resolve(name: String): File? =
        values.getProperty(name)?.trim()?.takeIf { it.isNotEmpty() }?.let { File(it).takeIf(File::isAbsolute) ?: File(folder, it) }
    val passwordFile = resolve("passwordFile")
    require(passwordFile != null && passwordFile.isFile && passwordFile.canRead()) {
        "The signing properties file needs passwordFile, a readable file holding the keystore password."
    }
    ownerOnly(passwordFile, "The keystore password file")
    val password = passwordFile.readText().trimEnd('\r', '\n')
    mapOf(
        "RATICODE_RELEASE_STORE_FILE" to resolve("storeFile")?.path,
        "RATICODE_RELEASE_STORE_PASSWORD" to password,
        "RATICODE_RELEASE_KEY_ALIAS" to values.getProperty("keyAlias")?.trim(),
        // PKCS12 keystores keep the key under the store's password.
        "RATICODE_RELEASE_KEY_PASSWORD" to password,
    )
}
val hasReleaseSigning = releaseSigning.values.any { it != null }
if (hasReleaseSigning) {
    require(releaseSigning.values.all { !it.isNullOrBlank() }) {
        "Release signing requires all four RATICODE_RELEASE_* signing variables, or storeFile, keyAlias and " +
            "passwordFile in the RATICODE_RELEASE_SIGNING_PROPERTIES file."
    }
    val keyStore = File(releaseSigning.getValue("RATICODE_RELEASE_STORE_FILE")!!)
    require(keyStore.isAbsolute && keyStore.isFile && keyStore.canRead()) {
        "Release keystore must be a readable file."
    }
    ownerOnly(keyStore, "The release keystore")
}

// version.properties is the only place the version is written; docs/versioning.md
// describes how scripts/version.py changes it.
val appVersion = Properties().apply {
    load(providers.fileContents(rootProject.layout.projectDirectory.file("version.properties")).asText.get().reader())
}
val appVersionName: String = appVersion.getProperty("versionName").orEmpty()
val appVersionCode: Int = appVersion.getProperty("versionCode").orEmpty().toIntOrNull() ?: 0
require(Regex("""(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?""").matches(appVersionName)) {
    "version.properties versionName must be MAJOR.MINOR.PATCH with an optional -prerelease."
}
require(appVersionCode in 1..2_100_000_000) {
    "version.properties versionCode must be a whole number from 1 to 2100000000."
}

android {
    namespace = "dev.raticode.mobile"
    // GeckoView needs API 37.2 to compile against (AGP 9.5 checks the minor level too). The app still
    // targets 36, so its behaviour on phones is unchanged.
    compileSdk = 37
    compileSdkMinor = 2

    defaultConfig {
        applicationId = "dev.raticode.mobile"
        minSdk = 26
        targetSdk = 36
        versionCode = appVersionCode
        versionName = appVersionName
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // GeckoView ships a native engine per processor type; these two cover current phones and the emulator.
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
    // Security checks that fail lint instead of warning. A web view without onRenderProcessGone lets a crashing
    // page end the whole app.
    lint { error += listOf("MissingOnRenderProcessGone") }
    // GeckoView's engine is large; compressed in the APK it takes about half the download.
    packaging { jniLibs { useLegacyPackaging = true } }
    sourceSets.getByName("test").resources.directories.add("../protocol/v1/fixtures")
    sourceSets.getByName("test").resources.directories.add("../protocol/probes/public-test-identities")
    sourceSets.getByName("test").kotlin.srcDir("src/testShared/java")
    sourceSets.getByName("androidTest").kotlin.srcDir("src/testShared/java")
    sourceSets.getByName("androidTest").assets.srcDir("../protocol/probes/public-test-identities")
    if (hasReleaseSigning) {
        signingConfigs.create("release") {
            storeFile = file(releaseSigning.getValue("RATICODE_RELEASE_STORE_FILE")!!)
            storePassword = releaseSigning.getValue("RATICODE_RELEASE_STORE_PASSWORD")
            keyAlias = releaseSigning.getValue("RATICODE_RELEASE_KEY_ALIAS")
            keyPassword = releaseSigning.getValue("RATICODE_RELEASE_KEY_PASSWORD")
        }
    }
    buildTypes {
        // A debug install says so in Android's app info, so it is never mistaken for a release.
        debug { versionNameSuffix = "-debug" }
        release {
            signingConfig = if (hasReleaseSigning) signingConfigs.getByName("release") else null
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
}

// GeckoView asks for a newer androidx.lifecycle, whose ViewModelStore no longer has the put() that the instrumentation
// tests use to clean up their clients. Hold the whole family at this app's own version; GeckoView's tests pass with it.
configurations.configureEach {
    resolutionStrategy.eachDependency {
        if (requested.group == "androidx.lifecycle") useVersion(libs.versions.lifecycle.get())
    }
}

dependencies {
    // CVE-2026-102554: Guava before 33.7.2 can be made to allocate gigabytes while deserializing. GeckoView brings in 33.3.1.
    constraints { implementation(libs.guava) { because("CVE-2026-102554 is fixed in 33.7.2") } }
    implementation(libs.geckoview)
    implementation(libs.androidx.webkit)
    implementation(libs.gson)
    implementation(libs.commonmark)
    implementation(libs.commonmark.tables)
    implementation(libs.commonmark.strikethrough)
    implementation(libs.commonmark.tasklist)
    implementation(libs.zxing.embedded)
    implementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.core)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.viewmodel)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.material)
    implementation(libs.androidx.compose.icons)
    debugImplementation(libs.androidx.compose.test.manifest)
    testImplementation(libs.tink.android)
    androidTestImplementation(libs.tink.android)
    testImplementation(libs.junit)
    testImplementation(libs.json)
    androidTestImplementation(libs.androidx.compose.test)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.junit)
}
