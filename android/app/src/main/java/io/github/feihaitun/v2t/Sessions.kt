package io.github.feihaitun.v2t

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.webkit.CookieManager
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONObject

/** Imported sessions are encrypted at rest; browser cookies stay in this app's WebView sandbox. */
class Sessions(private val context: Context, preferenceName: String = "sessions") {
    private val prefs = context.getSharedPreferences(preferenceName, Context.MODE_PRIVATE)
    private val alias = "v2t-sessions-v1"
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    fun import(site: Site, input: String) {
        importMany(listOf(site to input))
    }
    fun importMany(inputs: List<Pair<Site,String>>) {
        val values = inputs.associate { (site,input) -> site.name to encode(normalize(site,input)) }
        val editor = prefs.edit()
        values.forEach { (name,value) -> editor.putString(name,value) }
        check(editor.commit()) { "登录资料保存失败" }
    }
    private fun normalize(site: Site, input: String): String {
        require(input.length <= 100_000) { "Cookie 内容过长" }
        val cleaned = input.trim()
        val header = if (cleaned.contains('\t')) cleaned.lines().mapNotNull { line ->
            val fields = line.removePrefix("#HttpOnly_").split('\t')
            if (line.startsWith("#") && !line.startsWith("#HttpOnly_")) null
            else if (fields.size >= 7 && site.owns(fields[0].trimStart('.')) &&
                (fields[4].toLongOrNull()?.let { it == 0L || it > System.currentTimeMillis() / 1000 } != false)) "${fields[5]}=${fields[6]}" else null
        }.joinToString("; ") else cleaned.removePrefix("Cookie:").trim()
        require(header.isNotBlank() && !header.contains('\n') && !header.contains('\r') && header.split(';').all {
            val name = it.substringBefore('=').trim()
            it.contains('=') && name.matches(Regex("[A-Za-z0-9_!#$%&'*+.^`|~-]+"))
        }) { "请填写 Cookie 请求头，或该站点的 Netscape Cookie 文件内容" }
        return header
    }
    private fun saveEncrypted(name: String, value: String) {
        check(prefs.edit().putString(name, encode(value)).commit()) { "登录资料保存失败" }
    }
    private fun encode(value: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encoded = JSONObject().put("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .put("data", Base64.encodeToString(cipher.doFinal(value.toByteArray()), Base64.NO_WRAP))
        return encoded.toString()
    }
    private fun readEncrypted(name: String): String = runCatching {
        val saved = JSONObject(prefs.getString(name, null) ?: return "")
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(saved.getString("iv"), Base64.NO_WRAP)))
        String(cipher.doFinal(Base64.decode(saved.getString("data"), Base64.NO_WRAP)))
    }.getOrDefault("")
    private fun hosts(site: Site): List<String> = when (site) {
        Site.BILI -> listOf("bilibili.com","www.bilibili.com","api.bilibili.com","passport.bilibili.com")
        Site.X -> listOf("x.com","twitter.com")
        Site.XHS -> listOf("xiaohongshu.com","www.xiaohongshu.com","rednote.com","www.rednote.com")
        Site.YOUTUBE -> listOf("youtube.com","www.youtube.com","m.youtube.com")
    }
    fun saveWebSession(site: Site) {
        val cookies = CookieManager.getInstance()
        cookies.flush()
        val saved = JSONObject()
        hosts(site).forEach { host -> cookies.getCookie("https://$host/")?.takeIf { it.isNotBlank() }?.let { saved.put(host,it) } }
        saveEncrypted("web-${site.name}",saved.toString())
    }
    fun cookie(site: Site, url: String = site.login): String {
        val host = runCatching { java.net.URI(url).host }.getOrNull() ?: return ""
        if (!site.owns(host)) return ""
        val manual = readEncrypted(site.name)
        if (manual.isNotEmpty()) return manual
        val saved = runCatching { JSONObject(readEncrypted("web-${site.name}")) }.getOrNull()
        val stored = saved?.optString(host).orEmpty().ifEmpty {
            saved?.keys()?.asSequence()?.filter { host.endsWith(".$it") }?.maxByOrNull { it.length }?.let { saved.optString(it) }.orEmpty()
        }
        val live = CookieManager.getInstance().getCookie(url).orEmpty()
        val merged = linkedMapOf<String,String>()
        listOf(stored,live).forEach { header -> header.split(';').forEach { part ->
            val at = part.indexOf('='); if (at > 0) merged[part.take(at).trim()] = part.drop(at + 1)
        } }
        return merged.entries.joinToString("; ") { "${it.key}=${it.value}" }
    }
    fun status(site: Site): String {
        val header = cookie(site).ifEmpty { if (site == Site.XHS) cookie(site,"https://www.rednote.com/") else "" }
        if (header.isEmpty()) return "未保存登录资料"
        val expected = when (site) { Site.BILI -> listOf("SESSDATA="); Site.X -> listOf("auth_token="); Site.XHS -> listOf("web_session="); Site.YOUTUBE -> listOf("SID=","__Secure-3PSID=","SAPISID=") }
        return if (header.split(';').any { part -> expected.any { part.trim().startsWith(it) } }) "已保存登录态 · 有效性将在解析时检查" else "已有网页 Cookie · 可能尚未登录"
    }
    fun clearImport(site: Site) { prefs.edit().remove(site.name).commit() }
    fun clearAll(done: () -> Unit) {
        prefs.edit().clear().commit()
        CookieManager.getInstance().removeAllCookies { CookieManager.getInstance().flush(); done() }
        android.webkit.WebStorage.getInstance().deleteAllData()
    }
    fun <T> withCookieFile(site: Site, action: (File?) -> T): T {
        val rows = hosts(site).flatMap { host -> cookie(site, "https://$host/").split(';').mapNotNull {
            val part = it.trim(); val index = part.indexOf('=')
            if (index <= 0) null else ".$host\tTRUE\t/\tTRUE\t0\t${part.take(index)}\t${part.drop(index + 1)}"
        } }.distinct()
        if (rows.isEmpty()) return action(null)
        val file = File.createTempFile("session-", ".tmp", context.cacheDir)
        return try {
            file.setReadable(false, false); file.setWritable(false, false)
            file.setReadable(true, true); file.setWritable(true, true)
            // Export to parent domain so api.* and media requests use the same site-scoped session.
            file.writeText("# Netscape HTTP Cookie File\n" + rows.map {
                if (site == Site.BILI) it.replace(".www.bilibili.com\t", ".bilibili.com\t").replace(".api.bilibili.com\t", ".bilibili.com\t")
                else if (site == Site.XHS) it.replace(".www.xiaohongshu.com\t", ".xiaohongshu.com\t").replace(".www.rednote.com\t", ".rednote.com\t")
                else if (site == Site.YOUTUBE) it.replace(".www.youtube.com\t", ".youtube.com\t").replace(".m.youtube.com\t", ".youtube.com\t") else it
            }.distinct().joinToString("\n") + "\n")
            action(file)
        } finally { file.delete() }
    }
}
