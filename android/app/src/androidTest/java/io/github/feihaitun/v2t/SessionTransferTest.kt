package io.github.feihaitun.v2t

import android.webkit.CookieManager
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.json.JSONObject

@RunWith(AndroidJUnit4::class)
class SessionTransferTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext

    @Test fun importedBilibiliSessionAuthenticates() {
        org.junit.Assume.assumeTrue(InstrumentationRegistry.getArguments().getString("verify_account") == "bili")
        val result = JSONObject(Engine(context,Sessions(context)).text("https://api.bilibili.com/x/web-interface/nav",Site.BILI))
        assertEquals(0,result.getInt("code"))
        assertTrue(result.getJSONObject("data").getBoolean("isLogin"))
    }

    @Test fun pythonEnvelopeAuthenticatesAndImportsAtomically() {
        val fixture = File(context.filesDir,"session-sync-fixture.json")
        org.junit.Assume.assumeTrue(fixture.isFile)
        val sessions = Sessions(context,"usb-test")
        val input = File(context.cacheDir,"usb-test.json")
        try {
            fixture.copyTo(input,true)
            assertEquals(listOf(Site.BILI),SessionTransfer.receive(context,sessions,input))
            assertTrue(sessions.cookie(Site.BILI).contains("SESSDATA=USB_TEST_VALUE"))
            assertFalse(input.exists())
            assertFalse(context.getSharedPreferences("usb-test",0).getString("BILI","")!!.contains("USB_TEST_VALUE"))
            val damaged = JSONObject(fixture.readText())
            val data = android.util.Base64.decode(damaged.getString("ciphertext"),android.util.Base64.NO_WRAP)
            data[0] = (data[0].toInt() xor 1).toByte()
            damaged.put("ciphertext",android.util.Base64.encodeToString(data,android.util.Base64.NO_WRAP))
            input.writeText(damaged.toString())
            assertThrows(Exception::class.java) { SessionTransfer.receive(context,sessions,input) }
            assertTrue(sessions.cookie(Site.BILI).contains("SESSDATA=USB_TEST_VALUE"))
            assertFalse(input.exists())
        } finally { input.delete(); context.getSharedPreferences("usb-test",0).edit().clear().commit() }
    }

    @Test fun savedSessionsSurviveProcessRestart() {
        val phase = InstrumentationRegistry.getArguments().getString("phase")
        org.junit.Assume.assumeTrue(phase == "write" || phase == "read")
        val sessions = Sessions(context,"session-persistence-test")
        if (phase == "write") {
            sessions.import(Site.X,"auth_token=PERSISTENCE_TEST_VALUE")
            fun set(value: String) {
                val latch = CountDownLatch(1)
                instrumentation.runOnMainSync { CookieManager.getInstance().setCookie(Site.BILI.login,value) { latch.countDown() } }
                assertTrue(latch.await(10,TimeUnit.SECONDS))
            }
            set("v2t_persistence_test=stored-test; Path=/; Secure")
            sessions.saveWebSession(Site.BILI)
            set("v2t_persistence_test=; Path=/; Max-Age=0; Secure")
            CookieManager.getInstance().flush()
        } else {
            try {
                assertTrue(sessions.cookie(Site.X).contains("auth_token=PERSISTENCE_TEST_VALUE"))
                assertTrue(sessions.cookie(Site.BILI).contains("v2t_persistence_test=stored-test"))
                assertFalse(CookieManager.getInstance().getCookie(Site.BILI.login).orEmpty().contains("v2t_persistence_test="))
                assertEquals("",sessions.cookie(Site.X,"https://www.youtube.com/"))
            } finally { context.getSharedPreferences("session-persistence-test",0).edit().clear().commit() }
        }
    }
}
