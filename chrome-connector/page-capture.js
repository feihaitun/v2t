// 只装入本次任务创建的目标视频标签页；不接触 Cookie、账号接口或私信。
(() => {
  if (window.__v2tPageCapture) return;
  const id = location.pathname.match(/^\/video\/(\d{15,22})$/)?.[1];
  if (!id) return;
  let detail = null;
  const started = Date.now();
  function isDetail(url) {
    try { const u=new URL(url,location.href); return (u.hostname==='douyin.com'||u.hostname.endsWith('.douyin.com')) &&
      u.pathname==='/aweme/v1/web/aweme/detail/' && u.searchParams.get('aweme_id')===id; }
    catch (_) {return false;}
  }
  function remember(data) {
    const d=data?.aweme_detail;
    if (!d || String(d.aweme_id)!==id) return;
    const v=d.video||{};
    detail={aweme_id:id,item_title:d.item_title,desc:d.desc,video_text:d.video_text,
      interaction_stickers:d.interaction_stickers,
      video:{duration:v.duration,bit_rate:v.bit_rate,bit_rate_audio:v.bit_rate_audio,
        play_addr:v.play_addr,subtitleInfos:v.subtitleInfos,cla_info:v.cla_info}};
  }
  const nativeFetch=window.fetch;
  window.fetch=function(...args) {
    const result=nativeFetch.apply(this,args);
    const url=typeof args[0]==='string'?args[0]:args[0]?.url;
    if(isDetail(url)) result.then(r=>{if(r.ok)r.clone().json().then(remember).catch(()=>{});}).catch(()=>{});
    return result;
  };
  const nativeOpen=XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open=function(method,url,...args) {
    if(isDetail(url))this.addEventListener('load',()=>{
      try{if(this.status===200)remember(this.responseType==='json'?this.response:JSON.parse(this.responseText));}catch(_){}
    },{once:true});
    return nativeOpen.call(this,method,url,...args);
  };
  window.__v2tPageCapture={read() {
    if(detail)return {status:'ok',video_id:id,detail};
    // 若播放器通过其他页面数据正常加载，用其已加载的媒体地址；多个视频组则不猜。
    if(location.pathname!=='/video/'+id || Date.now()-started<8000)return null;
    const media={video:[],audio:[],combined:[]};
    const groups=new Set();
    for(const r of performance.getEntriesByType('resource')) {
      try {
        const u=new URL(r.name);
        if(!/(^|\.)(douyinvod\.com|zjcdn\.com)$/.test(u.hostname)||u.protocol!=='https:')continue;
        const marker=u.pathname.indexOf('/media-');
        if(marker<0)continue;
        // CDN 签名位于路径开头，目标内容标识位于 /video/tos/ 之后。
        const content=u.pathname.indexOf('/video/tos/');
        groups.add(u.pathname.slice(content>=0?content:0,marker));
        const kind=u.pathname.includes('/media-audio')?'audio':u.pathname.includes('/media-video')?'video':'combined';
        if(!media[kind].includes(r.name))media[kind].push(r.name);
      } catch (_) {}
    }
    const video=[...document.querySelectorAll('video')].find(v=>v.duration>1);
    if(groups.size===1 && video && media.audio.length && media.video.length)
      return {status:'ok',video_id:id,media,title:document.querySelector('h1')?.innerText||document.title,duration:video.duration};
    return null;
  }};
})();
