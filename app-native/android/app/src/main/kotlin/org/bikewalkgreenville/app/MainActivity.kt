package org.bikewalkgreenville.app

import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Handler
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // ponytail: ToneGenerator instead of an audio plugin + bundled asset —
        // the reroute cue is one double-beep, not a media pipeline.
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "bwg/tone")
            .setMethodCallHandler { call, result ->
                if (call.method == "reroute") {
                    try {
                        // TONE_PROP_ACK: one soft mid-tone — audible over street
                        // noise without the alarm-clock edge BEEP2 had.
                        val tg = ToneGenerator(AudioManager.STREAM_MUSIC, 70)
                        tg.startTone(ToneGenerator.TONE_PROP_ACK, 250)
                        Handler(mainLooper).postDelayed({ tg.release() }, 500)
                        result.success(true)
                    } catch (e: Exception) {
                        result.success(false)
                    }
                } else {
                    result.notImplemented()
                }
            }
    }
}
