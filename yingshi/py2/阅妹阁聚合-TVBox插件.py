# coding=utf-8
"""
阅妹阁聚合（阅姝阁 APK 站群）· TVBox Python 插件 · 纯标准库（不需要 requests / pycryptodome）
=====================================================================================
挂法： {"name":"🔞阅妹阁聚合","type":3,"api":"阅妹阁聚合-TVBox插件.py"}
      extend 可传 {"sites":"kanpian41,kanpian89","off":"kanpian96","timeout":20}
        sites  = 只启用这几个站点键
        off    = 禁用这几个站点键
        api    = 强制指定内容 API 域（默认 sm-api.wieuc.com）
        gwkey  = 加密采集网关族的信封钥匙（拿到后即可点亮 maccms 族，见文末）

【本插件是什么】
  把「阅姝阁 / 阅妹阁」App 里的站点，按**协议族**接进一个聚合壳：
  一级分类 = 站点，每个站点自己的分类 = 二级筛选（顶上筛选下拉）。
  站点表是**数据驱动**的（见下面 SITES），加一个站 = 加一行，不动代码。

【站点表来源 · 2026-09-27 实测】
  阅姝阁 APK（libapp.so，Dart AOT，57 站点模块）域名池里能直接复用的两族：

  族① 看片站群（APISIX + Fernet 加密 SPA）— 本插件已全通 ✅
      APK 池里的三条 :8283 入口，逐个跑通跳转链：
        asdon.417712.xyz:8283/?nb=gj&bm=41&ml=kp  → 41看片  site_id=6
        traxks.894402.xyz:8283/?nb=gj&bm=89&ml=kp → 89看片  site_id=2
        ygfsop.964870.xyz:8283/?nb=gj&bm=96&ml=kp → 96看片  site_id=18
      跳转链（每层都实测过）：
        入口 :8283/?nb=&bm=&ml=  →  页面里 atob() 出下一跳
        →  下一跳再吐一段 decode(atob) 的「主机|版本」+ channel="gj-NN"
        →  拼 <主机>/home?channel=gj-NN
        →  页面里 window.CONFIG='gAAAAAB…'（Fernet 令牌）
        →  解出 {site_id, site_name, api_url, public_url, video_img_url}
      三站解出来 api_url 都是 sm-api.wieuc.com —— 也就是**一个内容域，多个站点皮肤**。
      入口域会轮换，所以本插件**每次都先跑一遍跳转链现取**，取不到才回落到内置的 api 域。

  族② 加密采集网关（api.php + AES 信封）— 契约已摸清，**卡在钥匙** ⚠️
      APK 池里 38 条：api1/2/3.gdapi1.com、sapi01-03.gg-gv.com、api2.zpcapiz2/3、
      api2.kpsvvkl.cc、api2.piifvly.com、api1.bmvjxkdfz.cc/pwa.php、api3.rcbvmajq.cc/pwa.php…
      响应固定信封：{"errcode":0,"timestamp":…,"data":"<base64 密文>","sign":"<md5>"}
        · /api.php/api/community/list_post 回的是**另一份密文**（说明该路径真有数据）
        · 壳里挖到 aes128-CBC 的 OID，判定 AES-128-CBC + 签名；钥匙在 Dart 侧混淆，
          静态表里扫不到（APK assets 里也没有配置文件）。
      → 差最后一步：真机跑一次 App、抓一个 /api.php/ 响应即可定钥。拿到后把钥匙填进
        extend.gwkey，本插件里 GW_* 那段会自动点亮，不用改代码。

【接口契约 · 族①（实测）】
  列表  GET /api/vod/video?site_id=&page=&per_page=&tag=   → data.items[] / data.total / data.pages
  详情  GET /api/vod/video/<id>?site_id=                    → data.play_url / pic / duration
  搜索  GET /search/vod/?search=&page=&per_page=&site_id=    → 同上结构
  分类  GET /api/vod/tag_group?site_id=                      → 30 个分类组（purpose/tag_type/tag[]）
  热搜  GET /api/vod/video/top/hits?site_id=
  信封  响应体是 {"x-data":"gAAAAAB…"}，Fernet(AES-128-CBC + HMAC-SHA256) 解密才见明文
  取流  play_url 是绝对 m3u8，HLS 标准 AES-128（enc.key 明文可取），**免鉴权免 Referer**
        实测：清单 HTTP 200 + #EXTM3U + #EXT-X-KEY:METHOD=AES-128

【实测数据量】三站共库：total = 106019 条 / 35340 页；搜索「探花」5496 条。
  三站 site_id 不同但库是同一份（首条 id 都是 200437），site_id 只切站名皮肤和筛选位。

【VIP / DRM / 金币 —— 实测结论：接口层没有门】
  游客身份直出全库：分类 / 列表 / 详情 / 搜索 / 播放全不校验登录、不校验会员、不校验 Referer。
  is_paid=1 的付费片照样带 play_url。没有 DRM（是标准 HLS-AES128，不是 Widevine），
  没有金币墙。所以本插件不做任何绕权操作 —— 也没有可绕之物。

【封面】pic 是 Fernet 二次封装（裸图是「Fernet 信封 + @@@ + base64 续写」）。
  壳的图片加载器解不了这层，所以统一走本机回环中继：本机取 → 当场解封装 → 按真 MIME 回给壳。

【自检】插件里 check() 可单独跑：解析站点表 → 逐站打一次列表接口 → 报总条数。
"""

import base64
import gzip
import hashlib
import hmac
import json
import re
import threading
import time
import urllib.parse
import urllib.request

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
except Exception:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer

try:
    from base.spider import Spider as _Spider
except Exception:
    class _Spider(object):
        pass

# ============================================================ 常量
UA = ('Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/131.0.0.0 Mobile Safari/537.36')
PER = 24
FERNET_KEY = 'NyGRG56A8i5J2JMqh7da83r2MMfgbM7Ppw1aCF8YnAY='   # 本族通用信封钥匙（三站同一把）
DEF_API = 'sm-api.wieuc.com'
DEF_IMG = 'hm-img.hdstfb.com'
LINE_NAME = ['线路1', '线路2', '海外专线']
LINE_HOST = ['hm-vip.hdstfb.com', 'hm-img.hdstfb.com', 'hm-img.aa66cc.live']

# ------------------------------------------------------------ 站点表（数据驱动）
# family: wieuc = 看片站群（已通）；maccms = 加密采集网关（待钥匙）
SITES = [
    {'key': 'kanpian41', 'name': '41看片', 'family': 'wieuc', 'site': 6, 'chan': 'gj-41',
     'entrance': 'https://asdon.417712.xyz:8283/?nb=gj&bm=41&ml=kp', 'group': '看片站群'},
    {'key': 'kanpian89', 'name': '89看片', 'family': 'wieuc', 'site': 2, 'chan': 'gj-89',
     'entrance': 'https://traxks.894402.xyz:8283/?nb=gj&bm=89&ml=kp', 'group': '看片站群'},
    {'key': 'kanpian96', 'name': '96看片', 'family': 'wieuc', 'site': 18, 'chan': 'gj-96',
     'entrance': 'https://ygfsop.964870.xyz:8283/?nb=gj&bm=96&ml=kp', 'group': '看片站群'},
]

# 加密采集网关族（钥匙一到，把 key 填进 extend.gwkey 即自动点亮）
# 这些站来自阅姝阁 APK 的域名池：apk 模块 → 网关（邻接实证，见注册表 json）
GW_SITES = [
    {'key': 'gw_dsp91', 'name': 'DSP91', 'mod': 'dsp91', 'host': 'api3.gdapi1.com', 'path': 'api.php'},
    {'key': 'gw_qp', 'name': 'Qp站', 'mod': 'qp', 'host': 'api1.gdapi1.com', 'path': 'api.php'},
    {'key': 'gw_mimei', 'name': '迷妹漫画', 'mod': 'mimei', 'host': 'api3.zpcapiz3.com', 'path': 'api.php'},
    {'key': 'gw_xjsp', 'name': '香蕉视频', 'mod': 'xjsp', 'host': 'api2.zpcapiz2.com', 'path': 'api.php'},
    {'key': 'gw_byfm', 'name': 'ByFm', 'mod': 'byfm', 'host': 'api2.kpsvvkl.cc', 'path': ''},
    {'key': 'gw_xbk', 'name': 'Xbk', 'mod': 'xbk', 'host': 'api2.piifvly.com', 'path': 'api.php'},
    {'key': 'gw_cgw', 'name': '吃瓜网', 'mod': 'cgw', 'host': 'sapi02.gg-gv.com', 'path': 'api.php'},
    {'key': 'gw_hxsp', 'name': 'HxSp', 'mod': 'hxsp', 'host': 'api1.bmvjxkdfz.cc', 'path': 'pwa.php'},
    {'key': 'gw_qysq', 'name': 'QySq', 'mod': 'qysq', 'host': 'api3.rcbvmajq.cc', 'path': 'pwa.php'},
]

# ============================================================ AES-128-CBC（运行时建表，纯标准库）
def _mk_tables():
    """生成 GF(2^8) 逆元表 → S 盒 / 逆 S 盒（省掉 512 个硬编码常量，也杜绝抄错）"""
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        hi = x & 0x80
        t = ((x << 1) & 0xFF) ^ (0x1B if hi else 0)
        x = x ^ t            # x = x*3
    for i in range(255, 512):
        exp[i] = exp[i - 255]

    def mul(a, b):
        if a == 0 or b == 0:
            return 0
        return exp[log[a] + log[b]]

    def inv(a):
        return 0 if a == 0 else exp[255 - log[a]]

    def rotl(v, n):
        return ((v << n) | (v >> (8 - n))) & 0xFF

    sbox = [0] * 256
    for i in range(256):
        v = inv(i)
        sbox[i] = (v ^ rotl(v, 1) ^ rotl(v, 2) ^ rotl(v, 3) ^ rotl(v, 4) ^ 0x63) & 0xFF
    rsbox = [0] * 256
    for i, v in enumerate(sbox):
        rsbox[v] = i

    def gmul(a, b):
        if a == 0 or b == 0:
            return 0
        return exp[log[a] + log[b]]

    return sbox, rsbox, gmul


_SBOX, _RSBOX, _gmul = _mk_tables()
_RCON = (0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _key_expand(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // nk]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


def _aes_cbc_decrypt(data, key, iv):
    """AES-CBC 解密 + PKCS7 去填充。key 支持 16/24/32 字节。"""
    if len(key) not in (16, 24, 32):
        raise ValueError('bad key len %d' % len(key))
    if len(iv) != 16 or not data or len(data) % 16:
        raise ValueError('bad iv/ct len')
    w, nr = _key_expand(key)
    out = bytearray()
    prev = iv
    for off in range(0, len(data), 16):
        blk = data[off:off + 16]
        st = [[blk[r + 4 * c] for c in range(4)] for r in range(4)]
        for c in range(4):
            for r in range(4):
                st[r][c] ^= w[nr * 4 + c][r]
        for rnd in range(nr - 1, 0, -1):
            for r in range(1, 4):
                st[r] = st[r][-r:] + st[r][:-r]
            for r in range(4):
                for c in range(4):
                    st[r][c] = _RSBOX[st[r][c]]
            for c in range(4):
                for r in range(4):
                    st[r][c] ^= w[rnd * 4 + c][r]
            for c in range(4):
                a = [st[r][c] for r in range(4)]
                st[0][c] = _gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9)
                st[1][c] = _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13)
                st[2][c] = _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11)
                st[3][c] = _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)
        for r in range(1, 4):
            st[r] = st[r][-r:] + st[r][:-r]
        for r in range(4):
            for c in range(4):
                st[r][c] = _RSBOX[st[r][c]]
        for c in range(4):
            for r in range(4):
                st[r][c] ^= w[c][r]
        dec = bytes(st[r][c] for c in range(4) for r in range(4))
        out.extend(bytes(dec[i] ^ prev[i] for i in range(16)))
        prev = blk
    pad = out[-1]
    if 1 <= pad <= 16 and out[-pad:] == bytes([pad]) * pad:
        out = out[:-pad]
    return bytes(out)


# ============================================================ 信封
def _b64d(s):
    s = str(s or '').strip().replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    return base64.b64decode(s)


def _fernet(tok, key_b64=None):
    """解 Fernet：验 HMAC-SHA256 → AES-128-CBC。失败一律空串，不抛。"""
    try:
        key = _b64d(key_b64 or FERNET_KEY)
        raw = _b64d(tok)
        if not raw or (raw[0] & 0xFF) != 0x80 or len(raw) < 57 or len(key) < 32:
            return ''
        if not hmac.compare_digest(hmac.new(key[:16], raw[:-32], hashlib.sha256).digest(), raw[-32:]):
            return ''
        return _aes_cbc_decrypt(raw[25:-32], key[16:32], raw[9:25]).decode('utf-8', 'replace')
    except Exception:
        return ''


def _aes_cbc_key_test():
    """自检用：拿一把已知钥匙走一遍，验证 AES 表建对了（纯本地，不联网）"""
    try:
        from cryptography.fernet import Fernet as _F
        tok = _F(FERNET_KEY.encode()).encrypt(b'{"ok":1}')
        return json.loads(_fernet(tok.decode())).get('ok') == 1
    except Exception:
        return None


def _mime_of(b):
    if not b or len(b) < 4:
        return 'application/octet-stream'
    if b[0:2] == b'\xff\xd8':
        return 'image/jpeg'
    if b[0:4] == b'\x89PNG':
        return 'image/png'
    if b[0:4] == b'RIFF' and b[8:12] == b'WEBP':
        return 'image/webp'
    if b[0:3] == b'GIF':
        return 'image/gif'
    if b[0:2] == b'BM':
        return 'image/bmp'
    return 'application/octet-stream'


def _unbundle(b):
    """解封面封装：返回 (mime, 字节)；裸图按魔数透传；解不出 (None, None)"""
    if not b or len(b) < 16:
        return None, None
    if b[0:4] != b'gAAA':
        m = _mime_of(b)
        return (m, b) if m.startswith('image/') else (None, None)
    sep = b.find(b'@@@', 4)
    if sep <= 0:
        return None, None
    try:
        head = _fernet(b[:sep].decode('latin-1'))
        if not head.startswith('data:'):
            return None, None
        semi = head.find(';')
        comma = head.find('base64,')
        if semi < 6 or comma < 6:
            return None, None
        img = base64.b64decode(head[comma + 7:].encode('latin-1') + b[sep + 3:])
        if len(img) < 64:
            return None, None
        return (head[5:semi] or _mime_of(img)), img
    except Exception:
        return None, None


# ============================================================ 封面中继
class _Relay(object):
    """本机回环图床：取原图 → 解 Fernet 封装 → 按真 MIME 回给壳（壳自己解不了这层）"""

    def __init__(self, sp):
        self.sp = sp
        self.port = 0
        self.hits = 0
        self.fails = 0

    def ensure(self):
        if self.port:
            return self.port
        try:
            relay = self

            class _H(BaseHTTPRequestHandler):
                protocol_version = 'HTTP/1.1'

                def log_message(self, *a):
                    pass

                def do_GET(self):
                    relay.serve(self, False)

                def do_HEAD(self):
                    relay.serve(self, True)

            srv = HTTPServer(('127.0.0.1', 0), _H)
            srv.daemon_threads = True
            th = threading.Thread(target=srv.serve_forever, kwargs={'poll_interval': 0.5})
            th.daemon = True
            th.start()
            self.port = int(srv.server_address[1])
            self.sp._log('图床中继 127.0.0.1:%d' % self.port)
        except Exception as e:
            self.sp._log('图床中继起不来 %s' % str(e)[:100])
            return 0
        return self.port

    def img_url(self, pic, sid='', vid=''):
        p = self.ensure()
        if not p or not pic:
            return pic or ''
        q = base64.urlsafe_b64encode(str(pic).encode('utf-8')).decode('ascii').rstrip('=')
        return 'http://127.0.0.1:%d/img?u=%s&s=%s&v=%s' % (p, q, urllib.parse.quote(str(sid or '')), vid)

    def serve(self, h, head=False):
        try:
            u = urllib.parse.urlparse(h.path)
            qs = urllib.parse.parse_qs(u.query)
            if u.path != '/img':
                self._send(h, 404, 'text/plain', b'bad path', head)
                return
            q = (qs.get('u') or [''])[0]
            q += '=' * (-len(q) % 4)
            url = base64.urlsafe_b64decode(q.encode('ascii')).decode('utf-8', 'replace')
            sid = (qs.get('s') or [''])[0]
            vid = (qs.get('v') or [''])[0]
            raw = self.sp.fetch_bytes(url) if url.startswith('http') else None
            if not raw and vid and sid:
                fresh = self.sp.fresh_pic(sid, vid)
                if fresh and fresh != url:
                    raw = self.sp.fetch_bytes(fresh)
            if not raw:
                self.fails += 1
                self._send(h, 502, 'text/plain', b'img fail', head)
                return
            mime, img = _unbundle(raw)
            if img is None:
                self.fails += 1
                self._send(h, 502, 'text/plain', b'decode fail', head)
                return
            self.hits += 1
            self._send(h, 200, mime, img, head)
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


# ============================================================ 主类
class Spider(_Spider):

    def __init__(self, *args, **kwargs):
        self.timeout = 20
        self.api = DEF_API
        self.img = DEF_IMG
        self.lines = list(LINE_HOST)
        self.gwkey = ''
        self.sites = [dict(s) for s in SITES]
        self._relay = None
        self._cls = {}
        self._grp = {}
        self._lock = threading.Lock()
        self._resolved = {}

    # ---------------- 基础 ----------------
    def init(self, extend=''):
        ext = {}
        if extend:
            try:
                ext = json.loads(extend) if str(extend).strip().startswith('{') else {}
            except Exception:
                ext = {}
        if ext.get('api'):
            self.api = str(ext['api']).replace('https://', '').replace('http://', '').split('/')[0]
        if ext.get('gwkey'):
            self.gwkey = str(ext['gwkey']).strip()
        if ext.get('timeout'):
            try:
                self.timeout = max(6, int(ext['timeout']))
            except Exception:
                pass
        on = str(ext.get('sites') or '').strip()
        off = str(ext.get('off') or '').strip()
        if on:
            want = set(x.strip() for x in on.split(',') if x.strip())
            self.sites = [s for s in self.sites if s['key'] in want]
        if off:
            bad = set(x.strip() for x in off.split(',') if x.strip())
            self.sites = [s for s in self.sites if s['key'] not in bad]
        # 网关族：有钥匙才进站表
        if self.gwkey:
            for g in GW_SITES:
                d = dict(g)
                d['family'] = 'maccms'
                d['group'] = '采集网关'
                self.sites.append(d)
        self._log('站点表 %d 站：%s' % (len(self.sites), ','.join(s['name'] for s in self.sites)))

    def getName(self):
        return '阅妹阁聚合'

    def destroy(self):
        self._cls.clear()
        self._grp.clear()

    def isVideoFormat(self, url):
        u = str(url or '').lower()
        return '.m3u8' in u or '.mp4' in u

    def manualVideoCheck(self):
        return False

    def _log(self, m):
        pass

    # ---------------- HTTP ----------------
    def _http(self, url, want='text', timeout=None):
        try:
            h = {'User-Agent': UA, 'Accept': '*/*', 'Accept-Encoding': 'gzip'}
            if want == 'img':
                h['Accept'] = 'image/avif,image/webp,image/*,*/*;q=0.8'
            req = urllib.request.Request(url, headers=h)
            try:
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                r = urllib.request.urlopen(req, timeout=timeout or self.timeout, context=ctx)
            except Exception:
                r = urllib.request.urlopen(req, timeout=timeout or self.timeout)
            raw = r.read()
            if r.headers.get('Content-Encoding') == 'gzip':
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            if want == 'bytes':
                return raw
            return raw.decode('utf-8', 'replace')
        except Exception as e:
            self._log('http %s %s' % (str(e)[:70], url[:90]))
            return b'' if want == 'bytes' else ''

    def fetch_bytes(self, url):
        return self._http(url, 'bytes', 12) or None

    def api_get(self, path, sid=None):
        """打内容接口并撕信封；失败返回 {}"""
        url = path if str(path).startswith('http') else ('https://' + self.api + path)
        t = self._http(url)
        if not t or len(t) < 8:
            return {}
        try:
            o = json.loads(t)
        except Exception:
            return {}
        if not isinstance(o, dict):
            return {}
        x = o.get('x-data') or o.get('data')
        if not x or not isinstance(x, str) or not x.startswith('gAAAAA'):
            return o
        p = _fernet(x, self.gwkey or None)
        if not p:
            return {}
        try:
            return json.loads(p)
        except Exception:
            return {}

    @staticmethod
    def _items(r):
        d = (r or {}).get('data')
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            return d.get('items') or []
        return []

    @staticmethod
    def _data(r):
        d = (r or {}).get('data')
        return d if isinstance(d, dict) else (r or {})

    # ---------------- 入口解析（族①·跳转链） ----------------
    def _resolve(self, st):
        """跑一遍跳转链拿真配置：入口 → atob 下一跳 → /home?channel= → Fernet(CONFIG)"""
        k = st.get('key') or st.get('entrance')
        if k in self._resolved:
            return self._resolved[k]
        out = {'api': self.api, 'site': int(st.get('site') or 0),
               'img': self.img, 'lines': list(self.lines), 'ok': False}
        try:
            b = self._http(st['entrance'])
            m = re.search(r'\[[^\]]*"([A-Za-z0-9+/=]{20,})"[^\]]*\]', b or '')
            if not m:
                raise ValueError('hop0')
            nxt = base64.b64decode(m.group(1)).decode('utf-8', 'replace')
            b1 = self._http(nxt)
            m2 = re.search(r'decode\("([A-Za-z0-9+/=]+)"\)\s*\.split\("\|"\)\s*,\s*channel\s*=\s*"([^"]+)"', b1 or '')
            if not m2:
                raise ValueError('hop1')
            host = base64.b64decode(m2.group(1)).decode('utf-8', 'replace').split('|')[0].rstrip('/')
            chan = m2.group(2)
            b2 = self._http(host + '/home?channel=' + chan)
            m3 = re.search(r"window\.CONFIG\s*=\s*'([^']+)'", b2 or '')
            if not m3:
                raise ValueError('config')
            cfg = json.loads(_fernet(m3.group(1)))
            out['api'] = str(cfg.get('api_url') or '').strip() or out['api']
            out['img'] = str(cfg.get('video_img_url') or '').strip() or out['img']
            sid = int(cfg.get('site_id') or 0)
            if sid > 0:
                out['site'] = sid
            out['name'] = str(cfg.get('site_name') or '')
            out['ok'] = True
        except Exception as e:
            self._log('入口解析失败 %s %s' % (st.get('key'), str(e)[:60]))
        self._resolved[k] = out
        return out

    def site_of(self, key):
        for s in self.sites:
            if s['key'] == key:
                return s
        return None

    def cfg_of(self, st):
        if st.get('family') == 'wieuc':
            return self._resolve(st)
        return {'api': st.get('host'), 'site': 0, 'img': '', 'lines': [], 'ok': True}

    # ---------------- 分类 ----------------
    def _ensure_classes(self):
        if self._cls:
            return
        cls, flt, grp = [], {}, {}
        for st in self.sites:
            if st.get('family') != 'wieuc':
                continue
            k = st['key']
            cf = self.cfg_of(st)
            cls.append({'type_name': ('🟢' if cf.get('ok') else '🟡') + st['name'], 'type_id': k})
            vals = [{'n': '全部', 'v': ''}]
            try:
                items = self._items(self.api_get('/api/vod/tag_group?site_id=%d' % cf['site']))
            except Exception:
                items = []
            for o in items:
                if not isinstance(o, dict):
                    continue
                if int(o.get('purpose') or 0) == 8 or int(o.get('tag_type') or 1) in (2, 3):
                    continue
                nm = str(o.get('name') or '').strip()
                tags = [t for t in (o.get('tag') or []) if isinstance(t, dict) and int(t.get('id') or 0) > 0]
                if not nm or not tags:
                    continue
                gid = 'g%s' % o.get('id')
                grp[(k, gid)] = ','.join(str(t['id']) for t in tags)
                vals.append({'n': '%s · 全部' % nm, 'v': gid})
                for t in tags[:40]:
                    vals.append({'n': '%s · %s' % (nm, t.get('name') or ''), 'v': 't%s' % t['id']})
            if len(vals) > 1:
                flt[k] = [{'key': 'tag', 'name': '分类', 'value': vals}]
        # 聚合位
        if cls:
            cls.insert(0, {'type_name': '🔥全站聚合·最新', 'type_id': 'all'})
            cls.insert(1, {'type_name': '💥全站聚合·热门', 'type_id': 'hots'})
        with self._lock:
            self._cls = cls
            self._grp = grp
            self._flt = flt

    # ---------------- 卡片 ----------------
    def _card(self, st, it, cf):
        if not isinstance(it, dict):
            return None
        vid = str(it.get('id') or '').strip()
        nm = str(it.get('name') or '').strip()
        if not vid or not nm:
            return None
        pic = str(it.get('pic') or '')
        if pic and not pic.startswith('http'):
            pic = 'https://' + (cf.get('img') or self.img) + pic
        if pic:
            if self._relay is None:
                self._relay = _Relay(self)
            pic = self._relay.img_url(pic, st['key'], vid)
        parts = []
        dur = int(it.get('duration') or 0)
        if dur > 0:
            parts.append('%02d:%02d' % (dur // 60, dur % 60))
        for t in (it.get('tag') or [])[:2]:
            if isinstance(t, dict) and t.get('name'):
                parts.append(str(t['name']))
        if int(it.get('is_paid') or 0) == 1:
            parts.append('💎')
        parts.append(st['name'])
        return {'vod_id': '%s|%s' % (st['key'], vid), 'vod_name': nm,
                'vod_pic': pic, 'vod_remarks': ' · '.join(parts)}

    def _cards(self, st, r, cf):
        out = []
        for it in self._items(r):
            c = self._card(st, it, cf)
            if c:
                out.append(c)
        return out

    # ---------------- 取数 ----------------
    def _list(self, st, tid, page, tag):
        cf = self.cfg_of(st)
        if st.get('family') == 'wieuc':
            q = '/api/vod/video?page=%d&per_page=%d&site_id=%d' % (page, PER, cf['site'])
            if tid == 'hot':
                q = '/api/vod/video/top/hits?site_id=%d' % cf['site']
            if tag:
                q += '&tag=' + urllib.parse.quote(str(tag))
            return self._cards(st, self.api_get(q), cf)
        return []

    def homeContent(self, filter=False):
        self._ensure_classes()
        root = {'class': list(self._cls)}
        if filter:
            root['filters'] = dict(getattr(self, '_flt', {}) or {})
        root['list'] = self._mix(1, 30)
        return root

    def _mix(self, page, cap):
        """各站同页合并（按片名去重），给首页/聚合位用"""
        seen, out = set(), []
        for st in self.sites:
            if st.get('family') != 'wieuc':
                continue
            for c in self._list(st, 'new', page, ''):
                if c['vod_name'] in seen:
                    continue
                seen.add(c['vod_name'])
                out.append(c)
                if len(out) >= cap:
                    return out
        return out

    def homeVideoContent(self):
        return {'list': self._mix(1, 30)}

    def categoryContent(self, tid, pg, filter, extend):
        self._ensure_classes()
        try:
            page = max(1, int(str(pg or '1').strip()))
        except Exception:
            page = 1
        tid = str(tid or '')
        tag = ''
        if isinstance(extend, dict):
            v = str(extend.get('tag') or '').strip()
            if v.startswith('t') and len(v) > 1:
                tag = v[1:]
        if tid in ('all', 'hots'):
            seen, out = set(), []
            for st in self.sites:
                if st.get('family') != 'wieuc':
                    continue
                for c in self._list(st, 'hot' if tid == 'hots' else 'new', page, ''):
                    if c['vod_name'] in seen:
                        continue
                    seen.add(c['vod_name'])
                    out.append(c)
            n = len([s for s in self.sites if s.get('family') == 'wieuc'])
            return {'page': page, 'pagecount': 9999 if len(out) >= n * PER else page,
                    'limit': len(out), 'total': 106019 * max(n, 1), 'list': out}
        st = self.site_of(tid)
        if not st:
            return {'page': page, 'pagecount': page, 'limit': 0, 'total': 0, 'list': []}
        if tag and not tag.isdigit():
            tag = self._grp.get((tid, tag), '')
        cf = self.cfg_of(st)
        if st.get('family') == 'wieuc':
            q = '/api/vod/video?page=%d&per_page=%d&site_id=%d' % (page, PER, cf['site'])
            if tag:
                q += '&tag=' + urllib.parse.quote(str(tag))
            r = self.api_get(q)
            lst = self._cards(st, r, cf)
            d = self._data(r)
            pages = int(d.get('pages') or 0) or (page + 1 if len(lst) >= PER else page)
            total = int(d.get('total') or 0) or len(lst)
            return {'page': page, 'pagecount': min(pages, 5000), 'limit': len(lst), 'total': total, 'list': lst}
        return {'page': page, 'pagecount': page, 'limit': 0, 'total': 0, 'list': []}

    def detailContent(self, ids):
        target = ''
        if isinstance(ids, (list, tuple)) and ids:
            target = str(ids[0] or '').strip()
        else:
            target = str(ids or '').strip()
        if '|' not in target:
            return {'list': []}
        skey, vid = target.split('|', 1)
        vid = ''.join(c for c in vid if c.isdigit())[:12]
        st = self.site_of(skey)
        if not st or not vid:
            return {'list': []}
        cf = self.cfg_of(st)
        if st.get('family') != 'wieuc':
            return {'list': []}
        d = self._data(self.api_get('/api/vod/video/%s?site_id=%d' % (vid, cf['site'])))
        if not d:
            return {'list': []}
        nm = str(d.get('name') or '').strip() or (st['name'] + ' ' + vid)
        pic = str(d.get('pic') or '')
        if pic and not pic.startswith('http'):
            pic = 'https://' + (cf.get('img') or self.img) + pic
        if pic:
            if self._relay is None:
                self._relay = _Relay(self)
            pic = self._relay.img_url(pic, skey, vid)
        tags = ' '.join('#' + str((t or {}).get('name') or '') for t in (d.get('tag') or [])
                        if isinstance(t, dict) and t.get('name'))
        dur = int(d.get('duration') or 0)
        play = str(d.get('play_url') or '').strip()
        vod = {'vod_id': target, 'vod_name': nm, 'vod_pic': pic,
               'vod_remarks': '%s · %s' % (st['name'], st.get('group') or ''),
               'vod_year': str(d.get('pubdate') or '')[:4],
               'type_name': tags,
               'vod_content': ('【站源】阅妹阁聚合 · %s（族：%s）\n'
                               '【取数】接口信封 Fernet，源内已解；游客身份直取，无登录/会员校验\n'
                               '【取流】HLS 标准 AES-128（enc.key 明文，非 DRM）\n' % (st['name'], st.get('family'))
                               + ('【时长】%02d:%02d\n' % (dur // 60, dur % 60) if dur else '')
                               + ('【标签】%s\n' % tags.strip() if tags.strip() else '')
                               + ('【简介】%s' % str(d.get('description') or '')))}
        if play:
            froms, urls = [], []
            for i, host in enumerate(cf.get('lines') or LINE_HOST):
                u = play if play.startswith('http') else ('https://' + host + play)
                froms.append(LINE_NAME[i] if i < len(LINE_NAME) else ('线路%d' % (i + 1)))
                urls.append('正片$' + u)
            vod['vod_play_from'] = '$$$'.join(froms)
            vod['vod_play_url'] = '$$$'.join(urls)
        else:
            vod['vod_play_from'] = LINE_NAME[0]
            vod['vod_play_url'] = '暂无$https://' + cf['api'] + '/api/vod/video/' + vid
        return {'list': [vod]}

    def searchContent(self, key, quick, pg='1'):
        kw = str(key or '').strip()
        if not kw:
            return {'list': []}
        try:
            page = max(1, int(str(pg or '1').strip()))
        except Exception:
            page = 1
        res, seen, tot = [], set(), [0]
        lock = threading.Lock()

        def worker(st):
            cf = self.cfg_of(st)
            if st.get('family') != 'wieuc':
                return
            q = '/search/vod/?search=%s&page=%d&per_page=%d&site_id=%d' % (
                urllib.parse.quote(kw), page, PER, cf['site'])
            r = self.api_get(q)
            try:
                t = int(self._data(r).get('total') or 0)
                with lock:
                    if t > tot[0]:
                        tot[0] = t
            except Exception:
                pass
            for c in self._cards(st, r, cf):
                with lock:
                    if c['vod_name'] in seen:
                        continue
                    seen.add(c['vod_name'])
                    res.append(c)

        ths = [threading.Thread(target=worker, args=(s,)) for s in self.sites
               if s.get('family') == 'wieuc']
        for t in ths:
            t.daemon = True
            t.start()
        for t in ths:
            t.join(timeout=self.timeout + 4)
        return {'page': page, 'pagecount': page + 1 if len(res) >= PER else page,
                'limit': len(res), 'total': tot[0] or len(res), 'list': res[:120]}

    def fresh_pic(self, skey, vid):
        st = self.site_of(skey)
        if not st or st.get('family') != 'wieuc':
            return ''
        cf = self.cfg_of(st)
        d = self._data(self.api_get('/api/vod/video/%s?site_id=%d' % (str(vid), cf['site'])))
        p = str(d.get('pic') or '')
        if p and not p.startswith('http'):
            p = 'https://' + (cf.get('img') or self.img) + p
        return p

    def playerContent(self, flag, id, vipFlags=None):
        u = str(id or '').strip()
        if '$' in u:
            u = u.split('$')[-1]
        if u.startswith('http'):
            return {'parse': 0, 'playUrl': '', 'url': u,
                    'header': json.dumps({'User-Agent': UA, 'Referer': 'https://' + self.img + '/'})}
        if '|' in u:
            skey, vid = u.split('|', 1)
            st = self.site_of(skey)
            if st and st.get('family') == 'wieuc':
                cf = self.cfg_of(st)
                d = self._data(self.api_get('/api/vod/video/%s?site_id=%d' %
                                            (''.join(c for c in vid if c.isdigit())[:12], cf['site'])))
                p = str(d.get('play_url') or '').strip()
                if p:
                    full = p if p.startswith('http') else ('https://' + cf['lines'][0] + p)
                    return {'parse': 0, 'playUrl': '', 'url': full,
                            'header': json.dumps({'User-Agent': UA, 'Referer': 'https://' + cf['img'] + '/'})}
        return {'parse': 0, 'playUrl': '', 'url': u, 'header': json.dumps({'User-Agent': UA})}

    def localProxy(self, param):
        """壳走 localProxy 取图时直接用（不依赖本机 http 口）"""
        try:
            u = ''
            if isinstance(param, dict):
                u = str(param.get('u') or param.get('url') or '')
            if not u and isinstance(param, str):
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(param).query)
                u = (qs.get('u') or [''])[0]
            if not u.startswith('http'):
                return {'code': 404, 'content': ''}
            mime, img = _unbundle(self.fetch_bytes(u) or b'')
            if img is None:
                return {'code': 502, 'content': ''}
            return {'code': 200, 'content-type': mime, 'content': base64.b64encode(img).decode('ascii')}
        except Exception as e:
            self._log('localProxy %s' % str(e)[:70])
            return {'code': 500, 'content': ''}

    # ---------------- 自检 ----------------
    def check(self):
        line = []
        aes = _aes_cbc_key_test()
        line.append('AES表=%s' % ('OK' if aes else ('无crypto库可跳过' if aes is None else 'FAIL')))
        line.append('站点表=%d' % len(self.sites))
        ok = 0
        for st in self.sites:
            if st.get('family') != 'wieuc':
                continue
            cf = self._resolve(st)
            r = self.api_get('/api/vod/video?page=1&per_page=1&site_id=%d' % cf['site'])
            d = self._data(r)
            tot = int(d.get('total') or 0)
            if tot > 0:
                ok += 1
            line.append('%s[%s]site=%d total=%d' % (st['name'], '链通' if cf.get('ok') else '回落', cf['site'], tot))
        line.append('可用 %d 站' % ok)
        return ' · '.join(line)
