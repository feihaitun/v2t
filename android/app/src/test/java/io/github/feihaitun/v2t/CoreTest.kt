package io.github.feihaitun.v2t

import org.junit.Assert.*
import org.junit.Test

class CoreTest {
    @Test fun shareTextPreservesXhsAccessParameter() {
        assertEquals("https://www.xiaohongshu.com/explore/abc?xsec_token=abc123", Links.extract("分享视频 https://www.xiaohongshu.com/explore/abc?xsec_token=abc123，复制打开"))
    }
    @Test fun rejectLookalikeHostsAndCredentials() {
        listOf("https://bilibili.com.evil.test/video/BV1", "https://user:pass@x.com/user/status/1", "http://x.com/user/status/1").forEach {
            assertThrows(IllegalArgumentException::class.java) { Links.extract(it) }
        }
        assertFalse(NetworkRules.allowed(Site.BILI,"hdslb.com.evil.test"))
        assertFalse(NetworkRules.allowed(Site.BILI,"xiaohongshu.com"))
        assertTrue(NetworkRules.allowed(Site.BILI,"aisubtitle.hdslb.com"))
        assertEquals(Site.XHS,Site.fromUrl("https://www.rednote.com/explore/abc"))
        assertTrue(NetworkRules.allowed(Site.XHS,"www.rednote.com"))
    }
    @Test fun exactVideoSelectionRetainsAudioAndSilentVideo() {
        assertEquals("1080+audio", Variant("1080",1920,1080,0,"h264",false,audioId="audio").selector)
        assertEquals("gif", Variant("gif",480,480,0,"h264",false).selector)
        assertEquals("https://x.com/user/status/123",Links.mediaUrl(Site.X,"https://x.com/user/status/123/photo/1?s=20"))
    }
    @Test fun legacyCdnUrlsUpgradeWithoutChangingSignature() {
        assertEquals("https://sns-video-bd.xhscdn.com/a%2Fb.mp4?sign=a%2Bb&x=1",NetworkRules.secureUrl(Site.XHS,"http://sns-video-bd.xhscdn.com/a%2Fb.mp4?sign=a%2Bb&x=1"))
        assertThrows(IllegalArgumentException::class.java) { NetworkRules.secureUrl(Site.XHS,"http://xhscdn.com.evil.test/a") }
        assertThrows(IllegalArgumentException::class.java) { NetworkRules.secureUrl(Site.XHS,"http://user@xhscdn.com/a") }
    }
    @Test fun parseBilibiliAndWebVttCaptions() {
        val result = Captions.parse("{\"body\":[{\"from\":1.25,\"to\":3.5,\"content\":\"中文 and English\"}]}")
        assertEquals("1\n00:00:01,250 --> 00:00:03,500\n中文 and English\n",Captions.srt(result))
        val vtt = "WEBVTT\n\n00:01.250 --> 00:03.500 align:start\n<b>中文</b> and English\n\n00:04.000 --> 00:05.000\nNext line\n"
        assertEquals(2,Captions.parse(vtt).size)
        assertEquals(result.first(),Captions.parse(vtt).first())
    }
    @Test fun javascriptUndefinedDoesNotDamageQuotedContent() {
        assertEquals("{\"value\":null,\"title\":\"undefined name\"}",replaceUndefined("{\"value\":undefined,\"title\":\"undefined name\"}"))
    }
    @Test fun youtubeLinksAndAccountRedirectsStayScoped() {
        assertEquals(Site.YOUTUBE,Site.fromUrl(Links.extract("油管 https://youtu.be/BaW_jenozKc?si=sample")))
        assertEquals(Site.YOUTUBE,Site.fromUrl("https://m.youtube.com/shorts/BaW_jenozKc"))
        assertTrue(Site.YOUTUBE.loginHost("accounts.google.com"))
        assertFalse(Site.YOUTUBE.owns("accounts.google.com"))
        assertFalse(Site.YOUTUBE.loginHost("accounts.google.com.evil.test"))
        assertFalse(Site.BILI.loginHost("accounts.google.com"))
    }
}
