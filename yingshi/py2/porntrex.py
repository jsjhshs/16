# -*- coding: utf-8 -*-
"""
PornTrex 单文件 TVBox 爬虫 (type 3 / 纯标准库 / 零第三方依赖)

站点: https://www.porntrex.com/   (KVS 内核, 公开 JSON 接口没开, 全程 HTML 解析)

特点:
  - 分类: 固定分区 + 站点分类表(90+) + 首页热搜标签, 分类/标签/搜索都带排序筛选
  - 播放: 详情页 flashvars 里掏多档 mp4 直链(480p / 720p / 1080p / 2160p), 不用登录不用会员
  - 播放时先把 get_file 的 302 解析成 CDN 真链再交给播放器, 兼容不吃跳转的播放器(失败自动回落原链)
  - 列表/详情/真链全带缓存, 带域名探活与自动换域名重试

ext 可选项(全可选, 写进源配置的 ext 里):
  {"host": "https://www.porntrex.com"}   钉死域名
  {"cats": "core"}                       core=只留固定分区 / cats=固定+分类 / 空或 all=固定+分类+热搜标签
  {"sort": "top-rated"}                  默认排序(只对分类/标签/搜索生效)

分类 tid 约定(便于自己加自定义分类):
  home / l / p / r / hd / gay / shemale
  cat:<分类slug>   比如 cat:anal、cat:teen、cat:4k-porn
  tag:<标签slug>   比如 tag:lesbian
  model:<模特slug> 比如 model:cherie-deville
  /随便什么路径     直接当站内路径用, 比如 /tags/lesbian
"""

import gzip
import html as _html
import json
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except Exception:
    BaseSpider = object


# ============================================================================
# 0. 常量
# ============================================================================

# 入口域名池(第一个是主域, 后面是能通的备用写法); 某个挂了会自动顺次换
MIRRORS = [
    "https://www.porntrex.com",
    "https://porntrex.com",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

TIMEOUT = 20
LIST_TTL = 300           # 列表页缓存 5 分钟
DETAIL_TTL = 1800        # 详情页缓存 30 分钟
CLASS_TTL = 3600         # 分类表缓存 1 小时
HOST_TTL = 600           # 域名探活结果缓存 10 分钟

SORTS = [
    ("latest", "最新"),
    ("top-rated", "最高评分"),
    ("most-popular", "最多观看"),
    ("longest", "最长"),
    ("most-commented", "最多评论"),
]

# 固定分区 (tid, 显示名, 站内路径, 分页方式)
FIXED = [
    ("home", u"🏠 首页推荐", "latest-updates", "page"),
    ("l", u"🆕 最新更新", "latest-updates", "page"),
    ("p", u"🔥 最多观看", "most-popular", "page"),
    ("r", u"⭐ 最高评分", "top-rated", "page"),
    ("hd", u"🎬 HD 专区", "hd/latest-updates", "page"),
    ("gay", u"🌈 Gay 专区", "gay", "from4"),
    ("shemale", u"💃 Shemale 专区", "shemale", "from4"),
]
FIXED_SORTABLE = set()   # 固定分区不吃排序

# 标签云里的水词, 顺手滤掉
_STOP_TAGS = set((
    "with", "the", "she", "gets", "and", "for", "her", "his", "you", "all", "not",
    "are", "was", "from", "that", "this", "but", "can", "its", "out", "off", "put",
    "get", "too", "see", "how", "who", "why", "yes", "has", "had", "him", "one",
    "two", "new", "hot", "best", "porn", "video", "videos", "sex", "fuck",
))

_CARD_SPLIT = '<div class="video-preview-screen video-item'

_RE_CARD_URL = re.compile(r'<a\s+href="([^"]*?/video/\d+/[^"]*)"', re.I)
_RE_CARD_TITLE = re.compile(r'<img[^>]+alt="([^"]*)"', re.I)
_RE_CARD_PIC = re.compile(r'<img[^>]+data-src="([^"]+)"', re.I)
_RE_CARD_PIC2 = re.compile(r'<img[^>]+src="([^"]+)"', re.I)
_RE_CARD_DUR = re.compile(r'class="durations">[\s\S]{0,160}?</i>\s*([0-9:]{3,12})')
_RE_CARD_QUAL = re.compile(r'class="quality">\s*([^<]{0,14})</span>')
_RE_CARD_VIEWS = re.compile(r'class="viewsthumb">\s*([^<]{0,32}?)\s*</div>')
_RE_CARD_INF = re.compile(r'<p class="inf">[\s\S]{0,300}?title="([^"]*)"')

_RE_FLASHVARS = re.compile(
    r"(video_url|video_url_text|video_alt_url\d?|video_alt_url\d?_text)\s*:\s*'([^']*)'")
_RE_VIDPATH = re.compile(r'/video/(\d+)/([^/?#"]*)')
_RE_PAGEMAX = re.compile(r'data-max="(\d+)"')

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


# ============================================================================
# 1. 小工具
# ============================================================================

def _s(v):
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            return v.decode('utf-8', 'replace')
        except Exception:
            return ""
    return str(v)


def _txt(v):
    """去标签 + 反转义 + 压空白"""
    s = re.sub(r'<[^>]+>', ' ', _s(v))
    s = _html.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()


def _ep_name(v):
    """选集名里不能带 $ / #, 顺手压一下"""
    s = _txt(v).replace('$', ' ').replace('#', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def _abs(u, base=""):
    """协议/路径补全"""
    u = _html.unescape(_s(u).strip())
    if not u:
        return ""
    if u.startswith('//'):
        return 'https:' + u
    if u.startswith('http://') or u.startswith('https://'):
        return u
    if u.startswith('/'):
        return (base or "").rstrip('/') + u
    return (base or "").rstrip('/') + '/' + u


def _vidpath(u):
    """从视频链接里抠出站内路径 video/12345/slug"""
    m = _RE_VIDPATH.search(_s(u))
    if not m:
        return ""
    return "video/%s/%s" % (m.group(1), m.group(2))


def _to_int(v, d=0):
    try:
        return int(_s(v).strip())
    except Exception:
        return d


def _quality_rank(label, url=""):
    """按清晰度排序用; 数字越大越清"""
    m = re.search(r'(\d{3,4})p', _s(label) + ' ' + _s(url), re.I)
    if m:
        return _to_int(m.group(1))
    if _s(url).lower().split('?')[0].endswith('.m3u8'):
        return 9999
    return 0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不让 urllib 自动跟 302, 好把 Location 抓出来"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ============================================================================
# 2. 页面解析
# ============================================================================

def main_block(text):
    """切出主列表区块(从第一个 list_videos 容器到分页栏)"""
    if not text:
        return ""
    i = text.find('id="list_videos')
    if i < 0:
        return ""
    j = text.find('<div class="pagination"', i)
    if j < 0:
        j = text.find('id="list_videos', i + 30)
    if j < 0:
        j = len(text)
    return text[i:j]


def parse_cards(text):
    """列表页 -> TVBox 条目列表(首页/分类/标签/模特/搜索通用)"""
    blk = main_block(text)
    if not blk:
        return []
    out, seen = [], set()
    parts = blk.split(_CARD_SPLIT)[1:]
    for p in parts:
        nxt = p.find(_CARD_SPLIT)
        if nxt >= 0:
            p = p[:nxt]
        m = _RE_CARD_URL.search(p)
        if not m:
            continue
        url = _html.unescape(m.group(1))
        vid = _vidpath(url)
        if not vid or vid in seen:
            continue
        seen.add(vid)

        title = ""
        t = _RE_CARD_TITLE.search(p)
        if t:
            title = _html.unescape(t.group(1)).strip()
        if not title:
            t = _RE_CARD_INF.search(p)
            if t:
                title = _html.unescape(t.group(1)).strip()
        if not title:
            title = vid.split('/')[-1].replace('-', ' ').strip()

        pic = ""
        for rx in (_RE_CARD_PIC, _RE_CARD_PIC2):
            mm = rx.search(p)
            if mm:
                pic = _abs(mm.group(1))
                if pic and 'lazyload' not in pic:
                    break

        dur = ""
        mm = _RE_CARD_DUR.search(p)
        if mm:
            dur = mm.group(1).strip()
        qual = ""
        mm = _RE_CARD_QUAL.search(p)
        if mm:
            qual = _txt(mm.group(1))
        views = ""
        mm = _RE_CARD_VIEWS.search(p)
        if mm:
            views = _txt(mm.group(1))

        remarks = " · ".join([x for x in (qual, dur, views) if x])

        out.append({
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": pic,
            "vod_remarks": remarks,
        })
    return out


def parse_pagecount(text, pg):
    """站点分页栏带 data-max(总页数), 直接用; 没抓到就按当前页凑"""
    m = _RE_PAGEMAX.search(text or "")
    if m:
        n = _to_int(m.group(1), pg)
        return n if n >= pg else pg
    if text and 'class="pagination"' in text:
        return pg + 1
    return pg


def parse_detail(text, host):
    """详情页 -> vod 字典"""
    name = ""
    m = re.search(r'<p class="title-video">(.*?)</p>', text, re.S)
    if m:
        name = _txt(m.group(1))
    if not name:
        m = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', text, re.I)
        if m:
            name = _html.unescape(m.group(1)).strip()

    pic = ""
    m = re.search(r'<meta\s+property="og:image"\s+content="([^"]*)"', text, re.I)
    if m:
        pic = _abs(m.group(1))
    if not pic:
        m = re.search(r"preview_url\s*:\s*'([^']+)'", text)
        if m:
            pic = _abs(m.group(1))

    # 徽章: 发布时间 / 播放量 / 时长
    badges = [_txt(x) for x in re.findall(r'<em class="badge">(.*?)</em>', text, re.S)]
    badges = [b for b in badges if b]
    added, views, dur = "", "", ""
    for b in badges:
        if not added and re.search(r'(ago|hour|day|minute|month|year|week|just\s*now)', b, re.I):
            added = b
        elif not dur and re.search(r'\d+\s*(min|sec|hour|h\b|m\b)', b, re.I) and not b.isdigit():
            dur = b
        elif not views and (re.match(r'^[\d.,]+$', b) or 'view' in b.lower()):
            views = b
    if not views and badges:
        for b in badges:
            if re.match(r'^\d+$', b):
                views = b
                break

    models = []
    for slug, nm in re.findall(
            r'<a\s+href="https?://[^"]*/models/([^/"]+)/">\s*<i class="fa fa-star"></i>\s*([^<]+)</a>',
            text):
        nm = _txt(nm)
        if nm:
            models.append(nm)

    cats = [_txt(x) for x in re.findall(
        r'<a class="js-cat"[^>]*href="[^"]*/categories/[^"]*"[^>]*>([^<]+)</a>', text)]
    cats = [c for c in cats if c]

    tags = [_txt(x) for x in re.findall(
        r'<a\s+href="https?://[^"]*/tags/[^"]*"[^>]*>([^<]+)</a>', text)]
    tags = [t for t in dict.fromkeys(tags) if t]

    desc = ""
    m = re.search(r'<div class="videodesc item">[\s\S]{0,200}?<div class="items-holder">([\s\S]*?)</div>\s*</div>',
                  text)
    if m:
        desc = _txt(m.group(1))

    # 下载面板里的体积(按清晰度对上)
    sizes = {}
    for lab, sz in re.findall(r'>(MP4\s*[^,<]*?),\s*([\d.]+\s*[KMGkmg]b)</a>', text):
        q = re.search(r'(\d{3,4}p)', lab, re.I)
        if q:
            sizes[q.group(1).lower()] = sz.strip()

    # 播放地址: flashvars 里的 video_url / video_alt_urlN
    pairs = _RE_FLASHVARS.findall(text)
    raw = {}
    texts = {}
    for k, v in pairs:
        v = _html.unescape(_s(v).strip())
        if k.endswith('_text'):
            texts[k[:-5]] = v
        else:
            if v:
                raw[k] = v
    eps = []
    for k, v in raw.items():
        lab = texts.get(k) or k
        if k == 'video_url':
            lab = texts.get('video_url') or '480p'
        u = _abs(v, host)
        if u.endswith('.mp4/'):
            u = u[:-1]
        rank = _quality_rank(lab, u)
        q = re.search(r'(\d{3,4}p)', lab, re.I)
        sz = sizes.get(q.group(1).lower(), '') if q else ''
        nm = _ep_name(lab if not sz else "%s · %s" % (lab, sz))
        if u:
            eps.append((rank, nm or ('线路%d' % (len(eps) + 1)), u))
    eps.sort(key=lambda x: -x[0])
    eps = [(n, u) for _, n, u in eps]

    remarks = " · ".join([x for x in (
        dur,
        views if (views and 'view' in views.lower()) else (views + " views" if views else ""),
        added) if x])
    content_bits = []
    if models:
        content_bits.append(u"模特: " + "、".join(models))
    if cats:
        content_bits.append(u"分类: " + "、".join(cats))
    if tags:
        content_bits.append(u"标签: " + "、".join(tags[:12]))
    if desc:
        content_bits.append(u"\n" + desc)

    vod = {
        "vod_id": "",
        "vod_name": name,
        "vod_pic": pic,
        "vod_year": "",
        "vod_area": "",
        "vod_remarks": remarks,
        "vod_actor": "、".join(models),
        "vod_director": "PornTrex",
        "vod_content": "\n".join(content_bits) or name,
        "type_name": (cats[0] if cats else ""),
        "vod_play_from": "PornTrex",
        "vod_play_url": "#".join(["%s$%s" % (n, u) for n, u in eps]),
    }
    return vod


def parse_categories(text):
    """分类总表 -> [(slug, name)]; 两种版式都认(带 <p class="text"> 的 A-Z 表 + 导航下拉里的 <div class="info">)"""
    out, seen = [], set()
    pats = (
        r'<a[^>]+href="https?://[^"]*/categories/([^/"]+)/"[^>]*>\s*<p class="text">([^<]+)</p>',
        r'<a[^>]+href="https?://[^"]*/categories/([^/"]+)/"[^>]*>[\s\S]{0,400}?'
        r'<div class="info">([^<]+)</div>',
    )
    for pat in pats:
        for slug, name in re.findall(pat, text):
            slug = slug.strip()
            nm = _txt(name)
            if slug and nm and slug not in seen:
                seen.add(slug)
                out.append((slug, nm))
    return out


def parse_hot_tags(text, limit=26):
    """首页热搜标签云 -> [(slug, name)], 按字号(热度)降序"""
    out, seen = [], set()
    for slug, style, nm in re.findall(
            r'<a href="https?://[^"]*/tags/([^/"]+)/"\s+style="([^"]*)">([^<]+)</a>', text):
        slug = slug.strip()
        nm = _txt(nm)
        if not slug or not nm or slug in seen:
            continue
        if nm.lower() in _STOP_TAGS:
            continue
        size = 0
        m = re.search(r'font-size:\s*(\d+)px', style)
        if m:
            size = _to_int(m.group(1))
        seen.add(slug)
        out.append((size, slug, nm))
    out.sort(key=lambda x: -x[0])
    return [(s, n) for _, s, n in out[:limit]]


# ============================================================================
# 3. 爬虫主体
# ============================================================================

class Spider(BaseSpider):

    def __init__(self):
        self.host = MIRRORS[0]
        self.ext = {}
        self._cands = list(MIRRORS)
        self._host_ok_at = 0.0
        self._lock = threading.RLock()
        self._cache = {}
        self._play_cache = {}

    # ---------------- TVBox 必需 ----------------

    def getName(self):
        return "PornTrex"

    def init(self, extend=""):
        self.ext = self._parse_ext(extend)
        host = _s(self.ext.get("host")).strip().rstrip('/')
        if host:
            if not host.startswith('http'):
                host = 'https://' + host
            self.host = host
            self._cands = [host] + [h for h in MIRRORS if h != host]
        else:
            self.host = MIRRORS[0]
            self._cands = list(MIRRORS)

    def isVideoFormat(self, url):
        u = _s(url).lower().split('?')[0]
        return any(u.endswith(x) for x in (".mp4", ".m3u8", ".mkv", ".flv", ".ts", ".avi"))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        with self._lock:
            self._cache.clear()
            self._play_cache.clear()

    # ---------------- 基础设施 ----------------

    def _parse_ext(self, extend):
        if isinstance(extend, dict):
            return dict(extend)
        s = _s(extend).strip()
        if not s:
            return {}
        for cand in (s, urllib.parse.unquote(s)):
            try:
                d = json.loads(cand)
                if isinstance(d, dict):
                    return d
            except Exception:
                continue
        # 允许 host=xxx&cats=core 这种写法
        if '=' in s:
            d = {}
            for kv in re.split(r'[&;\s]+', s):
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    d[k.strip()] = v.strip()
            if d:
                return d
        return {}

    def _headers(self, referer=None):
        return {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer or (self.host.rstrip('/') + "/"),
            "Connection": "close",
        }

    def _raw(self, url, referer=None, timeout=None):
        req = urllib.request.Request(url, headers=self._headers(referer))
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT, context=_CTX) as resp:
            raw = resp.read()
            enc = _s(resp.headers.get('Content-Encoding')).lower()
        if 'gzip' in enc:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        elif 'deflate' in enc:
            try:
                import zlib
                raw = zlib.decompress(raw, -15)
            except Exception:
                pass
        return raw.decode('utf-8', 'replace')

    def _http(self, url, referer=None, use_cache=True, ttl=LIST_TTL, timeout=None):
        now = time.time()
        if use_cache:
            with self._lock:
                hit = self._cache.get(url)
            if hit and hit[0] > now:
                return hit[1]
        text = self._raw(url, referer=referer, timeout=timeout)
        if text and use_cache:
            with self._lock:
                self._cache[url] = (now + ttl, text)
        return text

    def _fetch(self, url, referer=None, use_cache=True, ttl=LIST_TTL):
        """抓取: 失败重试一次; 还失败就换域名再来一遍"""
        last = None
        for attempt in range(2):
            try:
                return self._http(url, referer=referer, use_cache=use_cache, ttl=ttl)
            except Exception as e:
                last = e
                time.sleep(0.4)
        # 换域名
        for alt in self._cands:
            if url.startswith(self.host) and alt != self.host:
                u2 = alt.rstrip('/') + url[len(self.host.rstrip('/')):]
                try:
                    text = self._http(u2, referer=referer, ttl=ttl)
                    if text:
                        self.host = alt.rstrip('/')
                        with self._lock:
                            self._host_ok_at = time.time()
                        return text
                except Exception as e:
                    last = e
                    continue
        raise last if last else RuntimeError('fetch failed')

    def _ensure_host(self):
        now = time.time()
        if now - self._host_ok_at < HOST_TTL:
            return
        with self._lock:
            if now - self._host_ok_at < HOST_TTL:
                return
        for cand in self._cands:
            try:
                text = self._http(cand.rstrip('/') + "/", use_cache=False, timeout=12)
                if text and _CARD_SPLIT in text:
                    self.host = cand.rstrip('/')
                    self._host_ok_at = time.time()
                    return
            except Exception:
                continue
        self._host_ok_at = time.time()

    def _url(self, path, page=1, sort="", mode="page"):
        base = self.host.rstrip('/')
        path = _s(path).strip().strip('/')
        if mode == "from4":
            u = base + '/' + path + '/'
            if sort and sort != 'latest':
                u = base + '/' + path + '/' + sort + '/'
            if page and page > 1:
                u += '?from4=%d' % page
            return u
        u = base + '/' + path + '/'
        if sort and sort != 'latest':
            u += sort + '/'
        if page and page > 1:
            u += '%d/' % page
        return u

    # ---------------- 分类表 ----------------

    def _classes(self):
        mode = _s(self.ext.get("cats")).strip().lower() or "all"
        classes = [{"type_id": t, "type_name": n} for t, n, _, _ in FIXED]
        if mode == "core":
            return classes, set()
        try:
            self._ensure_host()
            home = self._http(self.host.rstrip('/') + "/", use_cache=True,
                              ttl=CLASS_TTL, timeout=TIMEOUT)
        except Exception:
            home = ""
        sortable = set()
        try:
            catpage = self._http(self.host.rstrip('/') + "/categories/", use_cache=True,
                                 ttl=CLASS_TTL, timeout=TIMEOUT)
            for slug, nm in parse_categories(catpage):
                classes.append({"type_id": "cat:" + slug, "type_name": u"📂 " + nm})
                sortable.add("cat:" + slug)
        except Exception:
            pass
        if mode in ("all", "", "full"):
            try:
                for slug, nm in parse_hot_tags(home):
                    classes.append({"type_id": "tag:" + slug, "type_name": u"🏷 " + nm})
                    sortable.add("tag:" + slug)
            except Exception:
                pass
        return classes, sortable

    def _filters(self, sortable):
        if not sortable:
            return {}
        vals = [{"n": n, "v": v} for v, n in SORTS]
        return {tid: [{"key": "sort", "name": u"排序", "value": vals}] for tid in sortable}

    # ---------------- TVBox 接口 ----------------

    def homeContent(self, filter):
        classes, sortable = [], set()
        videos = []
        try:
            self._ensure_host()
            classes, sortable = self._classes()
        except Exception:
            classes = [{"type_id": t, "type_name": n} for t, n, _, _ in FIXED]
        try:
            text = self._fetch(self._url("latest-updates", 1))
            videos = parse_cards(text)
        except Exception:
            videos = []
        return {"class": classes, "filters": self._filters(sortable), "list": videos}

    def homeVideoContent(self):
        try:
            self._ensure_host()
            return {"list": parse_cards(self._fetch(self._url("latest-updates", 1)))}
        except Exception:
            return {"list": []}

    def _resolve_tid(self, tid):
        """tid -> (站内路径, 分页方式)"""
        t = _s(tid).strip()
        for f_tid, _, path, mode in FIXED:
            if t == f_tid:
                return path, mode
        if t.startswith("cat:"):
            return "categories/" + t[4:].strip('/'), "page"
        if t.startswith("tag:"):
            return "tags/" + t[4:].strip('/'), "page"
        if t.startswith("model:"):
            return "models/" + t[6:].strip('/'), "page"
        if t.startswith("/"):
            return t.strip('/'), "page"
        if t and re.match(r'^[\w\-./]+$', t):
            return t.strip('/'), "page"
        return "latest-updates", "page"

    def categoryContent(self, tid, pg, filter, extend):
        page = max(1, _to_int(pg, 1))
        sort = ""
        if isinstance(extend, dict):
            sort = _s(extend.get("sort")).strip()
        if not sort:
            sort = _s(self.ext.get("sort")).strip()
        path, mode = self._resolve_tid(tid)
        result = {"list": [], "page": page, "pagecount": 1, "limit": 100, "total": 0}
        try:
            self._ensure_host()
            url = self._url(path, page, sort, mode)
            text = self._fetch(url)
            items = parse_cards(text)
            result["list"] = items
            result["pagecount"] = parse_pagecount(text, page)
            result["limit"] = len(items) or 100
            result["total"] = result["pagecount"] * (len(items) or 100) if result["pagecount"] > page else len(items)
        except Exception:
            pass
        return result

    def detailContent(self, ids):
        vid = ""
        if isinstance(ids, (list, tuple)):
            vid = _s(ids[0]) if ids else ""
        else:
            vid = _s(ids)
        vid = vid.strip()
        p = _vidpath(vid)
        if p:
            vid = p
        vid = vid.strip('/')
        # 去掉可能带的域名/协议
        vid = re.sub(r'^https?://[^/]+/', '', vid)
        if not vid:
            return {"list": []}

        try:
            self._ensure_host()
        except Exception:
            pass
        text = ""
        for cand in (vid, vid + '/'):
            try:
                text = self._fetch(self.host.rstrip('/') + '/' + cand,
                                   referer=self.host.rstrip('/') + "/", ttl=DETAIL_TTL)
                if 'var flashvars' in text or 'flashvars' in text:
                    break
            except Exception:
                continue
        if not text:
            return {"list": []}
        vod = parse_detail(text, self.host)
        vod["vod_id"] = vid
        if not vod.get("vod_name"):
            return {"list": []}
        return {"list": [vod]}

    def searchContent(self, key, quick, pg="1"):
        page = max(1, _to_int(pg, 1))
        key = _s(key).strip()
        result = {"list": [], "page": page, "pagecount": 1, "limit": 85, "total": 0}
        if not key:
            return result
        try:
            self._ensure_host()
            kw = urllib.parse.quote(key, safe='')
            url = self._url("search/" + kw, page)
            text = self._fetch(url)
            items = parse_cards(text)
            result["list"] = items
            result["pagecount"] = parse_pagecount(text, page) if items else 1
            result["limit"] = len(items) or 85
            result["total"] = result["pagecount"] * (len(items) or 85) if result["pagecount"] > page else len(items)
        except Exception:
            pass
        return result

    def searchContent2(self, key, quick, pg="1", tid="", extend=None):
        return self.searchContent(key, quick, pg)

    # ---------------- 播放 ----------------

    def _resolve_play(self, url):
        """把 get_file 的 302 解析成 CDN 真链, 拿不到就原样返回"""
        try:
            op = urllib.request.build_opener(_NoRedirect())
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": self.host.rstrip('/') + "/",
                "Range": "bytes=0-0",
            })
            try:
                r = op.open(req, timeout=TIMEOUT)
                final = getattr(r, 'url', '') or url
                try:
                    r.close()
                except Exception:
                    pass
                return final
            except urllib.error.HTTPError as e:
                loc = e.headers.get('Location') if e.headers else None
                if loc:
                    return _abs(loc)
                return url
        except Exception:
            return url

    def playerContent(self, flag, id, vipFlags):
        headers = {
            "User-Agent": UA,
            "Referer": self.host.rstrip('/') + "/",
        }
        raw = _abs(_s(id).strip(), self.host)
        if not raw.startswith('http'):
            return {"parse": 0, "playUrl": "", "url": "", "header": headers}
        if raw.endswith('.mp4/'):
            raw = raw[:-1]

        with self._lock:
            hit = self._play_cache.get(raw)
        if hit and hit[0] > time.time():
            return {"parse": 0, "playUrl": "", "url": hit[1], "header": headers}

        url = raw
        if '/get_file/' in raw:
            url = self._resolve_play(raw)
            if url and url != raw and 'expires=' in url:
                try:
                    exp = _to_int(re.search(r'expires=(\d+)', url).group(1), 0)
                    ttl = max(120, min(1800, exp - int(time.time()) - 300))
                except Exception:
                    ttl = 1800
            else:
                ttl = 300
            with self._lock:
                self._play_cache[raw] = (time.time() + ttl, url)

        return {"parse": 0, "playUrl": "", "url": url, "header": headers}


# ============================================================================
# 4. 命令行自测: python3 porntrex_tvbox.py
# ============================================================================

def _selftest():
    sp = Spider()
    sp.init("")
    ok = [0]
    bad = [0]

    def check(title, cond, extra=""):
        if cond:
            ok[0] += 1
            print("  [OK]   %s %s" % (title, extra))
        else:
            bad[0] += 1
            print("  [FAIL] %s %s" % (title, extra))

    print("== 1. 域名探活 ==")
    sp._ensure_host()
    check("host", sp.host.startswith('http'), sp.host)

    print("== 2. 首页/分类表 ==")
    home = sp.homeContent(True)
    check("class 数量", len(home["class"]) > 5, str(len(home["class"])))
    check("首页条目", len(home["list"]) > 10, "n=%d 例: %s" % (
        len(home["list"]), home["list"][0]["vod_name"][:40] if home["list"] else "-"))
    cat_ids = [c["type_id"] for c in home["class"] if c["type_id"].startswith("cat:")]
    check("分类表", len(cat_ids) > 30, "n=%d" % len(cat_ids))

    print("== 3. 分类翻页 ==")
    c1 = sp.categoryContent("l", "1", False, {})
    c2 = sp.categoryContent("l", "2", False, {})
    check("最新 p1", len(c1["list"]) > 10, "n=%d pagecount=%s" % (len(c1["list"]), c1["pagecount"]))
    check("翻页换内容", bool(c2["list"]) and c2["list"][0]["vod_id"] != c1["list"][0]["vod_id"])
    c3 = sp.categoryContent("cat:anal", "2", False, {"sort": "top-rated"})
    check("分类+排序+翻页", len(c3["list"]) > 10, "n=%d" % len(c3["list"]))
    c4 = sp.categoryContent("gay", "2", False, {})
    check("gay 分区 from4 翻页", len(c4["list"]) > 10, "n=%d" % len(c4["list"]))
    c5 = sp.categoryContent("cat:teen", "1", False, {})
    check("分类 teen", len(c5["list"]) > 10, "n=%d" % len(c5["list"]))

    print("== 4. 搜索 ==")
    s1 = sp.searchContent("lesbian", False, "1")
    check("搜索", len(s1["list"]) > 5, "n=%d" % len(s1["list"]))
    s2 = sp.searchContent("zzzqqqxyznope", False, "1")
    check("空结果不炸", s2["list"] == [], "n=%d" % len(s2["list"]))

    print("== 5. 详情 + 直链 ==")
    vid = s1["list"][0]["vod_id"] if s1["list"] else c1["list"][0]["vod_id"]
    d = sp.detailContent([vid])
    check("详情有数据", bool(d["list"]), vid)
    if d["list"]:
        vod = d["list"][0]
        eps = [e for e in vod["vod_play_url"].split("#") if e]
        check("标题", bool(vod["vod_name"]), vod["vod_name"][:50])
        check("封面", vod["vod_pic"].startswith("http"), vod["vod_pic"][:60])
        check("播放地址档数", len(eps) >= 1, "n=%d 例: %s" % (len(eps), eps[0][:70] if eps else "-"))
        if eps:
            u = eps[0].split("$", 1)[1]
            p = sp.playerContent("", u, "")
            check("播放地址", _s(p.get("url")).startswith("http"), _s(p.get("url"))[:90])
            # 真拉一小段, 确认不是空播放器
            try:
                req = urllib.request.Request(p["url"], headers={
                    "User-Agent": UA, "Referer": sp.host + "/", "Range": "bytes=0-65535"})
                with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
                    head = r.read(2048)
                    ct = _s(r.headers.get('Content-Type'))
                check("直链真能拉流", len(head) > 512, "type=%s bytes=%d" % (ct, len(head)))
            except Exception as e:
                check("直链真能拉流", False, str(e)[:80])

    print("\n结果: %d 过 / %d 挂" % (ok[0], bad[0]))
    return bad[0] == 0


if __name__ == "__main__":
    try:
        sys.exit(0 if _selftest() else 1)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
