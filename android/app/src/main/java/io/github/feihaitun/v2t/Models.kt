package io.github.feihaitun.v2t

import java.net.URI
import java.util.Locale
import org.json.JSONArray
import org.json.JSONObject

enum class Site(val label: String, val domain: String, val login: String) {
    BILI("哔哩哔哩", "bilibili.com", "https://passport.bilibili.com/login"),
    XHS("小红书", "xiaohongshu.com", "https://www.xiaohongshu.com/explore"),
    X("X / Twitter", "x.com", "https://x.com/i/flow/login"),
    YOUTUBE("YouTube / 油管", "youtube.com", "https://www.youtube.com/");

    fun owns(host: String): Boolean = host == domain || host.endsWith(".$domain") ||
        (this == X && (host == "twitter.com" || host.endsWith(".twitter.com"))) ||
        (this == XHS && (host == "rednote.com" || host.endsWith(".rednote.com")))

    fun loginHost(host: String): Boolean = owns(host) ||
        (this == YOUTUBE && host in setOf("accounts.google.com","consent.google.com"))

    companion object {
        fun fromUrl(url: String): Site? {
            val host = runCatching { URI(url).host?.lowercase(Locale.ROOT) }.getOrNull() ?: return null
            return entries.firstOrNull { it.owns(host) } ?: when (host) {
                "b23.tv" -> BILI
                "xhslink.com", "www.xhslink.com", "xhslink.cn", "www.xhslink.cn" -> XHS
                "t.co" -> X
                "youtu.be", "www.youtu.be" -> YOUTUBE
                else -> null
            }
        }
    }
}

object Links {
    fun mediaUrl(site: Site, url: String): String {
        if (site != Site.X) return url
        val uri = URI(url)
        val post = Regex("^/[^/]+/status/[0-9]+").find(uri.path.orEmpty())?.value ?: return url
        // X treats /video/N and /photo/N differently for animated GIFs. Read the
        // parent post and let the user choose its actual media entries.
        return "https://${uri.host}$post"
    }
    fun extract(text: String): String {
        val found = Regex("https?://[^\\s<>\"\\u3000，。！？；）】]+").find(text)?.value
            ?.trimEnd('。', '，', '！', '？', ')', '）', ']', '】', ';', '；')
            ?: throw IllegalArgumentException("请粘贴小红书、B站、X 或 YouTube 的视频分享链接")
        val uri = URI(found)
        require(uri.scheme == "https" && uri.userInfo == null && uri.port in listOf(-1, 443)) { "请使用平台的 HTTPS 分享链接" }
        require(Site.fromUrl(found) != null) { "目前支持小红书、哔哩哔哩、X 和 YouTube" }
        return found
    }
}

data class Variant(val id: String, val width: Int, val height: Int, val bytes: Long,
                   val codec: String, val hasAudio: Boolean, val url: String = "", val audioId: String = "") {
    val label: String get() = buildString {
        append(if (width > 0 && height > 0) "${width}×${height}" else "原始画质")
        if (codec.isNotBlank()) append(" · ${codec.substringBefore('.')}")
        if (bytes > 0) append(" · 约 ${"%.1f".format(Locale.US, bytes / 1_000_000.0)} MB")
    }
    val selector: String get() = if (!hasAudio && audioId.isNotEmpty()) "$id+$audioId" else id
}
data class Subtitle(val language: String, val label: String, val url: String = "", val data: String = "", val ext: String = "srt")
data class MediaEntry(val id: String, val title: String, val duration: Double,
                      val variants: List<Variant>, val subtitles: List<Subtitle>,
                      val downloadUrl: String, val audioId: String = "", val headers: Map<String, String> = emptyMap(), val playlistIndex: Int = 0)
data class MediaInfo(val site: Site, val source: String, val entries: List<MediaEntry>)
enum class Mode(val label: String) {
    VIDEO("只下载视频"), BOTH("视频＋字幕"), TEXT("只要字幕"), AUDIO("只下载音频")
}
data class Cue(val start: Double, val end: Double, val text: String)
object Captions {
    fun srt(cues: List<Cue>): String = cues.filter { it.text.isNotBlank() && it.end > it.start }.mapIndexed { i, c ->
        "${i + 1}\n${time(c.start)} --> ${time(c.end)}\n${c.text.trim()}\n"
    }.joinToString("\n")
    private fun time(seconds: Double): String {
        val ms = (seconds.coerceAtLeast(0.0) * 1000).toLong()
        return "%02d:%02d:%02d,%03d".format(Locale.US, ms / 3600000, ms / 60000 % 60, ms / 1000 % 60, ms % 1000)
    }
    fun parse(text: String): List<Cue> {
        if (text.trimStart().startsWith("{")) {
            val body = JSONObject(text).optJSONArray("body") ?: return emptyList()
            return body.objects().map { Cue(it.optDouble("from"), it.optDouble("to"), it.optString("content")) }
        }
        val timeLine = Regex("(?:(\\d+):)?(\\d{2}):(\\d{2})[.,](\\d{3})\\s*-->\\s*(?:(\\d+):)?(\\d{2}):(\\d{2})[.,](\\d{3})[^\\n]*\\n(.*?)(?=\\n\\s*\\n|$)", RegexOption.DOT_MATCHES_ALL)
        fun stamp(m: MatchResult, offset: Int) = (m.groupValues[offset].toIntOrNull() ?: 0) * 3600.0 +
            m.groupValues[offset + 1].toInt() * 60 + m.groupValues[offset + 2].toInt() + m.groupValues[offset + 3].toInt() / 1000.0
        return timeLine.findAll(text.replace("\r\n", "\n")).map {
            Cue(stamp(it, 1), stamp(it, 5), it.groupValues[9].replace(Regex("<[^>]+>"), "").trim())
        }.toList()
    }
}
fun JSONArray.objects(): List<JSONObject> = (0 until length()).mapNotNull { optJSONObject(it) }
fun JSONObject.str(key: String): String = if (isNull(key)) "" else optString(key, "")

object NetworkRules {
    // Some platform payloads still advertise HTTP CDN URLs. Upgrade before any request;
    // never enable cleartext traffic or send credentials over HTTP.
    fun secureUrl(site: Site?, url: String): String {
        val uri = URI(url)
        if (uri.scheme != "http") return url
        require(site != null && uri.userInfo == null && uri.port in listOf(-1,80) && allowed(site,uri.host.orEmpty())) { "平台返回了不支持的资源地址" }
        return "https://${uri.host}${uri.rawPath.orEmpty()}" + (uri.rawQuery?.let { "?$it" } ?: "")
    }
    fun allowed(site: Site, host: String): Boolean {
        val suffixes = when (site) {
            Site.BILI -> listOf("bilibili.com","b23.tv","hdslb.com","bilivideo.com","bilivideo.cn","akamaized.net")
            Site.X -> listOf("x.com","twitter.com","t.co","twimg.com")
            Site.XHS -> listOf("xiaohongshu.com","rednote.com","xhslink.com","xhslink.cn","xhscdn.com","xiaohongshu.cn","rednotecdn.com")
            Site.YOUTUBE -> listOf("youtube.com","youtu.be","googlevideo.com","ytimg.com")
        }
        return suffixes.any { host == it || host.endsWith(".$it") }
    }
}
