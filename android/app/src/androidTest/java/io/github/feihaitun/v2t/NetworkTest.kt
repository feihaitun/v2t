package io.github.feihaitun.v2t

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import org.json.JSONObject

/** Opt-in real network check. Samples are provisioned separately, never embedded in the APK or repository. */
@RunWith(AndroidJUnit4::class)
class NetworkTest {
    @Test fun realPlatformResources() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val args = InstrumentationRegistry.getArguments()
        val platform = args.getString("site") ?: "bili"
        val file = File(context.getExternalFilesDir(null),"network-samples.json")
        assumeTrue(file.isFile)
        val url = JSONObject(file.readText()).optString(platform)
        assumeTrue(url.isNotEmpty())
        val engine = Engine(context,Sessions(context))
        val output = JSONObject().put("site",platform)
        try {
            val info = engine.inspect(url)
            val entry = info.entries.first()
            output.put("entries",info.entries.size).put("formats",entry.variants.size).put("subtitles",entry.subtitles.size)
            assertTrue("no media variants",entry.variants.isNotEmpty())
            if (args.getString("download") == "true") {
                val dir = File(context.cacheDir,"network-test-$platform").apply { mkdirs() }
                try {
                    val variant = entry.variants.last()
                    if (variant.url.isNotEmpty()) {
                        val resource = java.net.URI(variant.url)
                        output.put("resourceScheme",resource.scheme).put("resourceHost",resource.host).put("resourcePort",resource.port)
                    }
                    val downloaded = engine.download(info,entry,variant,Mode.VIDEO,dir) {}
                    assertTrue(downloaded.length() > 1000)
                    output.put("downloadBytes",downloaded.length()).put("audio",engine.hasAudio(downloaded))
                    val extractor = android.media.MediaExtractor()
                    try {
                        extractor.setDataSource(downloaded.path)
                        val video = (0 until extractor.trackCount).map { extractor.getTrackFormat(it) }.first { it.getString("mime").orEmpty().startsWith("video/") }
                        val width = video.getInteger("width"); val height = video.getInteger("height")
                        output.put("width",width).put("height",height)
                        if (variant.width > 0) assertEquals(variant.width,width)
                        if (variant.height > 0) assertEquals(variant.height,height)
                    } finally { extractor.release() }
                } finally { dir.deleteRecursively() }
            }
            if (entry.subtitles.isNotEmpty()) {
                val subtitles = engine.subtitles(entry.subtitles.first(),info.site,entry.downloadUrl,entry.playlistIndex)
                output.put("subtitleCues",subtitles.first.size)
                assertTrue(subtitles.first.isNotEmpty())
            }
            output.put("ok",true)
        } catch (e: Throwable) {
            output.put("ok",false).put("error",userError(e))
            // Logs contain only a redacted reason, never raw extractor output or credentials.
            throw AssertionError(userError(e))
        } finally {
            File(context.getExternalFilesDir(null),"network-result-$platform.json").writeText(output.toString())
        }
    }
}
