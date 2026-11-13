package io.github.feihaitun.v2t

import android.content.Intent
import android.net.Uri
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Exercises the actual foreground-service pipeline and Android file export. */
@RunWith(AndroidJUnit4::class)
class TaskFlowTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private val app get() = context.applicationContext as V2tApp

    private fun openApp() = instrumentation.startActivitySync(Intent(context,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    private fun awaitTask() {
        val until = android.os.SystemClock.elapsedRealtime() + 120_000
        while (app.screen.value.busy && android.os.SystemClock.elapsedRealtime() < until) Thread.sleep(100)
        assertFalse("foreground task timed out",app.screen.value.busy)
    }
    @Test fun localFileToTranscriptsAndMediaStore() {
        val activity = openApp()
        val before = app.store.load().map { it.id }.toSet()
        var record: Record? = null
        try {
            val sample = File(context.getExternalFilesDir(null),"test.wav")
            assertTrue(sample.isFile)
            instrumentation.runOnMainSync {
                app.pending = TaskSpec(localUri = Uri.fromFile(sample),mode = Mode.TEXT)
                TaskService.start(context,"download")
            }
            awaitTask()
            assertEquals("",app.screen.value.error)
            record = app.store.load().single { it.id !in before }
            assertEquals("done",record.state)
            assertTrue(record.outputs.containsAll(listOf("transcript.srt","transcript.txt","transcript.json")))
            assertFalse(File(app.store.directory(record.id),"local-media").exists())
            val provider = Exports.uri(context,record,"transcript.srt")
            val text = context.contentResolver.openInputStream(provider)!!.bufferedReader().use { it.readText() }
            assertTrue(text.contains(" --> "))
            val saved = Exports.save(context,record,"transcript.srt")
            try {
                assertEquals("application/x-subrip",context.contentResolver.getType(saved))
                assertEquals(text,context.contentResolver.openInputStream(saved)!!.bufferedReader().use { it.readText() })
            } finally { context.contentResolver.delete(saved,null,null) }
        } finally {
            record?.let { app.store.directory(it.id).deleteRecursively() }
            app.refresh(); activity.finish()
        }
    }
    @Test fun invalidModelImportPreservesInstalledModels() {
        val activity = openApp()
        val zip = File(context.cacheDir,"bad-model-test.zip")
        try {
            assertTrue(XAsr.installed(context))
            ZipOutputStream(zip.outputStream()).use {
                it.putNextEntry(ZipEntry("${XAsr.STREAMING}/tokens.txt")); it.write("invalid test payload".toByteArray()); it.closeEntry()
            }
            instrumentation.runOnMainSync { TaskService.start(context,"models",Uri.fromFile(zip).toString()) }
            awaitTask()
            assertTrue(app.screen.value.error.contains("校验失败"))
            assertTrue(XAsr.installed(context))
            assertFalse(File(XAsr.modelsDirectory(context).parentFile,"models-staging").exists())
        } finally { zip.delete(); activity.finish() }
    }
    @Test fun completeModelArchiveImportsSuccessfully() {
        val zip = File(context.getExternalFilesDir(null),"test-models.zip")
        org.junit.Assume.assumeTrue(zip.isFile)
        val activity = openApp()
        try {
            instrumentation.runOnMainSync { TaskService.start(context,"models",Uri.fromFile(zip).toString()) }
            awaitTask()
            assertEquals("",app.screen.value.error)
            assertTrue(XAsr.installed(context))
            assertFalse(File(XAsr.modelsDirectory(context).parentFile,"models-previous").exists())
        } finally { activity.finish() }
    }
}
