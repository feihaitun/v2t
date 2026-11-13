package io.github.feihaitun.v2t

import android.content.Context
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.util.concurrent.CancellationException
import org.json.JSONArray
import org.json.JSONObject

class Engine(private val context: Context, private val sessions: Sessions) {
    companion object {
        const val DESKTOP_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        const val MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
    }
    @Volatile var cancelled = false
    @Volatile private var activeProcess: Process? = null
    @Volatile private var activeConnection: HttpURLConnection? = null
    @Volatile private var processId: String? = null
    fun check() { if (cancelled || Thread.currentThread().isInterrupted) throw CancellationException("已停止") }
    fun cancel() { cancelled = true; activeConnection?.disconnect(); activeProcess?.destroyForcibly(); processId?.let { YoutubeDL.getInstance().destroyProcessById(it) } }
    fun init() {
        synchronized(Engine::class.java) {
            YoutubeDL.getInstance().init(context)
            val prefs = context.getSharedPreferences("runtime",Context.MODE_PRIVATE)
            if (prefs.getString("bundled","unknown") != "2026.10.26") {
                val target = File(context.noBackupFilesDir,"youtubedl-android/yt-dlp/yt-dlp")
                target.parentFile?.mkdirs()
                val temp = File(target.parentFile,"yt-dlp.install")
                context.resources.openRawResource(R.raw.ytdlp).use { input -> temp.outputStream().use { input.copyTo(it) } }
                check(temp.renameTo(target)) { "下载解析器初始化失败" }
                prefs.edit().putString("bundled","2026.10.26").apply()
            }
            FFmpeg.getInstance().init(context)
        }
    }
    fun update() { init(); YoutubeDL.getInstance().updateYoutubeDL(context, YoutubeDL.UpdateChannel.STABLE) }
    fun runtimeVersion(): String { init(); return YoutubeDL.getInstance().execute(YoutubeDLRequest(emptyList()).addOption("--version")).out.trim() }

    private fun connection(rawUrl: String, site: Site?, headers: Map<String, String> = emptyMap()): HttpURLConnection {
        var url = rawUrl
        repeat(8) {
            check()
            url = NetworkRules.secureUrl(site,url)
            val uri = URI(url)
            require(uri.scheme == "https" && uri.userInfo == null && uri.port in listOf(-1, 443)) { "平台返回了不支持的资源地址" }
            val host = uri.host ?: error("资源地址无效")
            require(host.contains('.') && host != "localhost" && !Regex("[0-9.]+|.*:.*").matches(host)) { "资源地址无效" }
            require(site != null && NetworkRules.allowed(site,host)) { "平台返回了未支持的资源域名" }
            val conn = URL(url).openConnection() as HttpURLConnection
            conn.instanceFollowRedirects = false
            conn.connectTimeout = 20_000; conn.readTimeout = 30_000
            conn.setRequestProperty("User-Agent", headers["User-Agent"] ?: DESKTOP_UA)
            headers.filterKeys { it.equals("Referer", true) || it.equals("Accept-Language", true) }.forEach { (k,v) -> conn.setRequestProperty(k,v) }
            if (site.owns(host)) sessions.cookie(site, url).takeIf { it.isNotEmpty() }?.let { conn.setRequestProperty("Cookie", it) }
            activeConnection = conn
            val code = conn.responseCode
            if (code in listOf(301,302,303,307,308)) {
                val next = conn.getHeaderField("Location") ?: error("链接跳转失败")
                url = URI(url).resolve(next).toString(); conn.disconnect()
            } else {
                if (code !in 200..299) { conn.disconnect(); throw IllegalStateException(when (code) {
                    401,403 -> "平台拒绝访问，请登录该平台后重试（$code）"
                    404 -> "资源不存在或已失效（404）"
                    429 -> "平台暂时限制了请求，请稍后重试（429）"
                    else -> "资源请求失败（HTTP $code）"
                }) }
                return conn
            }
        }
        error("分享链接跳转次数过多")
    }
    fun text(url: String, site: Site?, headers: Map<String, String> = emptyMap(), maxBytes: Int = 8_000_000): String {
        val conn = connection(url, site, headers)
        return try { conn.inputStream.use { input ->
            val output = java.io.ByteArrayOutputStream()
            val buffer = ByteArray(16384)
            while (true) {
                check(); val n = input.read(buffer); if (n < 0) break
                require(output.size() + n <= maxBytes) { "平台返回的数据过大" }; output.write(buffer,0,n)
            }
            output.toByteArray().toString(Charsets.UTF_8)
        } } finally { conn.disconnect(); activeConnection = null }
    }
    private fun resolve(url: String, site: Site): String {
        if (site.owns(URI(url).host.orEmpty())) return url
        val conn = connection(url, site, mapOf("User-Agent" to MOBILE_UA))
        return try { conn.url.toString().also { require(site.owns(URI(it).host.orEmpty())) { "分享链接没有跳转到对应平台" } } }
        finally { conn.disconnect(); activeConnection = null }
    }
    fun inspect(input: String): MediaInfo {
        val original = Links.extract(input)
        val site = Site.fromUrl(original)!!
        val url = Links.mediaUrl(site,resolve(original, site))
        val path = URI(url).path.orEmpty()
        require(when (site) {
            Site.BILI -> Regex("/video/(BV[0-9A-Za-z]+|av[0-9]+)").containsMatchIn(path)
            Site.X -> Regex("/status/[0-9]+").containsMatchIn(path)
            Site.XHS -> path.contains("/explore/") || path.contains("/discovery/item/")
            Site.YOUTUBE -> (path == "/watch" && Regex("(?:^|&)v=[A-Za-z0-9_-]{11}(?:&|$)").containsMatchIn(URI(url).rawQuery.orEmpty())) ||
                Regex("^/(shorts|live|embed)/[A-Za-z0-9_-]{11}/?$").matches(path)
        }) { "请分享具体的视频或帖子链接，暂不支持主页和合集" }
        check()
        if (site == Site.XHS) return xhs(url)
        init()
        val request = YoutubeDLRequest(url).addOption("--dump-single-json").addOption("--skip-download")
            .addOption("--no-playlist").addOption("--playlist-end", 8).addOption("--no-warnings")
            .addOption("--socket-timeout", 20).addOption("--retries", 1)
            .addOption("--write-subs").addOption("--write-auto-subs").addOption("--ignore-no-formats-error")
        val raw = sessions.withCookieFile(site) { cookie ->
            cookie?.let { request.addOption("--cookies", it.absolutePath) }
            execute(request, "inspect").out
        }
        val root = JSONObject(raw.trim())
        require(!root.optBoolean("is_live") && root.str("live_status") != "is_upcoming") { "首版支持单条视频和回放，请使用具体的视频链接" }
        val sourceEntries = root.optJSONArray("entries")?.objects() ?: listOf(root)
        val entries = sourceEntries.take(8).mapIndexed { entryIndex, json ->
            val all = json.optJSONArray("formats")?.objects().orEmpty()
            val audio = all.lastOrNull { it.str("vcodec") == "none" && it.str("acodec") != "none" }
            val audioId = audio?.str("format_id").orEmpty()
            val variants = all.filter { it.str("vcodec") != "none" && it.str("url").isNotBlank() && !it.optBoolean("has_drm") }
                .map { f -> Variant(f.str("format_id"), f.optInt("width"), f.optInt("height"),
                    f.optLong("filesize", f.optLong("filesize_approx")), f.str("vcodec").takeUnless { it == "unknown" }.orEmpty(),
                    f.str("acodec").let { it != "none" && it.isNotBlank() }, audioId = audioId) }
                .filter { it.id.matches(Regex("[A-Za-z0-9_.-]+")) }
                .sortedWith(compareByDescending<Variant> { it.width.toLong() * it.height }.thenByDescending { it.bytes })
            val subs = mutableListOf<Subtitle>()
            listOf("subtitles", "automatic_captions").forEach { key ->
                json.optJSONObject(key)?.let { languages -> languages.keys().forEach { lang ->
                    if (lang !in listOf("danmaku", "live_chat") && subs.none { it.language == lang }) {
                        val choices = languages.optJSONArray(lang)?.objects().orEmpty()
                        val f = choices.firstOrNull { it.str("ext") == "srt" } ?: choices.firstOrNull { it.str("ext") == "vtt" } ?: choices.firstOrNull { it.str("ext") == "json" }
                        if (f != null) subs += Subtitle(lang, "$lang · ${if (key == "subtitles") "平台字幕" else "平台自动字幕"}", f.str("url"), f.str("data"), f.str("ext"))
                    }
                } }
            }
            val orderedSubs = subs.sortedWith(compareBy<Subtitle> {
                when { it.label.endsWith("· 平台字幕") -> 0; it.language.endsWith("-orig") -> 1; else -> 2 }
            }.thenBy { when { it.language.startsWith("zh") -> 0; it.language.startsWith("en") -> 1; else -> 2 } })
            MediaEntry(json.str("id"), json.str("title").ifBlank { "视频" }, json.optDouble("duration", 0.0), variants, orderedSubs, url, audioId,
                playlistIndex = if (root.optJSONArray("entries") != null) entryIndex + 1 else 0)
        }
        require(entries.isNotEmpty()) { "平台没有返回视频资料" }
        return MediaInfo(site, url, entries)
    }
    private fun execute(request: YoutubeDLRequest, id: String, progress: ((Float, Long, String) -> Unit)? = null): com.yausername.youtubedl_android.YoutubeDLResponse {
        check(); processId = id
        return try { YoutubeDL.getInstance().execute(request, id, progress) }
        finally { processId = null }
    }
    private fun xhs(url: String): MediaInfo {
        val html = text(url, Site.XHS, mapOf("User-Agent" to MOBILE_UA))
        val json = Regex("window\\.__INITIAL_STATE__\\s*=\\s*(\\{.*?\\})\\s*</script>", RegexOption.DOT_MATCHES_ALL).find(html)?.groupValues?.get(1)
            ?: error("小红书未返回笔记数据。请保留完整分享链接，或在「账号」中登录后重试")
        val state = JSONObject(replaceUndefined(json))
        val direct = state.optJSONObject("noteData")?.optJSONObject("data")?.optJSONObject("noteData")
        val map = state.optJSONObject("note")?.optJSONObject("noteDetailMap")
        val note = direct ?: map?.keys()?.asSequence()?.mapNotNull { map.optJSONObject(it)?.optJSONObject("note") }?.firstOrNull()
            ?: error("小红书笔记已失效或当前账号无法查看")
        require(note.str("type") == "video") { "这是图文笔记，请分享视频笔记" }
        val video = note.optJSONObject("video") ?: error("没有视频资源")
        val streams = video.optJSONObject("media")?.optJSONObject("stream") ?: error("没有可下载的清晰度")
        val variants = streams.keys().asSequence().flatMap { streams.optJSONArray(it)?.objects().orEmpty() }.filter { it.str("masterUrl").isNotBlank() }
            .mapIndexed { i, s -> Variant("xhs-$i", s.optInt("width"), s.optInt("height"), s.optLong("size"), s.str("videoCodec"), true, s.str("masterUrl")) }
            .sortedByDescending { it.width.toLong() * it.height }.toList()
        val mediaV2 = when (val v = video.opt("mediaV2")) { is JSONObject -> v; is String -> runCatching { JSONObject(v) }.getOrNull(); else -> null }?.optJSONObject("video")
        val subtitles = mutableListOf<Subtitle>()
        mediaV2?.optJSONObject("subtitles")?.let { subs -> subs.keys().forEach { lang ->
            subs.optJSONArray(lang)?.objects()?.firstOrNull { it.str("url").isNotEmpty() }?.let { subtitles += Subtitle(lang, "$lang · 平台字幕", it.str("url")) }
        } }
        return MediaInfo(Site.XHS, url, listOf(MediaEntry(note.str("noteId"), note.str("title").ifBlank { note.str("desc").take(80) }.ifBlank { "小红书视频" },
            0.0, variants, subtitles, url)))
    }
    fun download(info: MediaInfo, entry: MediaEntry, variant: Variant?, mode: Mode, dir: File, report: (String) -> Unit): File {
        dir.mkdirs(); check()
        if (info.site == Site.XHS) {
            val selected = variant ?: entry.variants.lastOrNull() ?: error("没有视频资源")
            val output = File(dir, "media.mp4")
            val conn = connection(selected.url, info.site)
            try {
                val size = conn.contentLengthLong
                conn.inputStream.use { input -> output.outputStream().use { out ->
                    val buf = ByteArray(128 * 1024); var downloaded = 0L; var last = 0L
                    while (true) {
                        check(); val n = input.read(buf); if (n < 0) break; out.write(buf, 0, n); downloaded += n
                        if (System.currentTimeMillis() - last > 700) { report("正在下载 ${downloaded / 1_000_000} MB${if (size > 0) " / ${size / 1_000_000} MB" else ""}"); last = System.currentTimeMillis() }
                    }
                } }
            } finally { conn.disconnect(); activeConnection = null }
            return output
        }
        init()
        val selector = if (mode in listOf(Mode.AUDIO, Mode.TEXT)) entry.audioId.ifBlank { variant?.selector ?: "best" }
            else variant?.selector ?: error("请选择可用的清晰度")
        val request = YoutubeDLRequest(entry.downloadUrl).addOption("--no-playlist").addOption("--no-warnings")
            .addOption("--newline").addOption("--socket-timeout", 25).addOption("--retries", 3)
            .addOption("--fragment-retries", 3).addOption("--no-mtime")
            .addOption("--concurrent-fragments",4)
            .addOption("-f", selector).addOption("-o", File(dir, "media.%(ext)s").absolutePath)
            .addOption("--merge-output-format", "mp4").addOption("--http-chunk-size", "8M")
        if (entry.playlistIndex > 0) request.addOption("--yes-playlist").addOption("--playlist-items",entry.playlistIndex)
        sessions.withCookieFile(info.site) { file ->
            file?.let { request.addOption("--cookies", it.absolutePath) }
            execute(request, "download") { percent, _, _ -> report(if (percent in 0f..100f) "正在下载 ${percent.toInt()}%" else "正在下载或合并音视频…") }
        }
        check()
        return dir.listFiles()?.filter { it.name.startsWith("media.") && it.extension in listOf("mp4","mkv","webm","m4a","mp3","opus","ogg","aac","flac","wav") }?.maxByOrNull { it.length() }
            ?: error("下载没有生成完整媒体文件")
    }
    fun subtitles(subtitle: Subtitle, site: Site, sourceUrl: String = "", playlistIndex: Int = 0): Pair<List<Cue>, String> {
        // Let the maintained extractor handle YouTube's client headers and caption URLs.
        if (site == Site.YOUTUBE && sourceUrl.isNotBlank()) return downloadSubtitles(subtitle,site,sourceUrl,playlistIndex)
        val raw = subtitle.data.ifBlank { text(if (subtitle.url.startsWith("//")) "https:${subtitle.url}" else subtitle.url, site, maxBytes = 4_000_000) }
        if (raw.trimStart().startsWith("#EXTM3U") && sourceUrl.isNotBlank()) return downloadSubtitles(subtitle,site,sourceUrl,playlistIndex)
        return Captions.parse(raw) to raw
    }
    private fun downloadSubtitles(subtitle: Subtitle, site: Site, sourceUrl: String, playlistIndex: Int): Pair<List<Cue>,String> {
        init()
        val dir = File(context.cacheDir,"subtitle-${java.util.UUID.randomUUID()}").apply { mkdirs() }
        try {
            val request = YoutubeDLRequest(sourceUrl).addOption("--skip-download").addOption("--no-playlist")
                .addOption("--write-subs").addOption("--write-auto-subs").addOption("--sub-langs",subtitle.language)
                .addOption("--sub-format","srt/vtt/best").addOption("--convert-subs","srt")
                .addOption("--socket-timeout",20).addOption("--retries",2).addOption("-o",File(dir,"caption.%(ext)s").absolutePath)
            if (playlistIndex > 0) request.addOption("--yes-playlist").addOption("--playlist-items",playlistIndex)
            sessions.withCookieFile(site) { file ->
                file?.let { request.addOption("--cookies",it.absolutePath) }; execute(request,"subtitles")
            }
            val result = dir.listFiles()?.firstOrNull { it.extension == "srt" } ?: dir.listFiles()?.firstOrNull { it.extension == "vtt" }
                ?: error("平台字幕分段下载失败")
            val converted = result.readText()
            return Captions.parse(converted) to converted
        } finally { dir.deleteRecursively() }
    }
    fun ffmpeg(args: List<String>, consume: ((java.io.InputStream) -> Unit)? = null) {
        init(); check()
        val root = File(context.noBackupFilesDir, "youtubedl-android/packages")
        val builder = ProcessBuilder(listOf(File(context.applicationInfo.nativeLibraryDir, "libffmpeg.so").absolutePath, "-nostdin", "-hide_banner", "-loglevel", "error") + args)
        builder.environment()["LD_LIBRARY_PATH"] = listOf(File(context.applicationInfo.nativeLibraryDir), File(root,"ffmpeg/usr/lib"), File(root,"python/usr/lib")).joinToString(":") { it.absolutePath }
        val process = builder.start(); activeProcess = process
        val sink = object : java.io.OutputStream() { override fun write(b: Int) {}; override fun write(b: ByteArray,off: Int,len: Int) {} }
        val errors = Thread { process.errorStream.use { it.copyTo(sink) } }.apply { start() }
        try {
            process.inputStream.use { if (consume != null) consume(it) else it.copyTo(sink) }
            check(); val code = process.waitFor(); require(code == 0) { "媒体处理失败，请检查文件是否含有音轨（$code）" }
        } finally { process.destroy(); errors.join(2000); activeProcess = null }
    }
    fun transcribe(file: File, report: (String) -> Unit): List<Cue> {
        require(XAsr.installed(context)) { "手机上还没有识别模型，请到「设置」导入模型" }
        val recognizer = XAsr(XAsr.modelsDirectory(context))
        val cues = mutableListOf<Cue>()
        report("正在加载中英识别模型…")
        try {
            recognizer.load()
            ffmpeg(listOf("-i", file.absolutePath, "-vn", "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1")) { input ->
                val bytes = ByteArray(3200); var samplesRead = 0L; var last = 0L
                while (true) {
                    check(); var n = 0
                    while (n < bytes.size) { val count = input.read(bytes, n, bytes.size - n); if (count < 0) break; n += count }
                    if (n == 0) break
                    val samples = FloatArray(n / 2) { i -> ((bytes[2*i].toInt() and 255) or (bytes[2*i+1].toInt() shl 8)).toShort() / 32768f }
                    recognizer.accept(samples).filter { it.isFinal }.forEach { cues += Cue(it.start,it.end,it.text) }
                    samplesRead += samples.size
                    if (System.currentTimeMillis() - last > 800) { report("正在本地转写 · 已识别 ${samplesRead / 16000} 秒"); last = System.currentTimeMillis() }
                }
            }
            recognizer.finish()?.let { cues += Cue(it.start,it.end,it.text) }
            require(cues.isNotEmpty()) { "没有识别到人声。文件可能只有音乐或没有音轨" }
            return cues
        } finally { recognizer.release() }
    }
    fun hasAudio(file: File): Boolean? = runCatching {
        val media = android.media.MediaExtractor()
        try { media.setDataSource(file.absolutePath); (0 until media.trackCount).any { media.getTrackFormat(it).getString(android.media.MediaFormat.KEY_MIME)?.startsWith("audio/") == true } }
        finally { media.release() }
    }.getOrNull()
}

/** Replace JS undefined only outside quoted strings; never change the subtitle or title itself. */
fun replaceUndefined(raw: String): String {
    val out = StringBuilder(); var i = 0; var quoted = false; var escaped = false
    while (i < raw.length) {
        val c = raw[i]
        if (!quoted && raw.startsWith("undefined", i) && (i == 0 || !raw[i-1].isLetterOrDigit()) && (i+9 == raw.length || !raw[i+9].isLetterOrDigit())) { out.append("null"); i += 9; continue }
        out.append(c)
        if (quoted) { if (escaped) escaped = false else if (c == '\\') escaped = true else if (c == '"') quoted = false }
        else if (c == '"') quoted = true
        i++
    }
    return out.toString()
}
