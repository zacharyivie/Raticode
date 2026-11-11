// Build-tool transitive fixes only. These dependencies are not added to the APK.
buildscript {
    repositories { google() }
    dependencies {
        // Kotlin 2.4 metadata needs R8 >= 9.1.29. AGP 9.0.1 bundles an older R8.
        // https://developer.android.com/build/kotlin-support
        classpath("com.android.tools:r8:9.1.43")
    }
    configurations.classpath {
        resolutionStrategy.eachDependency {
            when {
                requested.group == "org.bouncycastle" && requested.name in setOf("bcprov-jdk18on", "bcpkix-jdk18on", "bcutil-jdk18on") -> useVersion("1.85")
                requested.group == "org.bitbucket.b_c" && requested.name == "jose4j" -> useVersion("0.9.6")
                requested.group == "org.apache.commons" && requested.name == "commons-lang3" -> useVersion("3.18.0")
                requested.group == "org.jdom" && requested.name == "jdom2" -> useVersion("2.0.6.1")
            }
        }
    }
}

plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.compose.compiler) apply false
}
