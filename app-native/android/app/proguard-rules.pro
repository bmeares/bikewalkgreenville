# Flutter embedding + plugins reached via reflection/JNI. Flutter's own R8
# rules cover the engine; these cover what this app links natively.

# MapLibre GL native bindings (JNI callbacks into Java classes by name).
-keep class org.maplibre.** { *; }
-dontwarn org.maplibre.**

# Geolocator's bound service is looked up by class name.
-keep class com.baseflow.geolocator.** { *; }

# flutter_local_notifications deserializes its own types.
-keep class com.dexterous.flutterlocalnotifications.** { *; }

# Play Core is referenced by Flutter's deferred-components support but not
# bundled here.
-dontwarn com.google.android.play.core.**
