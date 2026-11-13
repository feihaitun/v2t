package io.github.feihaitun.v2t

import android.media.MediaExtractor
import android.media.MediaFormat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import org.json.JSONObject

@RunWith(AndroidJUnit4::class)
class RuntimeTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private val engine get() = Engine(context,Sessions(context))

    @Test fun downloadRuntimeAndAudioVideoMerge() {
        val engine = engine
        val version = engine.runtimeVersion()
        assertTrue(version.matches(Regex("[0-9].*")))
        val dir = File(context.cacheDir,"runtime-test").apply { mkdirs() }
        try {
            val video = File(dir,"video.mp4"); val audio = File(dir,"audio.m4a"); val merged = File(dir,"merged.mp4")
            engine.ffmpeg(listOf("-y","-f","lavfi","-i","color=c=green:s=320x180:r=10","-t","1","-an","-c:v","mpeg4",video.path))
            engine.ffmpeg(listOf("-y","-f","lavfi","-i","sine=frequency=440:sample_rate=16000","-t","1","-c:a","aac",audio.path))
            engine.ffmpeg(listOf("-y","-i",video.path,"-i",audio.path,"-c","copy",merged.path))
            assertEquals(false,engine.hasAudio(video)); assertEquals(true,engine.hasAudio(merged))
            val extractor = MediaExtractor(); extractor.setDataSource(merged.path)
            try { assertEquals(2,extractor.trackCount); val fmt = extractor.getTrackFormat(0); assertEquals(320,fmt.getInteger(MediaFormat.KEY_WIDTH)) }
            finally { extractor.release() }
            File(context.getExternalFilesDir(null),"runtime-result.json").writeText(JSONObject().put("ytDlp",version).put("merge",true).put("silentVideo",true).toString())
        } finally { dir.deleteRecursively() }
    }
    @Test fun cookieScopeEncryptionAndTemporaryFileCleanup() {
        val sessions = Sessions(context,"sessions-test")
        try {
            sessions.import(Site.BILI,"SESSDATA=test-only; bili_jct=test-only")
            assertEquals("",sessions.cookie(Site.BILI,"https://x.com/"))
            assertTrue(sessions.cookie(Site.BILI).contains("SESSDATA=test-only"))
            val stored = context.getSharedPreferences("sessions-test",0).getString("BILI","")!!
            assertFalse(stored.contains("test-only"))
            var temp: File? = null
            runCatching { sessions.withCookieFile(Site.BILI) { temp = it; assertTrue(it!!.readText().contains(".bilibili.com")); throw IllegalStateException("interrupted") } }
            assertFalse(temp!!.exists())
        } finally { sessions.clearImport(Site.BILI) }
    }
    @Test fun mixedLanguageTranscriptionRunsOnPhone() {
        assertTrue("models missing",XAsr.installed(context))
        val sample = File(context.getExternalFilesDir(null),"test.wav")
        assertTrue("test.wav missing",sample.isFile)
        val start = System.nanoTime()
        val cues = engine.transcribe(sample) {}
        assertTrue(cues.isNotEmpty())
        assertTrue(cues.joinToString { it.text }.any { it in '\u4e00'..'\u9fff' })
        assertTrue(cues.zipWithNext().all { (a,b) -> b.start >= a.start && a.end <= b.end })
        File(context.getExternalFilesDir(null),"asr-result.json").writeText(JSONObject().put("elapsedMs",(System.nanoTime()-start)/1_000_000)
            .put("audioSeconds",cues.last().end).put("segments",cues.size).put("pssKb",android.os.Debug.getPss()).toString())
    }
}
