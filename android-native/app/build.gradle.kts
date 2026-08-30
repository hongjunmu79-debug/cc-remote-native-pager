import java.util.Properties
import java.io.File
import groovy.json.JsonSlurper

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

// Canonical release metadata (deploy/release-metadata.json) is the single
// source of truth for the package id, version name, and version code. CI
// overrides them through PAGER_* variables only when signing a real release.
val releaseMetadata = JsonSlurper().parse(
    rootProject.file("../deploy/release-metadata.json"),
) as Map<*, *>
val androidMetadata = releaseMetadata["android"] as Map<*, *>
val metadataApplicationId = androidMetadata["application_id"] as String
val metadataVersionName = androidMetadata["version_name"] as String
val metadataVersionCode = (androidMetadata["version_code"] as Number).toInt()

val signingPropertiesPath = providers.environmentVariable("PAGER_SIGNING_PROPERTIES").orNull
val signingPropertiesFile = signingPropertiesPath?.let(rootProject::file)
val signingProperties = Properties().apply {
    signingPropertiesFile?.let { file ->
        require(file.isFile) { "PAGER_SIGNING_PROPERTIES does not reference a file" }
        file.inputStream().use(::load)
    }
}

android {
    namespace = "dev.ccremote.pager"
    compileSdk = 36

    defaultConfig {
        applicationId = providers.environmentVariable("PAGER_APPLICATION_ID")
            .orNull ?: metadataApplicationId
        minSdk = 26
        targetSdk = 36
        versionCode = providers.environmentVariable("PAGER_VERSION_CODE")
            .orNull?.toIntOrNull() ?: metadataVersionCode
        versionName = providers.environmentVariable("PAGER_VERSION_NAME")
            .orNull ?: metadataVersionName
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField(
            "String",
            "DEFAULT_SERVER_URL",
            // Intentionally empty: first launch must let the user enter a
            // server endpoint instead of defaulting to a machine-specific LAN
            // address. CI may inject PAGER_DEFAULT_URL for scripted builds.
            "\"${providers.environmentVariable("PAGER_DEFAULT_URL").orNull ?: ""}\"",
        )
        providers.environmentVariable("PAGER_APP_LABEL").orNull?.let { label ->
            resValue("string", "app_name", label)
        }
    }

    signingConfigs {
        if (signingPropertiesPath != null) {
            create("pagerRelease") {
                val configuredStoreFile = File(
                    requireNotNull(signingProperties.getProperty("storeFile")),
                )
                storeFile = if (configuredStoreFile.isAbsolute) {
                    configuredStoreFile
                } else {
                    requireNotNull(signingPropertiesFile).parentFile.resolve(configuredStoreFile)
                }
                storePassword = requireNotNull(signingProperties.getProperty("storePassword"))
                keyAlias = requireNotNull(signingProperties.getProperty("keyAlias"))
                keyPassword = requireNotNull(signingProperties.getProperty("keyPassword"))
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            if (signingPropertiesPath != null) {
                signingConfig = signingConfigs.getByName("pagerRelease")
            }
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }

    lint {
        abortOnError = true
        checkReleaseBuilds = true
        warningsAsErrors = true
        // Versions are deliberately compatibility-pinned for the production
        // toolchain. Renovation is handled as a separate tested upgrade.
        // OldTargetApi is disabled because the pinned AGP (8.13.2) tops out at
        // compileSdk 36; raising targetSdk to the newest API would require an
        // AGP upgrade the repo does not yet pin, and it would still fail to
        // resolve the `android-37` SDK platform on hosted runners.
        disable += setOf(
            "AndroidGradlePluginVersion",
            "GradleDependency",
            "NewerVersionAvailable",
            "ObsoleteSdkInt",
            "OldTargetApi",
        )
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.webkit)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.camera.core)
    implementation(libs.androidx.camera.camera2)
    implementation(libs.androidx.camera.lifecycle)
    implementation(libs.androidx.camera.view)
    implementation(libs.mlkit.barcode.scanning)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui.tooling.preview)
    debugImplementation(libs.androidx.compose.ui.tooling)

    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
}
