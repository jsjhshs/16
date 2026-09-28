# coding=utf-8
"""
WNZS（网络杀手 / 5ibox 站群）· TVBox Python 插件 · 纯标准库自实现（不需要 requests / pycryptodome）
=====================================================================================
接口： homeContent / homeVideoContent / categoryContent / detailContent / searchContent / playerContent
挂法： {"name":"🔞WNZS┃网络杀手","type":3,"api":"py_wnzs.py"}
      extend 可传 {"host":"换域名","txt":"换线路TXT域","pid":1,"channelCode":"2020"}

【站情 · 2026-09-27 实地逆向】
  用户给的入口 https://jrf0h.febohz.cn/land3/?channelCode=2020.html 只是**广告落地壳**：
    · 首页 HTML 是一层 XOR 壳（key bf9cd4d420017fbea3b3582327afd2ec）→ 吐出 /land3/sx/tu/tc/main.js
    · main.js（Vite+Vue+Pinia，21 万字节）只做三件事：读 /land3/static/config.json
      拿 {apiBase, apkBase}；按**日期种子 LCG** 拼随机子域拉 APK；上报 pv/roi
    · 真站不在落地页里 —— 它在 **APK 里**：包名 app.benefit.yaml.price.tat54（Kotlin 原生，50 dex）

  APK 逆向（50 个 dex 全量字符串 + apktool 只解 smali）：
    · 域名不在常量里，是**运行时从 DNS TXT 拿的线路**：
        com/juneRain/jy/viewmodel/SplashModel -> DDNSCheck()
        解析 TXT  wnzs.5ibox.top  →  取 Answer 按 "|" 切分  →  channel_list 存 SP
        实测：wnzs.5ibox.top  TXT = "https://wnzsapia.yasqhm.cn"   ← 这就是 API 根
        （A 记录是空的，所以直接 ping 域名的做法必失败；线路轮换只改 TXT）
    · 网络层：Retrofit + OkHttp（自定义 TrustManager，**不校验证书**，可无视 TLS）
        拦截器 g5/b 只加两样东西：query 里补 appkey=tp19 与 accessToken=<JWT>
        —— 没有 HMAC / 没有签名算法 / 没有时间戳校验，请求极好复刻
    · 响应**全部加密**：见下 §加密信封
    · 游客令牌：POST /api/ai/register {pid,unique_code,model:"android",channelCode}
        自动开户发 JWT（HS256，payload: id/username/unique_code），带 accessToken 的接口才放行

【加密信封（本插件的核心）—— 跟 Java 版逐字节同一套】
  响应体 = base64(AES/ECB/PKCS5Padding(明文 JSON, key))
    key = "=^%7@84%0^!8892-"        ← 反编译 APIManager$d / la.a 拿到，16 字节 → AES-128
    java 对应：Cipher.getInstance("AES") 默认就是 ECB/PKCS5，Landroid/util/Base64;->decode(s,2)
  解密后 {"code":1,"msg":"succ","data":{...}}；个别接口（pv/roi 那类埋点）直接回明文 JSON。
  本文件里 AES-128 是手写的，保证壳里没有 pycryptodome 也能跑；有的话自动走库。

【接口】
  首页     GET  /api/video/index                      → cateParentList / areaList / companyList / cateVideo
              cateVideo = 首页真身：66 组 × 12 条 = 792 条（实测）
  最新片单 GET  /api/video/newVideoList?page=&limit=  → 总库 58,736 条（实测）分页，limit 生效
  分类片单 GET  /api/video/cateVideoList?cate_pid=&page=&limit=
              返回**分组数组** [{id,title,videoList:[...]}, ...]
              ⚠️ 实测 limit 被服务端忽略：恒为 6 组 × 12 条 = 每页 72 条，翻页靠 page
  搜索     POST /api/video/search  {keyword,page,limit}   ← 唯一 POST，body 是 JSON，参数名必须叫 keyword
  详情     GET  /api/video/detail?vid=                → video.play_url 直出 m3u8 + relatedVideoList
  标签     GET  /api/video/tagList                     → 分类的二级标签树

【取流 —— 2026-09-27 实测，全场最省事的一环】
  详情接口直接吐**绝对 m3u8**（HLS 标准，非 DRM）：
      https://m12.newpimg4.com/video/<uuid>/playlist.m3u8
  清单、分片、封面**一律免鉴权、免 Referer、免签名**（实测无 header 全 200，分片 2MB 拿到）。
  封面是带 sign/time 的绝对 URL（host 为 punycode 的 superman.xn--5nq817c96av9verb.com），
  无 header 也能 200 取到 84KB jpeg。所以本插件**不做中继**：直链直出，零依赖零维护。

【VIP 墙 —— 没有】
  抽样 9 个分类 / 18 个分页 / 816 条：isvip=0 isdark=0 占 100%，VIP 样本 0 条。
  详情里 isvip 字段在，但普通游客号取的 m3u8 实测可直接播放。故未做任何绕 VIP 逻辑。
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


class _WnzsRelay(object):
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
        self.txt = DEFAULT_TXT
        self.pid = 1
        self.channel = '2020'
        self.timeout = 20
        self.headers = {
            'User-Agent': DEFAULT_UA,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Content-Type': 'application/json; charset=utf-8',
        }
        self._token = ''
        self._host_ready = False
        self._cache = {}
        self._relay = None

    # ---------- 配置 ----------
    def init(self, extend=''):
        cfg = {}
        try:
            if extend:
                if isinstance(extend, dict):
                    cfg = dict(extend)
                else:
                    s = str(extend).strip()
                    if s.startswith('{'):
                        cfg = json.loads(s)
                    else:
                        for kv in re.split(r'[&;]', s):
                            if '=' in kv:
                                k, v = kv.split('=', 1)
                                cfg[k.strip()] = urllib.parse.unquote(v.strip())
        except Exception:
            cfg = {}
        if cfg.get('host'):
            h = str(cfg['host']).strip().rstrip('/')
            if not h.startswith('http'):
                h = 'https://' + h
            self.host = h
            self._host_ready = True
        if cfg.get('txt'):
            self.txt = str(cfg['txt']).strip()
        if cfg.get('pid'):
            try:
                self.pid = int(cfg['pid'])
            except Exception:
                pass
        if cfg.get('channelCode') or cfg.get('channel'):
            self.channel = str(cfg.get('channelCode') or cfg.get('channel'))
        if cfg.get('token'):
            self._token = str(cfg['token']).strip()
        if cfg.get('ua'):
            self.headers['User-Agent'] = str(cfg['ua'])
        return self

    def getName(self):
        return SITE_NAME

    def destroy(self):
        if self._relay is not None:
            self._relay.stop()
            self._relay = None
        self._cache = {}

    def isVideoFormat(self, url):
        u = str(url or '').lower()
        return ('.m3u8' in u) or ('.mp4' in u)

    def manualVideoCheck(self):
        return False

    # ---------- 请求层 ----------
    def _http(self, url, body=None, method='GET'):
        """requests → urllib 两级降级 + 一次重试；非 200 记最后一错"""
        last = ''
        for attempt in range(2):
            if requests is not None:
                try:
                    r = requests.request(method, url, data=body, timeout=self.timeout,
                                         headers=self.headers, verify=False)
                    if r.status_code == 200 and r.text:
                        return r.text
                    last = 'http %s' % r.status_code
                except Exception as e:
                    last = str(e)[:100]
            if _urlreq is not None:
                try:
                    req = _urlreq.Request(url, data=body, headers=self.headers, method=method)
                    raw = _urlreq.urlopen(req, timeout=self.timeout).read()
                    if raw[:2] == b'\x1f\x8b':
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    return raw.decode('utf-8', 'replace')
                except Exception as e:
                    last = str(e)[:100]
            if attempt == 0:
                time.sleep(0.6)
        self._log('request fail %s (%s)' % (url.split('?')[0], last))
        return ''

    def _fetch_bytes(self, url, timeout=None):
        """取原始字节（封面用；中继里调）"""
        to = timeout or self.timeout
        if requests is not None:
            try:
                r = requests.get(url, timeout=to, headers=self.headers, verify=False)
                if r.status_code == 200 and r.content:
                    return r.content
            except Exception:
                pass
        if _urlreq is not None:
            try:
                req = _urlreq.Request(url, headers=self.headers)
                raw = _urlreq.urlopen(req, timeout=to).read()
                if raw[:2] == b'\x1f\x8b':
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw
            except Exception:
                pass
        return None

    def _fresh_pic(self, vid):
        """封面短签过期后换一张新的：回详情接口重取 enpic（签名算在服务端，只能这么换）"""
        try:
            d = (self._get('/api/video/detail', vid=vid).get('data') or {})
            return (d.get('video') or {}).get('enpic', '') or ''
        except Exception:
            return ''

    def _ensure_relay(self):
        if self._relay is None:
            self._relay = _WnzsRelay(self)
        return self._relay if self._relay.ensure() else None

    def _pic(self, pic, vid):
        """封面统一从这里出：走本机中继（解打码 + 短签过期自动换新）；中继起不来就退回直链"""
        pic = str(pic or '')
        if not pic.startswith('http'):
            return pic
        rel = self._ensure_relay()
        if not rel:
            return pic
        return rel.img_url(pic, vid) or pic

    def _resolve_host(self):
        """线路自发现：读 wnzs.5ibox.top 的 TXT（站方换域名只改 DNS）"""
        if self._host_ready:
            return self.host
        self._host_ready = True
        try:
            txt = self._http(DOH % self.txt)
            if txt:
                j = json.loads(txt)
                for a in j.get('Answer', []) or []:
                    if int(a.get('type', 0)) == 16:
                        val = str(a.get('data', '')).strip('"').strip()
                        for one in val.split('|'):
                            one = one.strip()
                            if one.startswith('http'):
                                self.host = one.rstrip('/')
                                self._log('TXT 线路 → %s' % self.host)
                                return self.host
        except Exception as e:
            self._log('TXT 解析失败 %s' % str(e)[:80])
        self._log('TXT 无结果，用兜底 %s' % self.host)
        return self.host

    def _base(self):
        return self._resolve_host().rstrip('/')

    def _api(self, path, params=None, method='GET', body=None):
        """带 appkey / accessToken 打接口 + 自动解信封"""
        if not self._token and path != '/api/ai/register':
            self._ensure_token()
        u = urllib.parse.urlencode(params or {})
        url = '%s%s?%s' % (self._base(), path, u)
        raw = self._http(url, body=body, method=method)
        if not raw:
            return {}
        s = raw.strip()
        if s.startswith('{') or s.startswith('['):
            try:
                return json.loads(s)
            except Exception:
                return {}
        if 'Not Found' in s:
            self._log('%s 返回假 404（服务端路由拦截）' % path)
            return {}
        try:
            return json.loads(_py_decrypt(s))
        except Exception as e:
            self._log('%s 解密失败 %s' % (path, str(e)[:80]))
            return {}

    def _get(self, path, **params):
        params['appkey'] = APPKEY
        if self._token:
            params['accessToken'] = self._token
        data = self._api(path, params, 'GET')
        if isinstance(data, dict) and data.get('code') in (401, 403):
            # 令牌过期：重开游客号再打一次
            self._token = ''
            self._ensure_token()
            params['accessToken'] = self._token
            data = self._api(path, params, 'GET')
        return data

    def _post(self, path, obj):
        """POST 是 JSON body（Gson 转换器），accessToken 只能挂 query"""
        if not self._token:
            self._ensure_token()
        params = {'appkey': APPKEY, 'accessToken': self._token}
        return self._api(path, params, 'POST', json.dumps(obj).encode('utf-8'))

    def _ensure_token(self, force=False):
        if self._token and not force:
            return self._token
        ck = ('token', self._base())
        if not force and self._cache.get(ck):
            self._token = self._cache[ck]
            return self._token
        body = {
            'pid': self.pid,
            'unique_code': self._unique_code(),
            'model': 'android',
            'channelCode': self.channel,
        }
        data = self._api('/api/ai/register', {'appkey': APPKEY},
                         'POST', json.dumps(body).encode('utf-8'))
        try:
            tok = data['data']['token']
            if tok:
                self._token = str(tok)
                self._cache[ck] = self._token
                self._log('游客号开户成功 %s' % data['data'].get('userInfo', {}).get('username', ''))
        except Exception:
            self._log('游客号开户失败 %s' % json.dumps(data, ensure_ascii=False)[:150])
        return self._token

    def _unique_code(self):
        """设备唯一码：进程内固定即可（服务端拿它认账号，换一次就是一个新号）"""
        ck = 'uuid'
        if not self._cache.get(ck):
            self._cache[ck] = 'tvbox%08x' % (int(time.time() * 1000) & 0xffffffff)
        return self._cache[ck]

    @staticmethod
    def _log(msg):
        try:
            print('[wnzs] %s' % msg)
        except Exception:
            pass

    # ---------- 数据整理 ----------
    def _card(self, v):
        return {
            'vod_id': str(v.get('vid', '')),
            'vod_name': v.get('vod_name', ''),
            'vod_pic': self._pic(v.get('enpic', ''), v.get('vid', '')),
            'vod_remarks': (v.get('duration') or '') + (' · 🔥%s' % v.get('eyes') if v.get('eyes') else ''),
        }

    @staticmethod
    def _pick_list(data):
        """newVideoList 是 {data:[...]}；cateVideoList 是 [{videoList:[...]}]；统一成卡片数组"""
        out = []
        if isinstance(data, dict):
            for v in (data.get('data') or []):
                if isinstance(v, dict):
                    out.append(v)
        elif isinstance(data, list):
            for grp in data:
                if isinstance(grp, dict):
                    for v in (grp.get('videoList') or []):
                        if isinstance(v, dict):
                            out.append(v)
        return out

    # ---------- 五个入口 ----------
    def homeContent(self, filter=False):
        classes = []
        filters = {}
        try:
            d = self._get('/api/video/index').get('data') or {}
            for c in d.get('cateParentList') or []:
                tid = 'cate_%s' % c.get('cate_pid')
                classes.append({'type_id': tid, 'type_name': c.get('title', '')})
                filters[tid] = [
                    {'key': 'area', 'name': '地区', 'value': [{'n': '全部', 'v': ''}] +
                        [{'n': a.get('name', ''), 'v': a.get('area', '')} for a in (d.get('areaList') or [])]},
                    {'key': 'sort', 'name': '排序', 'value': [
                        {'n': '最新', 'v': 'new'}, {'n': '最热', 'v': 'hot'}]},
                ]
        except Exception as e:
            self._log('home 分类异常 %s' % str(e)[:90])
        classes.insert(0, {'type_id': 'new', 'type_name': '🔥最新入库'})
        home = []
        try:
            # 首页真身：/api/video/index 的 cateVideo —— 66 组 × 12 条（实测 792）
            cv = (self._get('/api/video/index').get('data') or {}).get('cateVideo') or []
            for v in self._pick_list(cv):
                home.append(v)
                if len(home) >= HOME_LIMIT:
                    break
        except Exception:
            home = []
        if not home:
            try:
                home = self._pick_list(
                    self._get('/api/video/newVideoList', page=1, limit=PAGE_SIZE).get('data'))
            except Exception:
                home = []
        return {'class': classes, 'filters': filters, 'list': [self._card(v) for v in home]}

    def homeVideoContent(self):
        try:
            cv = (self._get('/api/video/index').get('data') or {}).get('cateVideo') or []
            items = self._pick_list(cv)[:HOME_LIMIT]
        except Exception:
            items = []
        if not items:
            items = self._pick_list(self._get('/api/video/newVideoList', page=1, limit=PAGE_SIZE).get('data'))
        return {'list': [self._card(v) for v in items]}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        extend = extend or {}
        try:
            if str(tid) == 'new':
                d = self._get('/api/video/newVideoList', page=page, limit=PAGE_SIZE).get('data')
                items = self._pick_list(d)
                total = int((d or {}).get('total') or 0) if isinstance(d, dict) else 0
            else:
                pid = str(tid).replace('cate_', '')
                params = {'cate_pid': pid, 'page': page, 'limit': PAGE_SIZE}
                if extend.get('area'):
                    params['area'] = extend['area']
                if extend.get('sort'):
                    params['sort'] = extend['sort']
                d = self._get('/api/video/cateVideoList', **params).get('data')
                items = self._pick_list(d)
                total = 0
        except Exception as e:
            self._log('category 异常 %s' % str(e)[:90])
            items, total = [], 0
        return {
            'list': [self._card(v) for v in items],
            'page': page,
            'pagecount': 9999,
            'limit': PAGE_SIZE,
            'total': total or (page * PAGE_SIZE + (1 if items else 0)),
        }

    def detailContent(self, ids):
        vid = str(ids[0] if isinstance(ids, (list, tuple)) else ids).strip()
        d = (self._get('/api/video/detail', vid=vid).get('data') or {})
        v = d.get('video') or {}
        if not v:
            return {'list': []}
        play = v.get('play_url') or ''
        name = v.get('vod_name', '')
        rel = self._pick_list(d.get('relatedVideoList'))
        out = {
            'vod_id': vid,
            'vod_name': name,
            'vod_pic': self._pic(v.get('enpic', ''), vid),
            'type_name': v.get('vod_play_from', 'WNZS'),
            'vod_year': (v.get('update_time') or '')[:4],
            'vod_remarks': v.get('duration', ''),
            'vod_content': '%s\n播放源：%s · 时长 %s · 播放 %s\n更新：%s' % (
                name, v.get('vod_play_from', ''), v.get('duration', ''),
                v.get('eyes', ''), v.get('update_time', '')),
            'vod_play_from': 'WNZS直连',
            'vod_play_url': '%s$%s' % (name.replace('$', ' '), play) if play else '',
        }
        if rel:
            out["vod_play_from"] += "$$$🔎相关推荐"
            out['vod_play_url'] += '$$$' + '#'.join(
                '%s$%s' % (r.get('vod_name', '').replace('$', ' '), r.get('vid', ''))
                for r in rel[:REL_LIMIT])
        return {'list': [out]}

    def searchContent(self, key, quick, pg='1'):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        items = []
        try:
            d = self._post('/api/video/search', {'keyword': key, 'page': page, 'limit': PAGE_SIZE}).get('data')
            items = self._pick_list(d)
        except Exception as e:
            self._log('search 异常 %s' % str(e)[:90])
        return {'list': [self._card(v) for v in items]}

    def playerContent(self, flag, id, vipFlags=None):
        """详情里直接给的就是 m3u8 绝对地址 → 直连播放，无需任何 header"""
        url = str(id or '').strip()
        if url.isdigit():
            d = (self._get('/api/video/detail', vid=url).get('data') or {})
            url = (d.get('video') or {}).get('play_url', '')
        return {
            'parse': 0,
            'playUrl': '',
            'url': url,
            'header': json.dumps({'User-Agent': self.headers['User-Agent']}),
        }

    # ---------- 自检 ----------
    def check(self):
        """真打真站跑一遍五入口，返回一条可读结果串"""
        try:
            h = self.homeContent(True)
            cls = h.get('class') or []
            line = ['%s · 线路 %s' % (SITE_NAME, self._base())]
            line.append('home 分类 %d / 首页 %d' % (len(cls), len(h.get('list') or [])))
            tid = cls[1]['type_id'] if len(cls) > 1 else 'new'
            c = self.categoryContent(tid, '1', True, {})
            line.append('cat %s → %d 条' % (tid, len(c.get('list') or [])))
            if c.get('list'):
                vid = c['list'][0]['vod_id']
                d = self.detailContent([vid])
                one = (d.get('list') or [{}])[0]
                grp = (one.get('vod_play_url') or '').split('$$$')[0]
                play = grp.split('#')[0].split('$')[-1]
                line.append('detail %s → 播放 %s' % (vid, play[:56] or '空'))
                p = self.playerContent('', vid)
                line.append('player → %s' % (p.get('url') or '空')[:56])
            s = self.searchContent('秘书', True)
            line.append('search 秘书 → %d 条' % len(s.get('list') or []))
            return ' | '.join(line)
        except Exception as e:
            return '%s · 自检异常 %s' % (SITE_NAME, str(e)[:120])


if __name__ == '__main__':
    sp = Spider().init()
    print(sp.check())
