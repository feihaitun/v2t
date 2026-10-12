// 在本机页面中同源通信；不开放跨域接口，也不向页面注入账号凭据。
(() => {
  if (!['http://127.0.0.1:8765', 'http://localhost:8765'].includes(location.origin)) return;
  const client = crypto.randomUUID();
  let busy = false;
  async function post(path, body) {
    const r = await fetch('/api/chrome/' + path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    if (!r.ok) throw new Error('local_service_unavailable');
    return r.json();
  }
  async function heartbeat() {
    try { await post('heartbeat', {client}); } catch (_) { /* 服务未启动时安静等待 */ }
  }
  async function tick() {
    if (busy) return;
    busy = true;
    try {
      const task = await post('claim', {client});
      if (task) {
        let payload;
        try { payload = await chrome.runtime.sendMessage({type:'capture', video_id:task.video_id}); }
        catch (_) { payload = {video_id:task.video_id, status:'failed'}; }
        await post('result', {client, token:task.token, payload});
      }
    } catch (_) { /* 下次心跳继续；不输出资料和下载地址 */ }
    finally { busy = false; }
  }
  heartbeat(); tick();
  setInterval(heartbeat, 2000);
  setInterval(tick, 1000);
})();
