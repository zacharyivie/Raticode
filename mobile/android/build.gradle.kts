// Build-tool transitive fixes only. These dependencies are not added to the APK.
buildscript {
    // Kotlin 2.4 metadata needs R8 >= 9.1.29 (https://developer.android.com/build/kotlin-support).
    // AGP 9.5.1 bundles R8 9.5.29, so R8 is no longer pinned here; a classpath pin would override
    // AGP's own copy, even with an older version.
    repositories { google() }
    configurations.classpath {
        resolutionStrategy.eachDependency {
            when {
                requested.group == "org.bouncycastle" && requested.name in setOf("bcprov-jdk18on", "bcpkix-jdk18on", "bcutil-jdk18on") -> useVersion("1.85")
                requested.group == "org.bitbucket.b_c" && requested.name == "jose4j" -> useVersion("0.9.6")
                requested.group == "org.apache.commons" && requested.name == "commons-lang3" -> useVersion("3.18.0")
                requested.group == "org.jdom" && requested.name == "jdom2" -> useVersion("2.0.6.1")
                // CVE-2026-102554 (GHSA-xxph-c9ww-hj94): Guava before 33.7.2 can be made to allocate gigabytes while deserializing.
                requested.group == "com.google.guava" && requested.name == "guava" -> useVersion("33.8.0-jre")
            }
        }
    }
}

plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.compose.compiler) apply false
}
