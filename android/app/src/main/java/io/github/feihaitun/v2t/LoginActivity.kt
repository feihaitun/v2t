package io.github.feihaitun.v2t

import android.annotation.SuppressLint
import android.os.Bundle
import android.view.ViewGroup
import android.webkit.*
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import java.net.URI

class LoginActivity : ComponentActivity() {
    private lateinit var browser: WebView
    private var currentSite: Site? = null
    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val site = runCatching { Site.valueOf(intent.getStringExtra("site") ?: "") }.getOrNull() ?: run { finish(); return }
        currentSite = site
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(0xfff6f7f2.toInt()) }
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom); insets
        }
        val controls = LinearLayout(this)
        val close = Button(this).apply { text = "保存并返回"; setOnClickListener { Sessions(this@LoginActivity).saveWebSession(site); Sessions(this@LoginActivity).clearImport(site); finish() } }
        val desktop = CheckBox(this).apply { text = "电脑版网页" }
        controls.addView(close); controls.addView(desktop)
        val address = TextView(this).apply { textSize = 12f; setPadding(16, 8, 16, 8) }
        root.addView(controls); root.addView(address)
        browser = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.setSupportMultipleWindows(false)
            settings.mediaPlaybackRequiresUserGesture = true
            webChromeClient = WebChromeClient()
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    val uri = request.url
                    return uri.scheme != "https" || !site.loginHost(uri.host.orEmpty())
                }
                override fun onPageFinished(view: WebView, url: String) {
                    address.text = "${site.label}官方网页 · ${runCatching { URI(url).host }.getOrDefault("")}\n登录完成后点「保存并返回」"
                }
            }
        }
        val mobileUa = browser.settings.userAgentString
        desktop.setOnCheckedChangeListener { _, checked ->
            browser.settings.userAgentString = if (checked) Engine.DESKTOP_UA else mobileUa
            browser.settings.useWideViewPort = checked; browser.settings.loadWithOverviewMode = checked
            browser.reload()
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(browser, false)
        root.addView(browser, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)
        browser.loadUrl(site.login)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (browser.canGoBack()) browser.goBack() else finish() }
        })
    }
    override fun onStop() { currentSite?.let { runCatching { Sessions(this).saveWebSession(it) } }; super.onStop() }
    override fun onDestroy() { if (::browser.isInitialized) { browser.stopLoading(); browser.destroy() }; super.onDestroy() }
}
