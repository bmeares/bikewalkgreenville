import java.io.File
import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// SRA upload keystore (app-native/keystore.properties, gitignored; Drive backup).
// Falls back to debug signing when absent.
val keystoreProps = Properties()
val keystorePropsFile = rootProject.file("../keystore.properties")
val hasReleaseSigning = keystorePropsFile.exists() &&
    keystorePropsFile.readText().contains("STORE_PASSWORD=") &&
    !keystorePropsFile.readText().contains("STORE_PASSWORD=<set>")
if (hasReleaseSigning) {
    keystoreProps.load(keystorePropsFile.inputStream())
}

android {
    namespace = "org.bikewalkgreenville.app"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // flutter_local_notifications uses java.time -> needs desugaring.
        isCoreLibraryDesugaringEnabled = true
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        applicationId = "org.bikewalkgreenville.app"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                val sf = keystoreProps.getProperty("STORE_FILE")
                storeFile = if (File(sf).isAbsolute) File(sf)
                    else File(keystorePropsFile.parentFile, sf)
                storePassword = keystoreProps.getProperty("STORE_PASSWORD")
                keyAlias = keystoreProps.getProperty("KEY_ALIAS")
                keyPassword = keystoreProps.getProperty("KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = if (hasReleaseSigning) signingConfigs.getByName("release")
                else signingConfigs.getByName("debug")
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
}

flutter {
    source = "../.."
}
