// 仅接收来自本机工具页的任务。新标签页使用现有 Chrome 登录状态，用完关闭。
function trustedSender(sender) {
  try { return !!sender.tab && ['http://127.0.0.1:8765','http://localhost:8765'].includes(new URL(sender.url).origin); }
  catch (_) { return false; }
}

async function capture(videoId) {
  const scriptId = 'v2t-' + crypto.randomUUID();
  const url = 'https://www.douyin.com/video/' + videoId;
  let tab;
  try {
    await chrome.scripting.registerContentScripts([{id:scriptId, matches:[url], js:['page-capture.js'],
      runAt:'document_start', world:'MAIN', persistAcrossSessions:false}]);
    tab = await chrome.tabs.create({url, active:false});
    const deadline = Date.now()+26000;
    while (Date.now()<deadline) {
      try {
        const responses = await chrome.scripting.executeScript({target:{tabId:tab.id}, world:'MAIN',
          func: () => window.__v2tPageCapture?.read() || null});
        const result = responses[0]?.result;
        if (result) return result;
      } catch (_) { /* 页面正在跳转时，等加载完成 */ }
      await new Promise(resolve=>setTimeout(resolve,500));
    }
    return {video_id:videoId, status:'no_data'};
  } finally {
    if (tab?.id) { try { await chrome.tabs.remove(tab.id); } catch (_) {} }
    try { await chrome.scripting.unregisterContentScripts({ids:[scriptId]}); } catch (_) {}
  }
}

chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if (!trustedSender(sender) || message?.type!=='capture' || !/^\d{15,22}$/.test(message.video_id||'')) return false;
  capture(message.video_id).then(reply,()=>reply({video_id:message.video_id,status:'failed'}));
  return true;
});
