package io.github.feihaitun.v2t

import android.app.*
import android.content.*
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.*
import android.provider.MediaStore
import androidx.core.app.NotificationCompat
import androidx.core.content.FileProvider
import kotlinx.coroutines.flow.MutableStateFlow
import java.io.File
import java.util.UUID
import java.util.concurrent.CancellationException
import java.util.zip.ZipInputStream
import org.json.JSONArray
import org.json.JSONObject

data class Record(val id: String, val title: String, val state: String, val message: String,
                  val created: Long, val source: String, val outputs: List<String>)
data class ScreenState(val busy: Boolean = false, val status: String = "", val error: String = "", val info: MediaInfo? = null,
                       val records: List<Record> = emptyList(), val incoming: String = "")
data class TaskSpec(val info: MediaInfo? = null, val entryIndex: Int = 0, val variantIndex: Int = 0,
                    val subtitleIndex: Int = 0, val mode: Mode = Mode.VIDEO, val localUri: Uri? = null,
                    val recognize: Boolean = true)
class V2tApp : Application() {
    val screen = MutableStateFlow(ScreenState())
    var pending: TaskSpec? = null
    lateinit var sessions: Sessions
    lateinit var store: Records
    override fun onCreate() {
        super.onCreate(); sessions = Sessions(this); store = Records(this)
        cacheDir.listFiles()?.filter { it.name.startsWith("session-") }?.forEach { it.delete() }
        screen.value = screen.value.copy(records = store.load(recover = true))
        Thread { runCatching { SessionTransfer.prepare(this) } }.start()
    }
    @Synchronized fun change(fn: (ScreenState) -> ScreenState) { screen.value = fn(screen.value) }
    fun refresh() { change { it.copy(records = store.load()) } }
}
class Records(private val context: Context) {
    val root = File(context.filesDir, "jobs").apply { mkdirs() }
    fun directory(id: String): File { require(id.matches(Regex("[A-Za-z0-9-]+"))); return File(root, id).apply { mkdirs() } }
    @Synchronized fun save(record: Record) {
        val dir = directory(record.id)
        val json = JSONObject().put("id",record.id).put("title",record.title).put("state",record.state).put("message",record.message)
            .put("created",record.created).put("source",record.source).put("outputs",JSONArray(record.outputs))
        val temp = File(dir,"record.tmp"); temp.writeText(json.toString()); check(temp.renameTo(File(dir,"record.json")))
    }
    @Synchronized fun load(recover: Boolean = false): List<Record> = root.listFiles().orEmpty().mapNotNull { dir ->
        runCatching {
            val json = JSONObject(File(dir,"record.json").readText())
            val state = json.getString("state")
            Record(dir.name, json.getString("title"), if (recover && state == "running") "interrupted" else state,
                if (recover && state == "running") "任务中断，可以重新解析链接后重试" else json.optString("message"),
                json.getLong("created"),json.optString("source"),json.getJSONArray("outputs").let { array ->
                    (0 until array.length()).map { array.getString(it) }.filter { !it.contains('/') && File(dir,it).isFile }
                }).also { if (recover && state == "running") save(it) }
        }.getOrNull()
    }.sortedByDescending { it.created }
}

class TaskService : Service() {
    companion object {
        const val CHANNEL = "tasks"
        fun start(context: Context, action: String, text: String = "") {
            val app = context.applicationContext as V2tApp
            if (action != "cancel") {
                if (app.screen.value.busy) return
                app.change { it.copy(busy = true, error = "", status = "正在准备…") }
            }
            val intent = Intent(context, TaskService::class.java).setAction(action).putExtra("text",text)
            try { if (action == "cancel") context.startService(intent) else context.startForegroundService(intent) }
            catch (e: Exception) { app.change { it.copy(busy = false, error = "系统没有允许启动任务，请保持 App 在前台后重试") } }
        }
    }
    private val app get() = application as V2tApp
    private lateinit var engine: Engine
    private var worker: Thread? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var notificationAt = 0L
    override fun onBind(intent: Intent?) = null
    override fun onCreate() {
        super.onCreate(); engine = Engine(this, app.sessions)
        getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel(CHANNEL,"下载与转写",NotificationManager.IMPORTANCE_LOW))
    }
    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(this,0,Intent(this,MainActivity::class.java),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val cancel = PendingIntent.getService(this,1,Intent(this,TaskService::class.java).setAction("cancel"),PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this,CHANNEL).setSmallIcon(io.github.feihaitun.v2t.R.drawable.ic_app).setContentTitle("V2T 视频下载")
            .setContentText(text).setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true).addAction(0,"停止",cancel).build()
    }
    private fun report(text: String) {
        engine.check(); app.change { it.copy(status = text) }
        if (System.currentTimeMillis() - notificationAt > 800) {
            getSystemService(NotificationManager::class.java).notify(1,notification(text)); notificationAt = System.currentTimeMillis()
        }
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "cancel") { engine.cancel(); app.change { it.copy(status = "正在停止…") }; return START_NOT_STICKY }
        if (worker?.isAlive == true) return START_NOT_STICKY
        val action = intent?.action ?: run { stopSelf(); return START_NOT_STICKY }
        val spec = app.pending; app.pending = null
        try { startForeground(1,notification("正在准备…"), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC) }
        catch (e: Exception) { app.change { it.copy(busy = false,error = "系统没有允许后台任务，请重新打开 App 后重试") }; stopSelf(); return START_NOT_STICKY }
        wakeLock = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"v2t:task").apply { acquire(2 * 60 * 60 * 1000L) }
        engine.cancelled = false
        worker = Thread {
            try {
                when (action) {
                    "inspect" -> {
                        report("正在读取视频、清晰度和平台字幕…")
                        val info = engine.inspect(intent.getStringExtra("text").orEmpty())
                        app.change { it.copy(info = info, status = "已找到 ${info.entries.size} 项媒体") }
                    }
                    "download" -> { requireNotNull(spec) { "任务已过期，请重新选择" }; runTask(spec) }
                    "update" -> { report("正在更新下载解析器…"); engine.update(); app.change { it.copy(status = "下载解析器已更新") } }
                    "models" -> { importModels(Uri.parse(intent.getStringExtra("text"))); app.change { it.copy(status = "中英识别模型已就绪") } }
                }
            } catch (e: Throwable) {
                val message = if (engine.cancelled || e is CancellationException) "已停止，可重新开始" else userError(e)
                app.change { it.copy(error = message, status = "") }
            } finally {
                if (wakeLock?.isHeld == true) wakeLock?.release()
                app.change { it.copy(busy = false) }; app.refresh()
                stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
            }
        }.apply { name = "v2t-task"; start() }
        return START_NOT_STICKY
    }
    private fun runTask(spec: TaskSpec) {
        val entry = spec.info?.entries?.getOrNull(spec.entryIndex)
        val id = UUID.randomUUID().toString(); val dir = app.store.directory(id)
        var record = Record(id,entry?.title ?: "本地音视频转写","running","正在处理",System.currentTimeMillis(),spec.info?.source.orEmpty(),emptyList())
        app.store.save(record); app.refresh()
        val outputs = mutableListOf<String>()
        try {
            val needsText = spec.mode in listOf(Mode.TEXT, Mode.BOTH) || spec.localUri != null
            var cues: List<Cue> = emptyList()
            var subtitleSource = ""
            var subtitleFailure = ""
            if (needsText && entry != null && entry.subtitles.isNotEmpty()) {
                report("正在获取平台原字幕…")
                try {
                    val selected = entry.subtitles[spec.subtitleIndex.coerceIn(entry.subtitles.indices)]
                    val result = engine.subtitles(selected,spec.info!!.site,entry.downloadUrl,entry.playlistIndex)
                    cues = result.first
                    if (cues.isNotEmpty()) {
                        val raw = "platform." + when { result.second.trimStart().startsWith("{") -> "json"; result.second.trimStart().startsWith("WEBVTT") -> "vtt"; else -> "srt" }
                        File(dir,raw).writeText(result.second); outputs += raw; subtitleSource = selected.label
                    } else subtitleFailure = "平台字幕没有可用文字"
                } catch (e: Exception) { engine.check(); subtitleFailure = "平台字幕获取失败：${userError(e)}" }
            }
            val needMedia = spec.localUri != null || spec.mode in listOf(Mode.VIDEO,Mode.AUDIO,Mode.BOTH) || (needsText && cues.isEmpty() && spec.recognize)
            if (needsText && cues.isEmpty() && spec.recognize && spec.mode == Mode.TEXT) require(XAsr.installed(this)) { "未获取到平台字幕，且手机尚未安装识别模型。请先导入模型，或关闭自动转写" }
            var media: File? = null
            if (needMedia) {
                media = if (spec.localUri != null) {
                    report("正在读取本地文件…")
                    File(dir,"local-media").also { out ->
                        contentResolver.openInputStream(spec.localUri)?.use { input -> out.outputStream().use { stream ->
                            val buffer = ByteArray(128 * 1024)
                            while (true) { engine.check(); val n = input.read(buffer); if (n < 0) break; stream.write(buffer,0,n) }
                        } } ?: error("无法读取文件，请重新选择")
                    }
                } else engine.download(spec.info!!,entry!!,entry.variants.getOrNull(spec.variantIndex),spec.mode,dir,::report)
                if (spec.mode in listOf(Mode.VIDEO,Mode.BOTH)) outputs += media.name
                if (spec.mode == Mode.AUDIO) {
                    report("正在提取音频…")
                    val audio = File(dir,"audio.m4a")
                    engine.ffmpeg(listOf("-y","-i",media.absolutePath,"-vn","-c:a","aac","-b:a","160k",audio.absolutePath))
                    outputs += audio.name; if (media != audio) media.delete()
                }
            }
            if (needsText && cues.isEmpty() && spec.recognize && media != null && !XAsr.installed(this)) subtitleFailure = "视频已完成；安装模型后可转写，或选择已有平台字幕"
            if (needsText && cues.isEmpty() && media != null && engine.hasAudio(media) == false) subtitleFailure = if (spec.mode == Mode.BOTH) "没有音轨；视频或动态图已保存" else "文件没有音轨，无法生成人声文字稿"
            if (needsText && cues.isEmpty() && spec.recognize && media != null && XAsr.installed(this) && engine.hasAudio(media) != false) {
                if (Build.VERSION.SDK_INT >= 35) startForeground(1,notification("正在本地转写…"),ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING)
                try { cues = engine.transcribe(media,::report); subtitleSource = "手机本地识别 · X-ASR · 中英原文" }
                catch (e: Exception) {
                    engine.check()
                    if (spec.mode == Mode.BOTH && outputs.isNotEmpty()) subtitleFailure = "视频已完成，转写未完成：${userError(e)}" else throw e
                }
            }
            if (needsText && cues.isNotEmpty()) {
                File(dir,"transcript.srt").writeText(Captions.srt(cues))
                File(dir,"transcript.txt").writeText(cues.joinToString("\n") { it.text })
                File(dir,"transcript.json").writeText(JSONObject().put("source",subtitleSource).put("segments",JSONArray(cues.map {
                    JSONObject().put("start",it.start).put("end",it.end).put("text",it.text)
                })).toString(2))
                outputs += listOf("transcript.srt","transcript.txt","transcript.json")
            }
            if (spec.mode == Mode.TEXT || spec.localUri != null) media?.delete()
            val message = when {
                cues.isNotEmpty() -> "已完成 · $subtitleSource"
                needsText -> subtitleFailure.ifBlank { "平台未返回字幕；未运行本地识别" }
                else -> "已完成，可保存到手机或分享"
            }
            record = record.copy(state = if (outputs.isEmpty()) "empty" else "done",message = message,outputs = outputs)
            app.store.save(record); app.change { it.copy(status = message) }
        } catch (e: Throwable) {
            record = record.copy(state = if (engine.cancelled) "cancelled" else "failed",message = if (engine.cancelled) "已停止" else userError(e),outputs = outputs)
            app.store.save(record); throw e
        } finally {
            // Retain completed exports only. Cancelled transfers and temporary audio must
            // not accumulate inside the app's private storage.
            dir.listFiles()?.filter { it.name != "record.json" && it.name !in outputs }?.forEach { it.deleteRecursively() }
        }
    }
    private fun importModels(uri: Uri) {
        report("正在导入中英模型…")
        val models = XAsr.modelsDirectory(this)
        val staging = File(models.parentFile,"models-staging").apply { deleteRecursively(); mkdirs() }
        val allowed = mapOf(XAsr.STREAMING to setOf("encoder.int8.onnx","decoder.onnx","joiner.int8.onnx","tokens.txt"),
            XAsr.OFFLINE to setOf("encoder-epoch-99-avg-1.int8.onnx","decoder-epoch-99-avg-1.onnx","joiner-epoch-99-avg-1.int8.onnx","tokens.txt"))
        val checksums = JSONObject(assets.open("model-checksums.json").bufferedReader().use { it.readText() })
        val seen = mutableSetOf<String>()
        try {
            var total = 0L
            contentResolver.openInputStream(uri)?.use { source -> ZipInputStream(source).use { zip ->
                while (true) {
                    engine.check(); val item = zip.nextEntry ?: break
                    val name = item.name.removePrefix("models/"); val parts = name.split('/')
                    if (item.isDirectory) continue
                    val accept = parts.size == 2 && allowed[parts[0]]?.contains(parts[1]) == true
                    if (accept) require(seen.add(name)) { "模型包包含重复文件" }
                    val digest = java.security.MessageDigest.getInstance("SHA-256")
                    val file = if (accept) File(staging,name).apply { parentFile?.mkdirs() } else null
                    file?.outputStream().use { out ->
                        val buffer = ByteArray(128*1024)
                        while (true) {
                            engine.check(); val n = zip.read(buffer); if (n < 0) break
                            total += n; require(total < 1_000_000_000L) { "模型包超过大小限制" }
                            if (accept) { out!!.write(buffer,0,n); digest.update(buffer,0,n) }
                        }
                    }
                    if (accept) require(digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) } == checksums.getString(name)) { "模型校验失败，请使用配套的完整模型包" }
                }
            } } ?: error("无法读取模型包")
            require(allowed.all { (folder,files) -> files.all { File(staging,"$folder/$it").length() > 0 } }) { "模型包不完整，需要 X-ASR 的实时与离线两套模型" }
            val old = File(models.parentFile,"models-previous"); old.deleteRecursively()
            if (models.exists()) require(models.renameTo(old)) { "无法替换旧模型" }
            if (!staging.renameTo(models)) { old.renameTo(models); error("模型保存失败") }
            old.deleteRecursively()
        } finally { staging.deleteRecursively() }
    }
    override fun onTimeout(startId: Int, fgsType: Int) { engine.cancel(); stopSelf() }
    override fun onDestroy() { engine.cancel(); if (wakeLock?.isHeld == true) wakeLock?.release(); super.onDestroy() }
}

fun userError(error: Throwable): String {
    // Never expose extractor output: it can contain cookies or signed media URLs.
    val raw = error.message.orEmpty()
    return when {
        raw.contains("login",true) || raw.contains("cookies",true) || raw.contains("sign in",true) -> "平台要求登录，请在「账号」中登录对应网站后重试"
        raw.contains("403") -> "平台拒绝访问，请重新登录或稍后重试（403）"
        raw.contains("429") -> "平台限制了请求频率，请稍后重试（429）"
        raw.contains("Requested format",true) -> "所选清晰度已失效，请重新解析并选择"
        raw.contains("No video",true) || raw.contains("Unsupported URL",true) -> "没有找到视频，请检查是否为具体的视频帖子链接"
        raw.contains("timed out",true) || raw.contains("Unable to download",true) -> "网络请求失败，请检查网络后重试"
        raw.contains("http",true) || raw.length > 220 || raw.contains("Cookie",true) -> "平台解析失败，请更新下载解析器，或重新登录后重试"
        raw.any { it in '\u4e00'..'\u9fff' } -> raw
        else -> "任务未完成，请检查网络、登录状态，或更新下载解析器"
    }
}

object Exports {
    fun uri(context: Context, record: Record, fileName: String): Uri {
        require(fileName in record.outputs)
        return FileProvider.getUriForFile(context,"${context.packageName}.files",File((context.applicationContext as V2tApp).store.directory(record.id),fileName))
    }
    fun mime(name: String) = when (name.substringAfterLast('.')) { "mp4" -> "video/mp4"; "mkv" -> "video/x-matroska"; "webm" -> "video/webm"; "m4a" -> "audio/mp4"; "mp3" -> "audio/mpeg"; "srt" -> "application/x-subrip"; "json" -> "application/json"; else -> "text/plain" }
    fun save(context: Context, record: Record, fileName: String): Uri {
        require(fileName in record.outputs)
        val mime = mime(fileName)
        val collection = when { mime.startsWith("video") -> MediaStore.Video.Media.EXTERNAL_CONTENT_URI; mime.startsWith("audio") -> MediaStore.Audio.Media.EXTERNAL_CONTENT_URI; else -> MediaStore.Downloads.EXTERNAL_CONTENT_URI }
        val folder = when { mime.startsWith("video") -> "Movies/V2T"; mime.startsWith("audio") -> "Music/V2T"; else -> "Download/V2T" }
        val name = record.title.replace(Regex("[\\\\/:*?\"<>|\\n\\r]"),"_").take(60).ifBlank { "V2T" } + "_" + fileName
        val values = ContentValues().apply { put(MediaStore.MediaColumns.DISPLAY_NAME,name); put(MediaStore.MediaColumns.MIME_TYPE,mime); put(MediaStore.MediaColumns.RELATIVE_PATH,folder); put(MediaStore.MediaColumns.IS_PENDING,1) }
        val uri = context.contentResolver.insert(collection,values) ?: error("无法创建导出文件")
        try {
            context.contentResolver.openOutputStream(uri)?.use { out -> File((context.applicationContext as V2tApp).store.directory(record.id),fileName).inputStream().use { it.copyTo(out) } } ?: error("无法保存文件")
            context.contentResolver.update(uri,ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING,0) },null,null)
        } catch (e: Exception) { context.contentResolver.delete(uri,null,null); throw e }
        return uri
    }
}
