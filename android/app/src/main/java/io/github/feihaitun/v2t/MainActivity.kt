package io.github.feihaitun.v2t

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : ComponentActivity() {
    private val app get() = application as V2tApp
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        acceptShare(intent)
        setContent {
            MaterialTheme(colorScheme = lightColorScheme(primary = Color(0xff246349), onPrimary = Color.White,
                secondaryContainer = Color(0xffdce9df), background = Color(0xfff6f7f2), surface = Color(0xfffffefa),
                surfaceVariant = Color(0xffedf0e9), onSurface = Color(0xff20322b), outline = Color(0xff82968b))) {
                AppContent()
            }
        }
    }
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); setIntent(intent); acceptShare(intent) }
    private fun acceptShare(intent: Intent?) {
        if (intent?.getBooleanExtra("sync_sessions",false) == true) { SessionTransfer.receiveInApp(app); return }
        if (intent?.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            app.change { it.copy(incoming = intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty()) }
        } else if (intent?.action == Intent.ACTION_SEND && (intent.type?.startsWith("video/") == true || intent.type?.startsWith("audio/") == true)) {
            @Suppress("DEPRECATION") val uri = intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
            if (uri != null && !app.screen.value.busy) { app.pending = TaskSpec(localUri = uri,mode = Mode.TEXT); TaskService.start(this,"download") }
        }
    }
    private fun toast(text: String) { runOnUiThread { Toast.makeText(this,text,Toast.LENGTH_LONG).show() } }
    private fun login(site: Site) { startActivity(Intent(this,LoginActivity::class.java).putExtra("site",site.name)) }

    @OptIn(ExperimentalLayoutApi::class)
    @Composable private fun AppContent() {
        val state by app.screen.collectAsState()
        var tab by rememberSaveable { mutableIntStateOf(0) }
        var input by rememberSaveable { mutableStateOf("") }
        var entryIndex by rememberSaveable { mutableIntStateOf(0) }
        var variantIndex by rememberSaveable { mutableIntStateOf(0) }
        var subtitleIndex by rememberSaveable { mutableIntStateOf(0) }
        var mode by rememberSaveable { mutableStateOf(Mode.VIDEO) }
        var recognize by rememberSaveable { mutableStateOf(true) }
        var showAll by remember { mutableStateOf(false) }
        var importSite by remember { mutableStateOf<Site?>(null) }
        var clearDialog by remember { mutableStateOf(false) }
        var cookieRefresh by remember { mutableIntStateOf(0) }
        val notify = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
        val localPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null && !state.busy) {
                runCatching { contentResolver.takePersistableUriPermission(uri,Intent.FLAG_GRANT_READ_URI_PERMISSION) }
                app.pending = TaskSpec(localUri = uri,mode = Mode.TEXT); TaskService.start(this,"download")
            }
        }
        val modelPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null && !state.busy) TaskService.start(this,"models",uri.toString())
        }
        LaunchedEffect(state.incoming) { if (state.incoming.isNotBlank()) { input = state.incoming; tab = 0; app.change { it.copy(incoming = "") } } }
        LaunchedEffect(state.info) { entryIndex = 0; variantIndex = 0; subtitleIndex = 0; showAll = false }
        LaunchedEffect(entryIndex) { variantIndex = 0; subtitleIndex = 0 }
        DisposableEffect(Unit) {
            val observer = androidx.lifecycle.LifecycleEventObserver { _, event ->
                if (event == androidx.lifecycle.Lifecycle.Event.ON_RESUME) cookieRefresh++
            }
            lifecycle.addObserver(observer); onDispose { lifecycle.removeObserver(observer) }
        }
        fun permission() { if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) notify.launch(Manifest.permission.POST_NOTIFICATIONS) }
        Scaffold(containerColor = MaterialTheme.colorScheme.background, bottomBar = {
            NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                listOf("下载" to "↓", "文件" to "▤", "账号" to "◎", "设置" to "⚙").forEachIndexed { index, pair ->
                    NavigationBarItem(selected = tab == index,onClick = { tab = index },icon = { Text(pair.second,fontSize = 22.sp) },label = { Text(pair.first) })
                }
            }
        }) { padding ->
            LazyColumn(Modifier.fillMaxSize().padding(padding).padding(horizontal = 22.dp),verticalArrangement = Arrangement.spacedBy(16.dp),contentPadding = PaddingValues(top = 22.dp,bottom = 28.dp)) {
                item {
                    Row(Modifier.fillMaxWidth(),verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) { Text("V2T",fontSize = 32.sp,fontWeight = FontWeight.Bold); Text("视频、字幕，随身保存",fontSize = 13.sp,color = MaterialTheme.colorScheme.primary) }
                        Surface(color = MaterialTheme.colorScheme.secondaryContainer,shape = RoundedCornerShape(30.dp)) { Text("手机本地",Modifier.padding(horizontal = 12.dp,vertical = 6.dp),fontSize = 12.sp) }
                    }
                }
                if (state.busy || state.error.isNotEmpty() || state.status.isNotEmpty()) item {
                    Card(colors = CardDefaults.cardColors(containerColor = if (state.error.isNotEmpty()) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.secondaryContainer)) {
                        Column(Modifier.padding(16.dp),verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                            Text(state.error.ifBlank { state.status },fontSize = 14.sp)
                            if (state.busy) TextButton(onClick = { TaskService.start(this@MainActivity,"cancel") }) { Text("停止任务") }
                        }
                    }
                }
                when (tab) {
                    0 -> {
                        item {
                            Panel {
                                Text("保存一个视频",fontSize = 21.sp,fontWeight = FontWeight.SemiBold)
                                Text("小红书 · 哔哩哔哩 · X · YouTube",fontSize = 13.sp)
                                OutlinedTextField(value = input,onValueChange = { input = it },modifier = Modifier.fillMaxWidth(),minLines = 3,maxLines = 5,
                                    placeholder = { Text("粘贴视频链接，或完整的分享文案") },shape = RoundedCornerShape(16.dp),enabled = !state.busy)
                                Button(onClick = {
                                    permission(); app.change { it.copy(info = null) }; TaskService.start(this@MainActivity,"inspect",input)
                                },enabled = !state.busy && input.isNotBlank(),modifier = Modifier.fillMaxWidth().height(50.dp)) { Text("解析视频和字幕") }
                                TextButton(onClick = { localPicker.launch(arrayOf("video/*","audio/*")) },enabled = !state.busy,modifier = Modifier.fillMaxWidth()) { Text("选择手机里的音视频 → 转文字") }
                            }
                        }
                        val info = state.info
                        if (info != null && info.entries.isNotEmpty()) {
                            val entry = info.entries[entryIndex.coerceIn(info.entries.indices)]
                            item {
                                Panel {
                                    Text(info.site.label,color = MaterialTheme.colorScheme.primary,fontSize = 12.sp)
                                    Text(entry.title,fontWeight = FontWeight.SemiBold,fontSize = 20.sp)
                                    if (entry.duration > 0) Text("时长 ${entry.duration.toInt()/60} 分 ${entry.duration.toInt()%60} 秒",fontSize = 13.sp)
                                    if (info.entries.size > 1) FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        info.entries.forEachIndexed { i, _ -> FilterChip(selected = entryIndex == i,onClick = { entryIndex = i },label = { Text("第 ${i+1} 项") },enabled = !state.busy) }
                                    }
                                    TextButton(onClick = { login(info.site) },enabled = !state.busy) { Text("需要更多清晰度或字幕？登录 ${info.site.label}") }
                                    Text("登录后请重新解析，刷新可访问的资源",fontSize = 12.sp,color = MaterialTheme.colorScheme.outline)
                                }
                            }
                            item {
                                Panel {
                                    Text("你要保存什么？",fontWeight = FontWeight.SemiBold)
                                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        Mode.entries.forEach { value -> FilterChip(selected = mode == value,onClick = { mode = value },label = { Text(value.label) },enabled = !state.busy) }
                                    }
                                    if (mode in listOf(Mode.VIDEO,Mode.BOTH)) {
                                        Text("选择画质",fontWeight = FontWeight.SemiBold)
                                        if (entry.variants.isEmpty()) Text("当前没有可下载的视频档位，请登录后重新解析")
                                        entry.variants.take(if (showAll) 60 else 5).forEachIndexed { i, variant ->
                                            Row(Modifier.fillMaxWidth().clickable(enabled = !state.busy) { variantIndex = i },verticalAlignment = Alignment.CenterVertically) {
                                                RadioButton(selected = variantIndex == i,onClick = { variantIndex = i },enabled = !state.busy)
                                                Text(variant.label,fontSize = 14.sp,modifier = Modifier.weight(1f))
                                            }
                                        }
                                        if (entry.variants.size > 5) TextButton(onClick = { showAll = !showAll }) { Text(if (showAll) "收起" else "查看全部 ${entry.variants.size} 档") }
                                    }
                                    if (mode in listOf(Mode.TEXT,Mode.BOTH)) {
                                        if (entry.subtitles.isNotEmpty()) {
                                            Text("平台字幕",fontWeight = FontWeight.SemiBold)
                                            entry.subtitles.take(20).forEachIndexed { i, subtitle ->
                                                Row(verticalAlignment = Alignment.CenterVertically) {
                                                    RadioButton(selected = subtitleIndex == i,onClick = { subtitleIndex = i },enabled = !state.busy)
                                                    Text(subtitle.label,fontSize = 14.sp)
                                                }
                                            }
                                        } else Text("没有发现可用的平台字幕",fontSize = 14.sp)
                                        Row(verticalAlignment = Alignment.CenterVertically) {
                                            Checkbox(checked = recognize,onCheckedChange = { recognize = it },enabled = !state.busy)
                                            Text("没有字幕时，在手机上识别人声",fontSize = 13.sp)
                                        }
                                        Text(if (XAsr.installed(this@MainActivity)) "中英识别模型已就绪" else "还没有识别模型；平台原字幕仍可下载",fontSize = 12.sp,color = MaterialTheme.colorScheme.outline)
                                    }
                                    Button(onClick = {
                                        permission(); app.pending = TaskSpec(info,entryIndex,variantIndex,subtitleIndex,mode,recognize = recognize)
                                        TaskService.start(this@MainActivity,"download")
                                    },enabled = !state.busy && (mode == Mode.TEXT || entry.variants.isNotEmpty()),modifier = Modifier.fillMaxWidth().height(50.dp)) { Text(if (mode == Mode.TEXT) "获取字幕与文字稿" else "开始下载") }
                                    Text("完成后在「文件」中打开、分享或保存到手机",fontSize = 12.sp,color = MaterialTheme.colorScheme.outline)
                                }
                            }
                        }
                    }
                    1 -> {
                        item { Text("我的文件",fontSize = 23.sp,fontWeight = FontWeight.SemiBold) }
                        if (state.records.isEmpty()) item { Panel { Text("下载好的视频和文字稿会出现在这里。") } }
                        items(state.records,key = { it.id }) { record ->
                            Panel {
                                Text(record.title,fontWeight = FontWeight.SemiBold,fontSize = 17.sp)
                                Text(SimpleDateFormat("MM-dd HH:mm",Locale.US).format(Date(record.created)),fontSize = 12.sp,color = MaterialTheme.colorScheme.outline)
                                Text(record.message,fontSize = 13.sp)
                                record.outputs.forEach { filename ->
                                    HorizontalDivider()
                                    Text(filename,fontSize = 13.sp)
                                    Row {
                                        TextButton(onClick = { open(record,filename) }) { Text("打开") }
                                        TextButton(onClick = { share(record,filename) }) { Text("分享") }
                                        TextButton(onClick = { Thread { runCatching { Exports.save(this@MainActivity,record,filename) }.onSuccess { toast("已保存到手机的 V2T 文件夹") }.onFailure { toast("保存失败，请检查剩余空间") } }.start() }) { Text("保存到手机") }
                                    }
                                }
                                if (record.source.isNotEmpty() && !state.busy) TextButton(onClick = { input = record.source; tab = 0; app.change { it.copy(info = null,error = "",status = "") } }) { Text("重新解析这个链接") }
                            }
                        }
                    }
                    2 -> {
                        item {
                            Text("平台账号",fontSize = 23.sp,fontWeight = FontWeight.SemiBold)
                            Text("已有登录态可从电脑同步或导入 Cookie。只需保存一次，关闭 App、重启手机仍会保留；平台让会话过期时再更新。",fontSize = 13.sp)
                            OutlinedButton(onClick = { SessionTransfer.receiveInApp(app); cookieRefresh++ },enabled = !state.busy) { Text("接收电脑同步的登录态") }
                        }
                        items(Site.entries) { site ->
                            Panel {
                                Text(site.label,fontSize = 18.sp,fontWeight = FontWeight.SemiBold)
                                key(cookieRefresh,state.status) { Text(app.sessions.status(site),fontSize = 13.sp) }
                                Row {
                                    Button(onClick = { login(site) },enabled = !state.busy) { Text("打开官方网页登录") }
                                    TextButton(onClick = { importSite = site },enabled = !state.busy) { Text("导入 Cookie") }
                                }
                            }
                        }
                        item {
                            Text("Cookie 仅在本机使用。手动导入会优先于网页登录；重新网页登录并保存后，会恢复使用网页会话。",fontSize = 12.sp,color = MaterialTheme.colorScheme.outline)
                            TextButton(onClick = { clearDialog = true },enabled = !state.busy) { Text("退出全部平台并清除登录资料") }
                        }
                    }
                    3 -> {
                        item { Text("设置",fontSize = 23.sp,fontWeight = FontWeight.SemiBold) }
                        item { Panel {
                            Text("中英文本地转写",fontSize = 18.sp,fontWeight = FontWeight.SemiBold)
                            Text(if (XAsr.installed(this@MainActivity)) "模型已安装，转写不上传音视频。" else "导入 X-ASR 模型包后，即可离线识别中英文。",fontSize = 14.sp)
                            Text("两套模型约 330 MB；支持中文、英文和混合表达。",fontSize = 12.sp)
                            OutlinedButton(onClick = { modelPicker.launch(arrayOf("application/zip","application/octet-stream")) },enabled = !state.busy) { Text("导入模型 ZIP") }
                        } }
                        item { Panel {
                            Text("下载解析器",fontSize = 18.sp,fontWeight = FontWeight.SemiBold)
                            Text("网站变更导致解析失败时，可以更新解析器后重试。",fontSize = 14.sp)
                            OutlinedButton(onClick = { TaskService.start(this@MainActivity,"update") },enabled = !state.busy) { Text("从官方源更新 yt-dlp") }
                        } }
                        item { Panel {
                            Text("V2T 安卓预览版 0.1.0",fontWeight = FontWeight.SemiBold)
                            Text("使用 yt-dlp / youtubedl-android、FFmpeg 和 sherpa-onnx。针对 OnePlus 15、Android 16 构建。",fontSize = 13.sp)
                            Text("视频和字幕先保存在 App 内；需要长期保存时，请在「文件」里点「保存到手机」。卸载 App 会删除未导出的内容。",fontSize = 12.sp)
                        } }
                    }
                }
            }
        }
        if (importSite != null) {
            var cookie by remember { mutableStateOf("") }; var error by remember { mutableStateOf("") }
            AlertDialog(onDismissRequest = { importSite = null },title = { Text("导入 ${importSite!!.label} Cookie") },text = {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("粘贴你本人账号的 Cookie 请求头或 Netscape Cookie 文件内容。会在本机加密保存。",fontSize = 13.sp)
                    OutlinedTextField(value = cookie,onValueChange = { cookie = it },minLines = 3,maxLines = 6,visualTransformation = PasswordVisualTransformation(),keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
                    if (error.isNotEmpty()) Text(error,color = MaterialTheme.colorScheme.error)
                }
            },confirmButton = { TextButton(onClick = { runCatching { app.sessions.import(importSite!!,cookie) }.onSuccess { cookie = ""; importSite = null; cookieRefresh++ }.onFailure { error = it.message ?: "格式不正确" } }) { Text("保存") } },dismissButton = { TextButton(onClick = { cookie = ""; importSite = null }) { Text("取消") } })
        }
        if (clearDialog) AlertDialog(onDismissRequest = { clearDialog = false },title = { Text("清除全部平台登录资料？") },text = { Text("下次访问需要重新登录，已下载的文件会保留。") },confirmButton = { TextButton(onClick = { app.sessions.clearAll { cookieRefresh++ }; clearDialog = false }) { Text("清除") } },dismissButton = { TextButton(onClick = { clearDialog = false }) { Text("取消") } })
    }
    @Composable private fun Panel(content: @Composable ColumnScope.() -> Unit) {
        Card(Modifier.fillMaxWidth(),shape = RoundedCornerShape(24.dp),colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(18.dp),verticalArrangement = Arrangement.spacedBy(10.dp),content = content)
        }
    }
    private fun share(record: Record, name: String) {
        val uri = Exports.uri(this,record,name)
        startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType(Exports.mime(name)).putExtra(Intent.EXTRA_STREAM,uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),"分享文件"))
    }
    private fun open(record: Record, name: String) {
        val uri = Exports.uri(this,record,name)
        if (name.endsWith(".txt") || name.endsWith(".srt") || name.endsWith(".json")) {
            val text = java.io.File(app.store.directory(record.id),name).readText().take(100_000)
            val view = android.widget.TextView(this).apply { setTextIsSelectable(true); textSize = 16f; setPadding(30,20,30,20); this.text = text }
            val scroll = android.widget.ScrollView(this).apply { addView(view) }
            android.app.AlertDialog.Builder(this).setTitle(name).setView(scroll).setPositiveButton("关闭",null).show()
        } else runCatching { startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri,Exports.mime(name)).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)) }.onFailure { toast("没有找到可打开这个格式的播放器，可先保存到手机") }
    }
}
