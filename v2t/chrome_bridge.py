"""可选的已登录 Chrome 连接器：只在任务开启开关时请求目标视频资料。

连接器通过本机网页的同源接口领取一次性请求，不开放跨域访问；只接收
视频地址和字幕。凭据留在 Chrome 中，媒体地址只在任务内存中传递。
"""
from __future__ import annotations

import math
import secrets
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import config
from .jobs import StrategyFailed
from .media import probe
from .platforms.base import Strategy
from .platforms.douyin import _media_urls
from .douyin_cookie.tracks import allowed_url, candidate_tracks, parse_payload

ERRORS = {
    'not_connected': '还没连接 Chrome：请在 Chrome 打开本机网页，并按页面说明安装连接器',
    'timeout': 'Chrome 在 35 秒内没有交回视频资料',
    'login_required': '请先在正常使用的 Chrome 中登录抖音',
    'no_data': 'Chrome 页面没有交回可用的视频地址或字幕',
    'failed': 'Chrome 连接器暂时不可用，请检查连接状态',
}


def media_url(url):
    if not isinstance(url, str) or len(url) > 12000 or not allowed_url(url):
        return False
    host = urlparse(url).hostname or ''
    return any(host == d or host.endswith('.' + d) for d in (
        'douyinvod.com', 'zjcdn.com', 'bytecdn.cn', 'ibytedtos.com', 'pstatp.com'))


def normalize(payload, video_id):
    if not isinstance(payload, dict) or payload.get('video_id') != video_id:
        raise ValueError('视频编号不匹配')
    if payload.get('status') != 'ok':
        return {'status': payload.get('status') if payload.get('status') in ERRORS else 'failed'}
    raw = payload.get('detail') or {}
    if not isinstance(raw, dict) or (raw and str(raw.get('aweme_id')) != video_id):
        raise ValueError('视频详情不匹配')
    urls = {'combined': [], 'video': [], 'audio': []}
    for url, _ in _media_urls(raw, True):
        kind = 'audio' if 'media-audio' in url else 'video' if 'media-video' in url else 'combined'
        if media_url(url) and url not in urls[kind]:
            urls[kind].append(url)
    supplied = payload.get('media') or {}
    if isinstance(supplied, dict):
        for kind in urls:
            for url in (supplied.get(kind) or [])[:8]:
                if media_url(url) and url not in urls[kind]:
                    urls[kind].append(url)
    tracks = candidate_tracks(raw)
    title = raw.get('item_title') or raw.get('desc') or payload.get('title')
    duration = (raw.get('video') or {}).get('duration')
    duration = duration / 1000 if isinstance(duration, (float, int)) else payload.get('duration')
    if not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration <= 0:
        duration = None
    return {'status': 'ok', 'media': {k: v[:8] for k, v in urls.items()}, 'tracks': tracks,
            'meta': {'title': title[:1000] if isinstance(title, str) else None, 'duration': duration}}


class Broker:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = {}
        self.pending = None

    def heartbeat(self, client):
        if not isinstance(client, str) or not 8 <= len(client) <= 80:
            raise ValueError('连接标识无效')
        with self.lock:
            now = time.monotonic()
            self.clients = {k: v for k, v in self.clients.items() if now-v < 10}
            self.clients[client] = now

    def status(self):
        with self.lock:
            return {'connected': any(time.monotonic()-t < 10 for t in self.clients.values()),
                    'busy': self.pending is not None}

    def claim(self, client):
        self.heartbeat(client)
        with self.lock:
            p = self.pending
            if not p or p['claimed'] or p['deadline'] <= time.monotonic():
                return None
            p['claimed'] = client
            return {'token': p['token'], 'video_id': p['video_id']}

    def receive(self, client, token, payload):
        with self.lock:
            p = self.pending
            if (not p or p['claimed'] != client or p['deadline'] <= time.monotonic()
                    or not isinstance(token, str) or not secrets.compare_digest(p['token'], token)
                    or p['event'].is_set()):
                raise ValueError('请求已结束或不属于这个连接')
            p['result'] = normalize(payload, p['video_id'])
            p['event'].set()

    def request(self, video_id, timeout=35):
        if not self.status()['connected']:
            return {'status': 'not_connected'}
        p = {'video_id': video_id, 'token': secrets.token_urlsafe(32), 'claimed': None,
             'event': threading.Event(), 'deadline': time.monotonic()+timeout}
        with self.lock:
            if self.pending:
                return {'status': 'failed'}
            self.pending = p
        try:
            return p.get('result', {'status': 'timeout'}) if p['event'].wait(timeout) else {'status': 'timeout'}
        finally:
            with self.lock:
                if self.pending is p:
                    self.pending = None


broker = Broker()


def capture(ctx, at):
    if not ctx.options.douyin_chrome:
        raise StrategyFailed('已登录 Chrome 模块已关闭', expected=True)
    if ctx.chrome_capture is None:
        at.log('请保持 Chrome 和本机网页打开；正在从已登录的 Chrome 获取目标视频资料', level='info')
        ctx.chrome_capture = broker.request(ctx.video_id)
    result = ctx.chrome_capture
    if result.get('status') != 'ok':
        raise StrategyFailed(ERRORS.get(result.get('status'), ERRORS['failed']) + ' → 继续原来的访客流程', expected=True)
    ctx.remember_info(result['meta'])
    return result


def fetch(url, limit, *, media=False):
    """验证每次跳转；不附加 Cookie、不记录地址或第三方异常。"""
    import httpx
    from urllib.parse import urljoin
    with httpx.Client(timeout=30, trust_env=False) as client:
        for _ in range(4):
            if not (media_url(url) if media else allowed_url(url)):
                raise StrategyFailed('文件地址不在允许的平台域名内')
            with client.stream('GET', url, headers={'Referer': 'https://www.douyin.com/'}) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, r.headers.get('location', ''))
                    continue
                if r.status_code != 200:
                    raise StrategyFailed(f'文件下载返回 HTTP {r.status_code}')
                total = 0
                for chunk in r.iter_bytes(1 << 20):
                    total += len(chunk)
                    if total > limit:
                        raise StrategyFailed('文件超过本次下载大小限制')
                    yield chunk
                length = int(r.headers.get('content-length') or 0)
                if length and total != length:
                    raise StrategyFailed('文件没有下载完整')
                return
        raise StrategyFailed('文件地址跳转次数过多')


class ChromeSubtitles(Strategy):
    key = 'douyin_chrome_subtitles'
    label = '从已登录的 Chrome 查字幕'
    explain = '让你正常使用的 Chrome 打开目标视频，读取页面返回的字幕；没有就下载声音转写。'
    needs = 'login'
    option_flag = 'douyin_chrome'

    def run(self, ctx, at):
        result = capture(ctx, at)
        for track in result['tracks']:
            if ctx.options.language != 'auto' and track['language'].split('-')[0] != ctx.options.language.split('-')[0]:
                continue
            try:
                content = track.get('data')
                if content is None:
                    content = b''.join(fetch(track['url'], 2_000_000)).decode('utf-8-sig')
                segments = parse_payload(content)
            except Exception:
                continue
            if segments:
                ctx.subtitle = {'segments': segments, 'language': track['language'], 'kind': 'platform',
                                'how': '抖音平台字幕（从已登录的 Chrome 获取）'}
                at.success(f'拿到平台字幕：{len(segments)} 段')
                return
        raise StrategyFailed('Chrome 已交回视频资料，但没有可用字幕 → 接下来下载并在本机转写', expected=True)


class ChromeMedia(Strategy):
    key = 'douyin_chrome_media'
    label = '从已登录的 Chrome 获取视频/音频'
    explain = '下载 Chrome 页面返回的文件地址；完整视频若把画面和声音分开，就在本机合并。'
    needs = 'login'
    option_flag = 'douyin_chrome'

    def run(self, ctx, at):
        result = capture(ctx, at)
        folder = ctx.workdir / 'download-douyin_chrome'
        folder.mkdir(exist_ok=True)

        def get(urls, name, kind):
            for url in urls[:3]:
                dest = folder / name
                try:
                    with dest.open('wb') as f:
                        for block in fetch(url, 2_000_000_000, media=True):
                            f.write(block)
                    info = probe(dest)
                    if not info['has_' + kind]:
                        continue
                    return dest, info
                except Exception:
                    continue
            raise StrategyFailed('Chrome 返回的文件地址未能下载成功')

        media = result['media']
        if ctx.options.keep_video:
            picture, info = get(media['combined']+media['video'], 'picture.mp4', 'video')
            if info['has_audio']:
                dest = folder / 'media.mp4'
                picture.replace(dest)
            else:
                sound, audio = get(media['audio'], 'sound.m4a', 'audio')
                if info['duration'] and audio['duration'] and abs(info['duration']-audio['duration']) > 3:
                    raise StrategyFailed('画面与声音时长不一致，停止合并')
                dest = folder / 'media.mp4'
                proc = subprocess.run([config.FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-i', str(picture), '-i', str(sound), '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy',
                    '-movflags', '+faststart', str(dest)], capture_output=True, timeout=120)
                if proc.returncode:
                    raise StrategyFailed('画面和声音合并失败')
            verified = probe(dest)
            if not verified['has_audio'] or not verified['has_video']:
                raise StrategyFailed('完整视频检查失败')
            for name in ('picture.mp4', 'sound.m4a'):
                (folder / name).unlink(missing_ok=True)
            label = '完整视频（画面和声音）'
        else:
            dest, verified = get(media['audio']+media['combined'], 'media.m4a', 'audio')
            label = '音频'
        ctx.media_path, ctx.media_strategy = dest, self.key
        ctx.media_how = f'从你已登录的 Chrome 页面取得地址，下载了{label}'
        at.success(f'下载好了{label}，{verified["size"]/1e6:.1f} MB')
