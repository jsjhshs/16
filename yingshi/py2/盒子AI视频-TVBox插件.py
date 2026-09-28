# coding=utf-8
"""
盒子AI视频（hezi_ai_video 站群）· TVBox Python 插件 · 纯标准库自实现（不需要 requests / pycryptodome）
=====================================================================================
接口： homeContent / homeVideoContent / categoryContent / detailContent / searchContent / playerContent
挂法： {"name":"🔞盒子AI视频┃原生","type":3,"api":"py_<文件名>.py"}
      extend 可传 {"host":"换域名","channelCode":"369","pid":0,"token":"直填令牌"}

【站情 · 2026-09-27 实地逆向】
  用户给的入口 https://luanlunz.dteuhe.cn/land/?channelCode=369.html 是**广告落地壳**：
    · 首页 HTML 是一层 XOR 壳（key 125fb556e5a1bb2e58cac3722163cdc4，逐字节循环异或 base64）
      → 吐出 /land/st/tk/th/main.js
    · main.js 只是 loader，动态插 /land/api.js?v=<15 分钟时间片>：
        window.g = { ApiUrl:'hhftu.cn/tt', Apis:'https://www.baidu.com', zxfwUrl:'' }
      ApiUrl 是 APK 分发域；APK 地址用**日期种子 LCG** 现算（seed=YYMMDD，
      a=1664525 c=1013904223 m=2^32，36 进制取 8 字符当随机子域）
      实测 https://xuw06yao.hhftu.cn/tt/369.apk → 200，11,946,284 字节
    · 落地页的「在线」按钮走 origin + "/" + channelCode = https://<host>/369
      ← 这才是网页版真站（同一套 ThinkPHP，DOCUMENT_ROOT /www/wwwroot/hezi_ai_video）

  真站怎么找到的：/369 是聚合导航（直播/短视频/游戏三块，全外链，没有片库）。
  真正的内容在页面内联脚本里：
      /static/vip/#/vip/longVideo?baseUrl=<host>&username=&plain_passwd=&fingerprint=&channelCode=
  「VIP 长视频中心」是独立 Vite SPA（/static/vip/assets/index.<hash>.js，1.5 MB），
  它读 /static/vip/static/config.json 拿 issmssgUrl 当 axios baseURL：
      实测 {"fsdgdxfz":"https://ytmx.foyyiv.cn","issmssgUrl":"https://ytmx.foyyiv.cn"}
      但 ytmx.foyyiv.cn 从大陆不可达（DNS 空）→ **落地页自身的域就是同源反代**，用它最稳。

【加密信封】跟 WNZS 站群**逐字节同一套**：
  响应体 = base64(AES/ECB/PKCS7(明文 JSON, key))，key = "=^%7@84%0^!8892-"（16 字节 → AES-128）
  出处 SPA 里 const uu=(t,e)=>{ CryptoJS.AES.decrypt({ciphertext},key,{mode:ECB,padding:Pkcs7}) }
  本文件里 AES-128 是手写的，壳里没有 pycryptodome 也能跑；有的话自动走库。

【两段式开户（关键坑）】这里有**两套令牌**，混用会被服务端按 JWT 签名失败打回：
  ① 游客号  POST /api/ai/register {pid,unique_code,model:"android",channelCode}
  ② VIP 令牌 POST /api/vip/v1/ai/register {username,passwd,unique_code,model,channelCode}
     —— 内容接口只认 ②，Authorization 头 = token 原串（不带 Bearer），query 再带一份 accessToken

【接口】
  首页     GET /api/vip/v1/video/index
              → cateParentList(9) / areaList(5) / companyList(8) / cateVideo(分组片单)
  分类     GET /api/vip/v1/video/cateVideoList?cate_pid=&page=&limit=  → [{id,title,videoList:[...]}]
           GET /api/vip/v1/video/videoByCid?cid=&page=&limit=          → {videoList:{total,...,data}}
  最新     GET /api/vip/v1/video/newVideoList?page=&limit=              → {total,...,data}
  搜索     POST /api/vip/v1/video/search {keyword,page,limit}
  详情     GET /api/vip/v1/video/detail?vid=                            → video.play_url 绝对 m3u8
  暗网     GET /api/vip/v1/dark/index  /dark/videoByCid  /dark/detail

【取流】play_url 直出绝对 m3u8（superman.xn--5nq817c96av9verb.com/video/m3u8/<日期>/<hash>/index.m3u8）
  实测清单 200 / 分片 2.38 MB 200，**免鉴权、免 Referer**，直链直出，不做中继。

【封面】字段 enpic = enc pic：**整张图逐字节异或 0x88**（真身 JPEG/PNG），且 URL 上带短签
  （time 就是失效时刻，~20 分钟）。两个坑叠一起就是「列表里视频没有图片」。
  本插件用本机中继（127.0.0.1）取图 + 解打码 + 过期回详情接口续签，图永远新鲜。

【VIP 墙】抽样实测：isvip=1 的条目普通游客号照样直出 play_url 并成功拉分片 → 无可绕之物，不做绕 VIP。
"""

import base64
import gzip
import io
import json
import re
import threading
import time
import urllib.parse

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except Exception:
    BaseHTTPRequestHandler = None
    ThreadingHTTPServer = None

try:
    import urllib.request as _urlreq
except Exception:
    _urlreq = None

try:
    import requests
except Exception:
    requests = None

try:                                              # TVBox 壳内基类
    from base.spider import Spider as _Spider
except Exception:
    class _Spider(object):                        # 原生 python 下自检用
        def init(self, *a, **kw):
            return self

# ---------------------------------------------------------------- 常量
SITE_NAME = '🔞WNZS┃网络杀手'
DEFAULT_HOST = 'https://wnzsapia.yasqhm.cn'   # wnzs.5ibox.top TXT 实测值，兜底
DEFAULT_TXT = 'wnzs.5ibox.top'                # 线路 TXT 域名（站方轮换线路只改这里）
APPKEY = 'tp19'
AES_KEY = b'=^%7@84%0^!8892-'
DEFAULT_UA = 'okhttp/4.9.3'
DOH = 'https://dns.alidns.com/resolve?name=%s&type=TXT'
PAGE_SIZE = 24
REL_LIMIT = 12
HOME_LIMIT = 300      # index.cateVideo 有 66 组 × 12 = 792 条，全推太重，默认取前 300

# ---------------------------------------------------------------- 常量
SITE_NAME = '🔞盒子AI视频┃原生'
HOSTS = [                                          # 站群多域，接口完全一致，一条死了自动跳下一条
    'https://luanlunz.dteuhe.cn',                  # 落地页域（同源反代，最稳）
    'https://luanluna.utbgd.cn',
    'https://ttapi.utbgd.cn',
]
DEFAULT_HOST = HOSTS[0]
DEFAULT_UA = ('Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36')
AES_KEY = b'=^%7@84%0^!8892-'
PAGE_SIZE = 24
REL_LIMIT = 12
HOME_LIMIT = 200      # index.cateVideo 分组片单，全推太重，截断


def _parse_extend(extend):
    cfg = {}
    if not extend:
        return cfg
    try:
        s = str(extend).strip()
        if s[:1] == '{':
            j = json.loads(s)
            for k in j:
                cfg[str(k)] = j[k]
        else:
            for kv in re.split(r'[&;]', s):
                i = kv.find('=')
                if i > 0:
                    cfg[kv[:i].strip()] = urllib.parse.unquote(kv[i + 1:].strip())
    except Exception:
        pass
    return cfg


def _is_dark(key):
    return str(key or '')[:1] == 'd'


def _strip_key(key):
    s = str(key or '').strip()
    if len(s) > 1 and s[0] in ('v', 'd') and s[1].isdigit():
        return s[1:]
    return s


# ---------------------------------------------------------------- AES-128/ECB 纯标准库实现
_SBOX = None
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36,
         0x6c, 0xd8, 0xab, 0x4d, 0x9a]


def _sbox():
    global _SBOX
    if _SBOX is not None:
        return _SBOX
    p = q = 1
    sbox = [0] * 256
    while True:
        p = (p ^ ((p << 1) & 0xff) ^ (0x1b if p & 0x80 else 0)) & 0xff
        q ^= (q << 1) & 0xff
        q ^= (q << 2) & 0xff
        q ^= (q << 4) & 0xff
        q &= 0xff
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) \
            ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xff
        if p == 1:
            break
    sbox[0] = 0x63
    _SBOX = sbox
    return sbox


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        h = a & 0x80
        a = (a << 1) & 0xff
        if h:
            a ^= 0x1b
        b >>= 1
    return r & 0xff


def _expand(key):
    sbox = _sbox()
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [sbox[x] for x in t]
            t[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = [sbox[x] for x in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


def _dec_block(blk, w, nr):
    sbox = _sbox()
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    st = [[blk[r + 4 * c] for c in range(4)] for r in range(4)]
    def ark(rnd):
        for c in range(4):
            k = w[rnd * 4 + c]
            for r in range(4):
                st[r][c] ^= k[r]
    ark(nr)
    for rnd in range(nr - 1, -1, -1):
        for r in range(1, 4):
            st[r] = st[r][-r:] + st[r][:-r]
        for r in range(4):
            for c in range(4):
                st[r][c] = inv[st[r][c]]
        ark(rnd)
        if rnd:
            for c in range(4):
                a = [st[r][c] for r in range(4)]
                st[0][c] = _mul(a[0], 14) ^ _mul(a[1], 11) ^ _mul(a[2], 13) ^ _mul(a[3], 9)
                st[1][c] = _mul(a[0], 9) ^ _mul(a[1], 14) ^ _mul(a[2], 11) ^ _mul(a[3], 13)
                st[2][c] = _mul(a[0], 13) ^ _mul(a[1], 9) ^ _mul(a[2], 14) ^ _mul(a[3], 11)
                st[3][c] = _mul(a[0], 11) ^ _mul(a[1], 13) ^ _mul(a[2], 9) ^ _mul(a[3], 14)
    return bytes(st[r][c] for c in range(4) for r in range(4))


def _aes_ecb_dec(data, key):
    w, nr = _expand(key)
    out = []
    for i in range(0, len(data) - 15, 16):
        out.append(_dec_block(data[i:i + 16], w, nr))
    return b''.join(out)


def _py_decrypt(raw_b64):
    raw = base64.b64decode(raw_b64 + '=' * (-len(raw_b64) % 4))
    try:
        from Crypto.Cipher import AES
        pt = AES.new(AES_KEY, AES.MODE_ECB).decrypt(raw)
    except Exception:
        pt = _aes_ecb_dec(raw, AES_KEY)
    if pt and 0 < pt[-1] <= 16:
        pt = pt[:-pt[-1]]
    return pt.decode('utf-8', 'replace')


# ---------------------------------------------------------------- 封面中继（本机 127.0.0.1）
def _b64u(s):
    try:
        return base64.urlsafe_b64encode(str(s or '').encode('utf-8')).decode('ascii').rstrip('=')
    except Exception:
        return ''


def _unb64u(s):
    s = str(s or '')
    if not s:
        return ''
    try:
        return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4)).decode('utf-8', 'replace')
    except Exception:
        return ''


def _is_image(b):
    if not b or len(b) < 4:
        return False
    if b[:2] == b'\xff\xd8':
        return True
    if b[:4] == b'\x89PNG':
        return True
    if b[:3] == b'GIF':
        return True
    if b[:4] == b'RIFF':
        return True
    return False


def _de_xor(b):
    """封面是**打码图**：字段名 enpic 就是 enc pic —— 整张图逐字节异或了 0x88。
    实测这张 203,434 字节的封面头是 01 D8 C6 CF，异或 0x88 后 = 89 50 4E 47（PNG 魔数），
    往后 8 字节对得上 IHDR 段长 0x0000000D —— 真身是 PNG（服务端 content-type 谎报 image/jpeg）。
    原样给壳就是一片花屏 = 「列表里视频没有图片」。已经是正常图片就原样返回，绝不把好图异或坏。"""
    if not b or len(b) < 8 or _is_image(b):
        return b
    out = bytes(x ^ 0x88 for x in b)
    return out if _is_image(out) else b


def _mime_of(b):
    if not b:
        return 'application/octet-stream'
    if b[:4] == b'\x89PNG':
        return 'image/png'
    if b[:3] == b'GIF':
        return 'image/gif'
    if b[:4] == b'RIFF':
        return 'image/webp'
    return 'image/jpeg'


class _PicRelay(object):
    """只监听 127.0.0.1 的极小 HTTP 服务，只干一件事：把封面拿回来 + 解打码 + 过期续签。

    为什么必须有它（这就是「视频列表没有视频图片」的真根子）：
      ① 封面是**短签**：URL 上的 time 就是失效时刻（实测约 22 分钟），到期后无 Referer 也 403，
         去掉 sign/time 同样 403，伪造 sign 还是 403 —— 签名算在服务端，设备侧算不出来；
      ② 封面是**打码图**：整张图异或 0x88，原样交给壳 = 花屏/空封面。
    走中继后，壳每次取图都经这里：取不到就拿站内 id 回详情接口换一张新签名的封面再取，
    顺手解打码 —— 所以列表缓存多久、签名过没过期，图都是好的。
    """

    def __init__(self, sp):
        self.sp = sp
        self.httpd = None
        self.port = 0
        self.hits = 0
        self.resigns = 0
        self.fails = 0
        self._lock = threading.Lock()

    def ensure(self):
        if self.httpd is not None:
            return self.port
        if ThreadingHTTPServer is None or BaseHTTPRequestHandler is None:
            return 0
        with self._lock:
            if self.httpd is not None:
                return self.port
            try:
                srv = ThreadingHTTPServer(('127.0.0.1', 0), self._handler())
                srv.daemon_threads = True
                th = threading.Thread(target=srv.serve_forever, kwargs={'poll_interval': 0.5})
                th.daemon = True
                th.start()
                self.httpd = srv
                self.port = int(srv.server_address[1])
                self.sp._log('封面中继已起 127.0.0.1:%d' % self.port)
            except Exception as e:
                self.sp._log('封面中继起不来 %s' % str(e)[:110])
                return 0
        return self.port

    def stop(self):
        try:
            if self.httpd is not None:
                self.httpd.shutdown()
                self.httpd.server_close()
        except Exception:
            pass
        self.httpd = None
        self.port = 0

    def img_url(self, pic, vid):
        p = self.ensure()
        if not p:
            return ''
        return 'http://127.0.0.1:%d/img?u=%s&v=%s' % (p, _b64u(pic), urllib.parse.quote(str(vid or '')))

    def _handler(self):
        relay = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def do_GET(self):
                relay.serve(self, False)

            def do_HEAD(self):
                relay.serve(self, True)

        return _H

    @staticmethod
    def _q(qs, key, default=''):
        v = qs.get(key)
        return v[0] if v else default

    def serve(self, h, head=False):
        try:
            u = urllib.parse.urlparse(h.path)
            qs = urllib.parse.parse_qs(u.query)
            sp = self.sp
            if u.path == '/img':
                url = _unb64u(self._q(qs, 'u'))
                vid = self._q(qs, 'v')
                raw = sp._fetch_bytes(url, timeout=12) if url.startswith('http') else None
                if not raw and vid:
                    fresh = sp._fresh_pic(vid)
                    if fresh and fresh != url:
                        raw = sp._fetch_bytes(fresh, timeout=12)
                        if raw:
                            self.resigns += 1
                if not raw:
                    self.fails += 1
                    self._send(h, 502, 'text/plain', b'img fail', head)
                    return
                raw = _de_xor(raw)
                self.hits += 1
                self._send(h, 200, _mime_of(raw), raw, head)
                return
            self._send(h, 404, 'text/plain', b'bad path', head)
        except Exception as e:
            self.fails += 1
            try:
                self._send(h, 500, 'text/plain', ('err %s' % str(e)[:60]).encode('utf-8'), head)
            except Exception:
                pass

    def _send(self, h, code, mime, body, head=False):
        body = body or b''
        try:
            h.send_response(code)
            h.send_header('Content-Type', mime)
            h.send_header('Content-Length', str(len(body)))
            h.send_header('Cache-Control', 'no-store')
            h.end_headers()
            if not head:
                h.wfile.write(body)
        except Exception:
            pass
        h.close_connection = True



# ---------------------------------------------------------------- 主体
class Spider(_Spider):

    def __init__(self, *args, **kwargs):
        self.host = DEFAULT_HOST
        self.pid = 0
        self.channel = '369'
        self._token = ''
        self._guest = ''
        self._guest_user = ''
        self._guest_pass = ''
        self._relay = None
        self._cache = {}
        self.headers = {
            'User-Agent': DEFAULT_UA,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        self._host_idx = 0

    # ---------- 生命周期 ----------
    def init(self, extend=''):
        cfg = _parse_extend(extend)
        if cfg.get('host'):
            h = str(cfg['host']).strip().rstrip('/')
            if not h.startswith('http'):
                h = 'https://' + h
            self.host = h
        if cfg.get('channelCode'):
            self.channel = str(cfg['channelCode']).strip()
        elif cfg.get('channel'):
            self.channel = str(cfg['channel']).strip()
        if cfg.get('pid'):
            try:
                self.pid = int(str(cfg['pid']).strip())
            except Exception:
                pass
        if cfg.get('token'):
            self._token = str(cfg['token']).strip()
        return self

    def _log(self, msg):
        try:
            print('[hzai] %s' % msg)
        except Exception:
            pass

    def getName(self):
        return SITE_NAME

    def destroy(self):
        if self._relay is not None:
            self._relay.stop()
            self._relay = None
        self._token = ''
        self._guest = ''

    def isVideoFormat(self, url):
        u = str(url or '').lower()
        return '.m3u8' in u or '.mp4' in u

    def manualVideoCheck(self):
        return False

    # ---------- HTTP ----------
    def _base(self):
        return self.host.rstrip('/')

    def _next_host(self):
        if len(HOSTS) < 2:
            return self._base()
        self._host_idx = (self._host_idx + 1) % len(HOSTS)
        self.host = HOSTS[self._host_idx]
        self._log('切线路 → %s' % self.host)
        return self._base()

    def _http(self, url, body=None, method='GET'):
        hd = dict(self.headers)
        if body is not None:
            hd['Content-Type'] = 'application/json; charset=utf-8'
        for attempt in (0, 1):
            try:
                if _urlreq is not None:
                    req = _urlreq.Request(url, data=body, headers=hd, method=method)
                    with _urlreq.urlopen(req, timeout=20) as r:
                        raw = r.read()
                    if raw:
                        return raw.decode('utf-8', 'replace')
            except Exception as e:
                if attempt:
                    self._log('request fail %s (%s)' % (url.split('?')[0], str(e)[:70]))
            try:
                if attempt == 0:
                    time.sleep(0.6)
            except Exception:
                pass
        return ''

    def _fetch_bytes(self, url, timeout=None):
        if not url or not url.startswith('http'):
            return None
        try:
            if _urlreq is not None:
                req = _urlreq.Request(url, headers={'User-Agent': DEFAULT_UA})
                with _urlreq.urlopen(req, timeout=timeout or 15) as r:
                    return r.read()
        except Exception:
            pass
        try:
            if requests is not None:
                r = requests.get(url, headers={'User-Agent': DEFAULT_UA}, timeout=timeout or 15, verify=False)
                if r.status_code < 400:
                    return r.content
        except Exception:
            pass
        return None

    # ---------- 信封 ----------
    def _unwrap(self, raw):
        if not raw:
            return {}
        s = raw.strip()
        if s[:1] == '"' and s[-1:] == '"':
            try:
                s = json.loads(s)
            except Exception:
                s = s[1:-1]
        s = s.replace('\\/', '/')
        if s[:1] in ('{', '['):
            try:
                return json.loads(s)
            except Exception:
                return {}
        if 'Not Found' in s or s[:5] == '<html' or '<!DOCTYPE' in s[:40]:
            return {}
        try:
            return json.loads(_py_decrypt(s))
        except Exception as e:
            self._log('解密失败 %s' % str(e)[:80])
            return {}

    def _api(self, path, params=None, method='GET', body=None):
        params = dict(params or {})
        if self._token:
            params['accessToken'] = self._token
        url = '%s%s?%s' % (self._base(), path, urllib.parse.urlencode(params))
        raw = self._http(url, body=body, method=method)
        data = self._unwrap(raw)
        if not data:
            url2 = '%s%s?%s' % (self._next_host(), path, urllib.parse.urlencode(params))
            data = self._unwrap(self._http(url2, body=body, method=method))
        return data

    def _get(self, path, **params):
        if not self._token:
            self._ensure_token()
        data = self._api(path, params, 'GET')
        code = data.get('code') if isinstance(data, dict) else None
        if code in (401, 403) or not data:
            self._token = ''
            self._guest = ''
            self._ensure_token()
            data = self._api(path, params, 'GET')
        return data

    def _post(self, path, obj):
        if not self._token:
            self._ensure_token()
        if isinstance(obj, dict) and self._token:
            obj = dict(obj)
            obj['accessToken'] = self._token
        return self._api(path, {}, 'POST', json.dumps(obj).encode('utf-8'))

    # ---------- 两段式开户 ----------
    def _unique_code(self):
        ck = 'uuid'
        if not self._cache.get(ck):
            self._cache[ck] = 'tvbox%08x%06x' % (int(time.time() * 1000) & 0xffffffff,
                                                 int(time.time() * 7919) & 0xffffff)
        return self._cache[ck]

    def _ensure_token(self, force=False):
        """两套令牌：① 游客号 /api/ai/register ② VIP 令牌 /api/vip/v1/ai/register。
        内容接口只认 ②，混用会被服务端按 JWT 签名失败打回（实测 500 SignatureInvalidException）。"""
        if self._token and not force:
            return self._token
        ck = ('token', self._base())
        if not force and self._cache.get(ck):
            self._token = self._cache[ck]
            return self._token
        try:
            if not self._guest:
                body = {
                    'pid': self.pid,
                    'unique_code': self._unique_code(),
                    'model': 'android',
                    'channelCode': self.channel,
                }
                d = self._api('/api/ai/register', {}, 'POST', json.dumps(body).encode('utf-8'))
                data = d.get('data') or {}
                self._guest = str(data.get('token') or '')
                ui = data.get('userInfo') or {}
                if ui.get('unique_code'):
                    self._cache['uuid'] = str(ui['unique_code'])
                self._guest_user = str(ui.get('username') or '')
                self._guest_pass = str(ui.get('plain_passwd') or ui.get('passwd') or self._guest_user)
                self._log('游客号开户 %s' % ('成功' if self._guest else '失败'))
            body = {
                'username': self._guest_user,
                'passwd': self._guest_pass,
                'unique_code': self._unique_code(),
                'model': 'android',
                'channelCode': self.channel,
            }
            d = self._api('/api/vip/v1/ai/register', {}, 'POST', json.dumps(body).encode('utf-8'))
            tok = (d.get('data') or {}).get('token')
            if tok:
                self._token = str(tok)
                self._cache[ck] = self._token
                self._log('VIP 令牌 成功')
            else:
                self._log('VIP 令牌 失败 %s' % json.dumps(d, ensure_ascii=False)[:140])
        except Exception as e:
            self._log('开户异常 %s' % str(e)[:120])
        return self._token

    # ---------- 封面中继 ----------
    def _ensure_relay(self):
        if self._relay is None:
            self._relay = _PicRelay(self)
        return self._relay if self._relay.ensure() else None

    def _pic(self, pic, key):
        pic = str(pic or '').strip()
        if len(pic) < 8 or '127.0.0.1' in pic:
            return pic
        rel = self._ensure_relay()
        if not rel:
            return pic
        return rel.img_url(pic, key) or pic

    def _fresh_pic(self, key):
        k = str(key or '')
        if not k:
            return ''
        path = '/api/vip/v1/dark/detail' if k[:1] == 'd' else '/api/vip/v1/video/detail'
        d = (self._get(path, vid=_strip_key(k)).get('data') or {})
        return (d.get('video') or {}).get('enpic', '') or ''

    # ---------- 入口 ----------
    def _card(self, v, prefix='v'):
        if not v:
            return {}
        vid = str(v.get('vid') or '')
        key = prefix + vid
        rem = str(v.get('eyes') or '')
        if rem and rem != '0万':
            rem = '🔥' + rem
        return {
            'vod_id': key,
            'vod_name': str(v.get('vod_name') or ''),
            'vod_pic': self._pic(v.get('enpic'), key),
            'vod_remarks': rem or str(v.get('update_time') or '')[:10],
        }

    @staticmethod
    def _videos(data):
        out = []
        if not data:
            return out
        if isinstance(data, dict):
            a = data.get('data')
            if isinstance(a, list):
                return [x for x in a if isinstance(x, dict)]
            vl = data.get('videoList')
            if isinstance(vl, dict):
                return Spider._videos(vl)
            if isinstance(vl, list):
                return [x for x in vl if isinstance(x, dict)]
            return out
        if isinstance(data, list):
            for g in data:
                if not isinstance(g, dict):
                    continue
                a = g.get('videoList')
                if isinstance(a, list):
                    out.extend([x for x in a if isinstance(x, dict)])
                else:
                    out.append(g)
        return out

    @staticmethod
    def _total(data):
        if isinstance(data, dict):
            if 'total' in data:
                return int(data.get('total') or 0)
            vl = data.get('videoList')
            if isinstance(vl, dict):
                return int(vl.get('total') or 0)
        return 0

    def _filters(self, tid):
        f = []
        try:
            pid = tid.replace('cate_', '')
            d = self._get('/api/vip/v1/video/cateVideoList', cate_pid=pid, page=1, limit=12)
            groups = d.get('data')
            if isinstance(groups, list) and groups:
                vals = [{'n': '全部', 'v': ''}]
                for g in groups:
                    if isinstance(g, dict):
                        vals.append({'n': str(g.get('title') or ''), 'v': str(g.get('id') or '')})
                if len(vals) > 1:
                    f.append({'key': 'cid', 'name': '分类', 'value': vals})
        except Exception as e:
            self._log('filter 异常 %s' % str(e)[:80])
        return f

    def homeContent(self, filter=False):
        root = {'class': [], 'list': []}
        idx = (self._get('/api/vip/v1/video/index').get('data') or {})
        dark = (self._get('/api/vip/v1/dark/index').get('data') or {})
        cls = [{'type_id': 'new', 'type_name': '🔥最新入库'}]
        for c in (idx.get('cateParentList') or []):
            cls.append({'type_id': 'cate_%s' % c.get('cate_pid'), 'type_name': str(c.get('title') or '')})
        for c in (dark.get('categoryList') or []):
            cls.append({'type_id': 'dark_%s' % c.get('cid'), 'type_name': '🕳' + str(c.get('title') or '')})
        root['class'] = cls
        if filter:
            fl = {}
            for c in cls:
                t = c['type_id']
                if t.startswith('cate_'):
                    fl[t] = self._filters(t)
            root['filters'] = fl
        root['list'] = self._home_feed(idx)
        return root

    def _home_feed(self, idx):
        out = []
        for v in self._videos((idx or {}).get('cateVideo')):
            if len(out) >= HOME_LIMIT:
                break
            c = self._card(v, 'v')
            if c:
                out.append(c)
        if not out:
            d = self._get('/api/vip/v1/video/newVideoList', page=1, limit=PAGE_SIZE).get('data')
            for v in self._videos(d):
                c = self._card(v, 'v')
                if c:
                    out.append(c)
        return out

    def homeVideoContent(self):
        idx = (self._get('/api/vip/v1/video/index').get('data') or {})
        return {'list': self._home_feed(idx)}

    def categoryContent(self, tid, pg, filter=False, extend=None):
        page = 1
        try:
            page = max(1, int(str(pg)))
        except Exception:
            pass
        extend = extend or {}
        prefix = 'v'
        data = None
        try:
            if tid == 'new':
                data = self._get('/api/vip/v1/video/newVideoList', page=page, limit=PAGE_SIZE).get('data')
            elif str(tid).startswith('dark_'):
                prefix = 'd'
                data = self._get('/api/vip/v1/dark/videoByCid', page=page, limit=PAGE_SIZE,
                                 cid=str(tid).replace('dark_', '')).get('data')
            elif str(tid).startswith('cate_'):
                cid = str(extend.get('cid') or '')
                if cid:
                    data = self._get('/api/vip/v1/video/videoByCid', page=page, limit=PAGE_SIZE,
                                     cid=cid).get('data')
                else:
                    data = self._get('/api/vip/v1/video/cateVideoList', page=page, limit=PAGE_SIZE,
                                     cate_pid=str(tid).replace('cate_', '')).get('data')
        except Exception as e:
            self._log('category 异常 %s' % str(e)[:100])
        lst = []
        for v in self._videos(data):
            c = self._card(v, prefix)
            if c:
                lst.append(c)
        known = self._total(data)
        if known <= 0:
            known = (page - 1) * PAGE_SIZE + len(lst) + (1 if len(lst) >= PAGE_SIZE else 0)
        return {
            'list': lst,
            'page': page,
            'pagecount': 9999,
            'limit': PAGE_SIZE,
            'total': known,
        }

    def detailContent(self, ids):
        key = str((ids or [''])[0]).strip()
        lst = []
        try:
            dark = _is_dark(key)
            path = '/api/vip/v1/dark/detail' if dark else '/api/vip/v1/video/detail'
            d = (self._get(path, vid=_strip_key(key)).get('data') or {})
            v = d.get('video')
            if v:
                name = str(v.get('vod_name') or '')
                play = str(v.get('play_url') or '')
                ut = str(v.get('update_time') or '')
                one = {
                    'vod_id': key,
                    'vod_name': name,
                    'vod_pic': self._pic(v.get('enpic'), key),
                    'type_name': '暗网重口' if dark else '盒子AI',
                    'vod_year': ut[:4],
                    'vod_remarks': ('🔥' + str(v.get('eyes'))) if v.get('eyes') else '',
                    'vod_content': '%s\n播放源：%s · 播放 %s\n更新：%s' % (
                        name, '暗网重口' if dark else '盒子AI视频', v.get('eyes') or '', ut),
                }
                froms = '盒子AI直连'
                url = ('%s$%s' % (name.replace('$', ' '), play)) if play else ''
                rel = self._videos(d.get('relatedVideoList'))[:REL_LIMIT]
                if rel:
                    froms += '$$$🔎相关推荐'
                    parts = []
                    for r in rel:
                        parts.append('%s$%s%s' % (str(r.get('vod_name') or '').replace('$', ' '),
                                                  'd' if dark else 'v', r.get('vid')))
                    url += '$$$' + '#'.join(parts)
                one['vod_play_from'] = froms
                one['vod_play_url'] = url
                lst.append(one)
        except Exception as e:
            self._log('detail 异常 %s' % str(e)[:100])
        return {'list': lst}

    def searchContent(self, key, quick=False, pg='1'):
        page = 1
        try:
            page = max(1, int(str(pg)))
        except Exception:
            pass
        lst = []
        try:
            d = self._post('/api/vip/v1/video/search',
                           {'keyword': key, 'page': page, 'limit': PAGE_SIZE}).get('data')
            for v in self._videos(d):
                c = self._card(v, 'v')
                if c:
                    lst.append(c)
        except Exception as e:
            self._log('search 异常 %s' % str(e)[:100])
        return {'list': lst}

    def playerContent(self, flag, vid, vipFlags=None):
        key = str(vid or '').strip()
        url = key
        if not key.startswith('http'):
            d = (self._get('/api/vip/v1/dark/detail' if _is_dark(key) else '/api/vip/v1/video/detail',
                           vid=_strip_key(key)).get('data') or {})
            url = (d.get('video') or {}).get('play_url', '') or ''
        return {
            'parse': 0,
            'playUrl': '',
            'url': url,
            'header': json.dumps({'User-Agent': DEFAULT_UA}),
        }

    # ---------- 自检 ----------
    def check(self):
        try:
            h = self.homeContent(True)
            cls = h.get('class') or []
            line = ['%s · 线路 %s' % (SITE_NAME, self._base())]
            line.append('home 分类 %d / 首页 %d' % (len(cls), len(h.get('list') or [])))
            tid = next((c['type_id'] for c in cls if c['type_id'].startswith('cate_')), 'new')
            c = self.categoryContent(tid, '1', True, {})
            line.append('cat %s → %d 条' % (tid, len(c.get('list') or [])))
            cd = self.categoryContent('dark_65', '1', False, {})
            line.append('dark → %d 条' % len(cd.get('list') or []))
            if c.get('list'):
                first = c['list'][0]
                vid = first['vod_id']
                line.append('首条《%s》图=%s' % (str(first['vod_name'])[:14],
                                            '有' if first['vod_pic'] else '无'))
                d = self.detailContent([vid])
                one = (d.get('list') or [{}])[0]
                grp = (one.get('vod_play_url') or '').split('$$$')[0]
                play = grp.split('#')[0].split('$')[-1]
                line.append('detail %s → %s' % (vid, (play or '空')[:56]))
                p = self.playerContent('', vid)
                line.append('player → %s' % (p.get('url') or '空')[:56])
                b = self._fetch_bytes(play, 15)
                line.append('m3u8 实拉 %s' % ((str(len(b)) + ' 字节') if b else '失败'))
                pic = first['vod_pic']
                if '127.0.0.1' in pic:
                    b2 = self._fetch_bytes(pic, 15)
                    line.append('封面中继 %s' % ((str(len(b2)) + ' 字节') if b2 else '失败'))
            s = self.searchContent('秘书', True)
            line.append('search 秘书 → %d 条' % len(s.get('list') or []))
            return ' | '.join(line)
        except Exception as e:
            return '%s · 自检异常 %s' % (SITE_NAME, str(e)[:140])


if __name__ == '__main__':
    sp = Spider().init()
    print(sp.check())
