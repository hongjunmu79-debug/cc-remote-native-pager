import java.util.Properties
import java.io.File

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
        applicationId = "dev.ccremote.lan"
        minSdk = 26
        targetSdk = 36
        versionCode = providers.environmentVariable("PAGER_VERSION_CODE")
            .orNull?.toIntOrNull() ?: 30_010
        versionName = providers.environmentVariable("PAGER_VERSION_NAME")
            .orNull ?: "3.0.0-pager.1"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField(
            "String",
            "DEFAULT_SERVER_URL",
            "\"${providers.environmentVariable("PAGER_DEFAULT_URL").orNull ?: "http://192.168.3.4:8766/"}\"",
        )
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
        disable += setOf(
            "AndroidGradlePluginVersion",
            "GradleDependency",
            "NewerVersionAvailable",
            "ObsoleteSdkInt",
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
