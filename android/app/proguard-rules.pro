# ProGuard rules for Wisp Android App

# Keep DataStore
-keepclassmembers class * extends androidx.datastore.preferences.protobuf.GeneratedMessageLite {
    <fields>;
}

# Keep Kotlin serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** { *; }
-keepclassmembers class kotlinx.serialization.** { *; }

# Keep Ktor client
-keep class io.ktor.** { *; }
-dontwarn io.ktor.**

# Keep sealed classes and their subclasses for serialization
-keep class com.wisp.app.WispMessage { *; }
-keep class com.wisp.app.*Message { *; }
-keep class com.wisp.app.*Item { *; }
-keep class com.wisp.app.*Listing { *; }
-keep class com.wisp.app.*Content { *; }
-keep class com.wisp.app.*Result { *; }
-keep class com.wisp.app.*Info { *; }
-keep class com.wisp.app.*Response { *; }

# Keep ViewModel
-keep class * extends androidx.lifecycle.ViewModel { *; }

# Keep Compose
-keep class androidx.compose.** { *; }
-dontwarn androidx.compose.**

# General Android
-keep public class * extends android.app.Application
-keep public class * extends android.app.Activity

# Remove logging in release
-assumenosideeffects class android.util.Log {
    public static boolean isLoggable(java.lang.String, int);
    public static int v(...);
    public static int i(...);
    public static int w(...);
    public static int d(...);
}
