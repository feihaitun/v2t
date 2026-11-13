package io.github.feihaitun.v2t

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.spec.MGF1ParameterSpec
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.OAEPParameterSpec
import javax.crypto.spec.PSource
import javax.crypto.spec.SecretKeySpec

/** USB transport contains ciphertext only. The receiving key never leaves Android Keystore. */
object SessionTransfer {
    private const val ALIAS = "v2t-usb-transfer-v1"
    private val importing = java.util.concurrent.atomic.AtomicBoolean(false)
    const val INBOX = "session-sync-inbox.json"
    private fun store() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    @Synchronized fun prepare(context: Context) {
        val store = store()
        if (!store.containsAlias(ALIAS)) KeyPairGenerator.getInstance("RSA","AndroidKeyStore").apply {
            initialize(KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(2048).setDigests(KeyProperties.DIGEST_SHA256)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_OAEP).build())
        }.generateKeyPair()
        val key = store().getCertificate(ALIAS).publicKey.encoded
        val json = JSONObject().put("version",1).put("publicKey",Base64.encodeToString(key,Base64.NO_WRAP))
            .put("fingerprint",fingerprint(key))
        File(context.filesDir,"session-sync-key.json").writeText(json.toString())
    }
    private fun fingerprint(key: ByteArray) = MessageDigest.getInstance("SHA-256").digest(key).joinToString("") { "%02x".format(it.toInt() and 255) }
    private fun bytes(json: JSONObject, key: String) = Base64.decode(json.getString(key),Base64.NO_WRAP)

    fun receive(context: Context, sessions: Sessions, input: File = File(context.filesDir,INBOX)): List<Site> {
        require(input.isFile && input.length() in 1..1_500_000L) { "没有待接收的电脑会话，请在电脑运行同步工具" }
        try {
            val envelope = JSONObject(input.readText())
            require(envelope.getInt("version") == 1) { "电脑同步工具版本不匹配" }
            val store = store()
            require(envelope.getString("fingerprint") == fingerprint(store.getCertificate(ALIAS).publicKey.encoded)) { "登录资料不是发给这台手机的，请重新同步" }
            val rsa = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding")
            rsa.init(Cipher.DECRYPT_MODE,store.getKey(ALIAS,null),OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA1,PSource.PSpecified.DEFAULT))
            val rawKey = rsa.doFinal(bytes(envelope,"wrappedKey"))
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE,SecretKeySpec(rawKey,"AES"),GCMParameterSpec(128,bytes(envelope,"nonce")))
            cipher.updateAAD("V2T-session-sync-v1".toByteArray())
            val payload = JSONObject(String(cipher.doFinal(bytes(envelope,"ciphertext")),Charsets.UTF_8))
            rawKey.fill(0)
            require(payload.getString("requestId") == envelope.getString("requestId")) { "同步资料不完整" }
            val entries = payload.getJSONArray("sites").objects()
            require(entries.size in 1..Site.entries.size && entries.map { it.str("site") }.distinct().size == entries.size) { "同步资料格式不正确" }
            val items = entries.map { Site.valueOf(it.getString("site")) to it.getString("cookies") }
            sessions.importMany(items)
            return items.map { it.first }
        } finally { input.delete() }
    }
    fun receiveInApp(app: V2tApp) {
        val input = File(app.filesDir,INBOX)
        if (!input.isFile) {
            app.change { it.copy(error = "",status = "请连接电脑并运行会话同步工具，手机会自动保存收到的登录态") }; return
        }
        if (!importing.compareAndSet(false,true)) return
        Thread {
            val requestId = runCatching { JSONObject(input.readText()).optString("requestId") }.getOrDefault("")
            val result = JSONObject().put("requestId",requestId)
            try {
                require(!app.screen.value.busy) { "请等当前下载或转写结束后再同步" }
                val sites = receive(app,app.sessions,input)
                result.put("ok",true).put("imported",JSONArray(sites.map { it.name }))
                app.change { it.copy(error = "",status = "已保存 ${sites.joinToString("、") { s -> s.label }} 的登录态，关闭 App 或重启手机仍保留；请重新解析视频") }
            } catch (e: Exception) {
                input.delete(); result.put("ok",false).put("error","同步未完成，请重试；原有登录资料仍会保留")
                app.change { it.copy(error = result.getString("error")) }
            } finally {
                File(app.filesDir,"session-sync-result.json").writeText(result.toString())
                importing.set(false)
            }
        }.apply { name = "v2t-session-import"; start() }
    }
}
