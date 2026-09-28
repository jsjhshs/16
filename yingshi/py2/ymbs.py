#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
淫民本色 (ymbs) — TVBox / 影视仓 / OK影视 (T4) Spider
站点: https://nks.ymbs2.fit/ymbs/
结构取证结论:
  站点形态   苹果CMS v10, 但路径被改写成 /cn/home/web/index.php/vod/...  且入口是 /ymbs/ 前缀
  分类       /cn/home/web/index.php/vod/type/id/{20..30}.html        11 个分类
  列表分页   /cn/home/web/index.php/vod/type/id/{id}/page/{n}.html    100 条/页, 实测 720 页
  详情/播放  /cn/home/web/index.php/vod/play/id/{id}/sid/1/nid/1.html  ← 无独立 detail 页(500)
  播放数据   var player_data={"encrypt":0, "url":"https:\\/\\/...index.m3u8", "from":"ckplayer"}
             encrypt=0 即明文 m3u8, 无需解密; 兜底仍保留 encrypt=1/2 解析
  搜索       POST /cn/home/web/index.php/vod/search.html  字段 wd
  卡片       <li class="thumb item"> + <span class="title"> + <img class="lazy fadein" src>
  标题/简介  <title>「在线播放{片名} 第N集 - 高清资源 - 淫民本色」 / meta description
  反爬       弱: Referer 必须带 /ymbs/ ; 无 CF, 无签名, 无 JS 加密
  多集       本站全部单集(sid 恒为 1, nid 恒为 1), 但仍按标准多集格式生成 vod_play_url
"""

import re
import json
import time
import threading
import urllib.parse
import urllib.request
import ssl

try:
    from base.spider import Spider as _BaseSpider
except Exception:
    class _BaseSpider(object):
        def __init__(self):
            pass

try:
    import requests
except Exception:
    requests = None


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

CATS = [
    ("20", "国产自拍"),
    ("21", "强奸乱伦"),
    ("22", "男同女同"),
    ("23", "重口味"),
    ("24", "日本AV"),
    ("25", "无码视频"),
    ("26", "有码视频"),
    ("27", "中文字幕"),
    ("28", "欧美极品"),
    ("29", "三级伦理"),
    ("30", "动漫精品"),
]


class Spider(_BaseSpider):
    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass
        self.sitedomain = "https://nks.ymbs2.fit"
        self.webpath = "/cn/home/web"
        self.web = self.sitedomain + self.webpath
        self.homepath = "/ymbs/"
        self.host = self.sitedomain
        self.timeout = 25
        self.sess = requests.Session() if requests is not None else None
        self._meta_cache = {}          # vod_id -> {name, pic, remark, year, area, actor, content}
        self._lock = threading.Lock()
        self._last_req = 0.0
        self._min_gap = 0.35
        self._ssl = ssl.create_default_context()
        self._ssl.check_hostname = False
        self._ssl.verify_mode = ssl.CERT_NONE

    # ------------------------------------------------------------------ 基础设施
    def _throttle(self):
        with self._lock:
            gap = time.time() - self._last_req
            if gap < self._min_gap:
                time.sleep(self._min_gap - gap)
            self._last_req = time.time()

    def _headers(self, referer=None):
        return {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": referer or (self.sitedomain + self.homepath),
        }

    def _get(self, url, referer=None, retries=2):
        for _ in range(retries + 1):
            self._throttle()
            try:
                if self.sess is not None:
                    r = self.sess.get(url, headers=self._headers(referer), timeout=self.timeout, verify=False)
                    r.encoding = r.apparent_encoding or "utf-8"
                    return r.text
                req = urllib.request.Request(url, headers=self._headers(referer))
                return urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl).read().decode("utf-8", "ignore")
            except Exception:
                time.sleep(0.6)
        return ""

    def _post(self, url, data, referer=None, retries=1):
        for _ in range(retries + 1):
            self._throttle()
            try:
                if self.sess is not None:
                    r = self.sess.post(url, data=data, headers=self._headers(referer), timeout=self.timeout, verify=False)
                    r.encoding = r.apparent_encoding or "utf-8"
                    return r.text
            except Exception:
                time.sleep(0.6)
        return ""

    # ------------------------------------------------------------------ TVBox 契约
    def getName(self):
        return "淫民本色"

    def getDependence(self):
        return []

    def init(self, extend=""):
        if isinstance(extend, str) and extend.strip().startswith("{"):
            try:
                extend = json.loads(extend)
            except Exception:
                extend = {}
        return self._get(self.web + "/")

    def isVideoFormat(self, url):
        return bool(re.search(r"\.m3u8|\.mp4", url or "", re.I))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        try:
            if self.sess is not None:
                self.sess.close()
        except Exception:
            pass

    def action(self, action):
        return {}

    # ------------------------------------------------------------------ 工具
    def fix_url(self, url):
        if not url:
            return ""
        url = url.strip().replace(" ", "%20")
        if url.startswith("\\/\\/"):
            url = "https://" + url.replace("\\/", "/")[2:]
        elif url.startswith("//"):
            url = "https:" + url
        if url.startswith("/"):
            return self.sitedomain + url
        if not url.startswith("http"):
            return self.web + "/" + url
        return url

    def _clean(self, s):
        s = re.sub(r"<[^>]+>", "", s or "")
        s = s.replace("&nbsp;", " ").replace("&amp;", "&")
        return re.sub(r"\s+", " ", s).strip()

    # ------------------------------------------------------------------ 列表解析
    def _parse_list(self, html):
        vods = []
        if not html:
            return vods
        seen = set()
        for block in re.findall(r'<li class="thumb item">(.*?)</li>', html, re.S):
            mid = re.search(r"vod/play/id/(\d+)/sid/(\d+)/nid/(\d+)", block)
            if not mid:
                continue
            vid, sid, nid = mid.group(1), mid.group(2), mid.group(3)
            if vid in seen:
                continue
            name = ""
            nm = re.search(r'<span class="title">(.*?)</span>', block, re.S)
            if nm:
                name = self._clean(nm.group(1))
            if not name:
                nm = re.search(r'<img[^>]+alt="([^"]*)"', block)
                name = self._clean(nm.group(1)) if nm else ""
            if not name:
                continue
            pic = ""
            pm = re.search(r'<img[^>]+src="(https?://[^"]+)"', block)
            if not pm:
                pm = re.search(r'data-original="([^"]+)"', block)
            if pm:
                pic = self.fix_url(pm.group(1))
            if pic and any(x in pic.lower() for x in ("loading", "blank", "logo", "icon", "avatar", "/images/")):
                pic = ""
            rm = re.search(r'<span class="added">(.*?)</span>', block, re.S)
            if rm:
                remark = self._clean(rm.group(1))
            # 播放页没有本片主视觉, 只有推荐缩略图, 故列表解析时顺带存封面
            if pic:
                self._meta_cache[vid] = {"pic": pic, "name": name, "remark": remark}
            seen.add(vid)
            vods.append({
                "vod_id": "%s|%s|%s" % (vid, sid, nid),
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": remark,
            })
        return vods

    # ------------------------------------------------------------------ 分类 / 首页
    def homeContent(self, filter):
        cats = [{"type_id": c, "type_name": n} for c, n in CATS]
        html = self._get(self.web + "/")
        if html:
            known = {c for c, _ in CATS}
            for cid, cname in re.findall(r'href="[^"]*vod/type/id/(\d+)\.html"[^>]*>\s*([^<]{1,20})', html):
                cname = self._clean(cname)
                if cid not in known and cname and not cname.startswith("更多"):
                    cats.append({"type_id": cid, "type_name": cname})
                    known.add(cid)
        return {"class": cats, "filters": {}}

    def homeVideoContent(self):
        html = self._get(self.web + "/index.php/vod/type/id/20.html")
        if not html:
            html = self._get(self.web + "/")
        return {"list": self._parse_list(html)[:100]}

    def categoryContent(self, tid, pg, filter=None, extend=None):
        tid = str(tid).strip()
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        if pg > 1:
            url = "%s/index.php/vod/type/id/%s/page/%d.html" % (self.web, tid, pg)
        else:
            url = "%s/index.php/vod/type/id/%s.html" % (self.web, tid)
        vods = self._parse_list(self._get(url))
        return {
            "list": vods,
            "page": pg,
            "pagecount": pg + 1 if len(vods) >= 60 else pg,
            "limit": len(vods),
            "total": len(vods),
        }

    def searchContent(self, key, quick, pg="1"):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        wd = str(key or "").strip()
        # GET 路由实测无结果(返回首页), 站点为 POST 表单
        url = self.web + "/index.php/vod/search/page/%d.html" % pg if pg > 1 else self.web + "/index.php/vod/search.html"
        html = self._post(url, {"wd": wd})
        if not html:
            html = self._post(self.web + "/index.php/vod/search.html", {"wd": wd})
        vods = self._parse_list(html)
        return {
            "list": vods,
            "page": pg,
            "pagecount": pg + 1 if len(vods) >= 30 else pg,
            "limit": len(vods),
            "total": len(vods),
        }

    # ------------------------------------------------------------------ 详情
    def _play_page(self, vid, sid, nid):
        return "%s/index.php/vod/play/id/%s/sid/%s/nid/%s.html" % (self.web, vid, sid, nid)

    def _unpack_id(self, ids):
        """vod_id 形如 'vid|sid|nid'；兼容 str / list / tuple / 纯数字。"""
        if isinstance(ids, (list, tuple)):
            ids = ",".join(str(i) for i in ids)
        raw = str(ids or "").strip()
        m = re.search(r"(\d{4,9})(?:\D+(\d{1,3}))?(?:\D+(\d{1,4}))?", raw)
        if not m:
            return None
        vid = m.group(1)
        sid = m.group(2) or "1"
        nid = m.group(3) or "1"
        return vid, sid, nid

    def detailContent(self, ids):
        unpacked = self._unpack_id(ids)
        if not unpacked:
            return {"list": []}
        vid, sid, nid = unpacked
        html = self._get(self._play_page(vid, sid, nid))
        if not html:
            return {"list": []}

        # 片名: <ul class="bread"><li>片名</li></ul>
        name = ""
        bm = re.search(r'<ul class="bread[^"]*"[^>]*>\s*<li>(.*?)</li>', html, re.S)
        if bm:
            name = self._clean(bm.group(1))
        if not name:
            tm = re.search(r"<title>(.*?)</title>", html, re.S)
            if tm:
                name = re.sub(r"^在线播放", "", tm.group(1).split(" - ")[0]).strip()

        content = ""
        dm = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', html)
        if dm:
            content = dm.group(1).strip()

        # 播放地址在 player_data; 详情阶段只取「能否解析」标记, 不缓存签名直链
        remark = ""
        em = re.search(r"第(\d+)集", tm.group(1)) if (tm := re.search(r"<title>(.*?)</title>", html, re.S)) else None
        if em:
            remark = "第%s集" % em.group(1)

        vod = {
            "vod_id": "%s|%s|%s" % (vid, sid, nid),
            "vod_name": name,
            "vod_pic": self._meta_cache.get(vid, {}).get("pic", ""),
            "vod_remarks": remark,
            "vod_content": content,
            "vod_year": "",
            "vod_area": "",
            "vod_actor": "",
            "vod_director": "",
            "vod_play_from": "ymbs",
            # 播放页为单集站, 仍给出唯一一集; 集名带集号, 避免同名塌陷
            "vod_play_url": "第%s集$%s|%s|%s" % (nid, vid, sid, nid),
        }
        return {"list": [vod]}

    # ------------------------------------------------------------------ 播放
    def _player_data(self, html):
        m = re.search(r"var\s+player_data\s*=\s*(\{.*?\})\s*</script>", html, re.S)
        if not m:
            m = re.search(r"player_data\s*=\s*(\{.*?\})", html, re.S)
        if not m:
            return None
        raw = m.group(1)
        try:
            return json.loads(raw)
        except Exception:
            pass
        # JSON 里的 \/ 转义, 手工容错解析
        try:
            return json.loads(raw.replace("\\/", "/"))
        except Exception:
            pass
        try:
            return json.loads(raw.replace("\\/", "/").replace("\t", "").replace("\n", "").replace("\r", ""))
        except Exception:
            um = re.search(r'"url"\s*:\s*"([^"]+)"', raw)
            em = re.search(r'"encrypt"\s*:\s*(\d+)', raw)
            if um:
                return {"url": um.group(1), "encrypt": int(em.group(1)) if em else 0}
            return None

    def _decode_url(self, data):
        if not data:
            return ""
        enc = data.get("encrypt")
        url = data.get("url") or ""
        url = url.replace("\\/", "/")
        try:
            if int(enc) == 0:
                return self.fix_url(url)
            if int(enc) == 1:
                # 苹果CMS encrypt=1: base64(url) xor 播放页 key
                import base64 as _b64
                raw = _b64.b64decode(url + "===").decode("utf-8", "ignore")
                return self.fix_url(self._xor(raw))
            if int(enc) == 2:
                import base64 as _b64
                raw = _b64.b64decode(url + "===").decode("utf-8", "ignore")
                return self.fix_url(raw)
        except Exception:
            return ""
        return self.fix_url(url)

    def _xor(self, text):
        """苹果CMS encrypt=1 的标准异或: 固定 5 轮 shift, key 从 playerconfig 取, 缺省用常见值。"""
        key = getattr(self, "_player_key", None)
        if key is None:
            js = self._get(self.web + "/static/js/playerconfig.js")
            km = re.search(r'(?:decode|key|encode)[^\n]{0,40}?["\']([A-Za-z0-9_\-]{4,32})["\']', js) if js else None
            key = km.group(1) if km else "mgU3c"
            self._player_key = key
        try:
            out = [chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(text)]
            return "".join(out)
        except Exception:
            return text

    def playerContent(self, flag, id, vipFlags=None):
        unpacked = self._unpack_id(id)
        if not unpacked:
            return {"parse": 0, "jx": 0, "playUrl": "", "url": "", "header": {}}
        vid, sid, nid = unpacked
        page = self._play_page(vid, sid, nid)
        html = self._get(page)
        data = self._player_data(html)
        url = self._decode_url(data) if data else ""
        if not url:
            pm = re.search(r'https?://[^\s\'"<>$#]+\.m3u8(?:\?[^\s"\'<>#]*)?', html)
            if pm:
                url = pm.group(0)
        if not url:
            # 兜底: 该站 player_data.url 恒为 m3u8, 若仍失败则抛嗅探
            return {"parse": 1, "jx": 0, "playUrl": "", "url": page, "header": self._headers(page)}
        return {
            "parse": 0,
            "jx": 0,
            "playUrl": "",
            "url": url,
            "header": {"User-Agent": UA, "Referer": page},
        }

    # ------------------------------------------------------------------ 本地代理
    def localProxy(self, param):
        if isinstance(param, str):
            try:
                param = json.loads(param)
            except Exception:
                param = {}
        if not isinstance(param, dict):
            param = {}
        url = param.get("url") or param.get("u") or ""
        if not url:
            return [404, "text/plain", b"Not Found", {}]
        url = self.fix_url(url)
        try:
            if self.sess is not None:
                r = self.sess.get(url, headers=self._headers(self.web + "/"), timeout=self.timeout,
                                 verify=False, stream=True)
                body, ct = r.content, r.headers.get("Content-Type", "application/octet-stream")
            else:
                req = urllib.request.Request(url, headers=self._headers(self.web + "/"))
                resp = urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl)
                body, ct = resp.read(), resp.headers.get("Content-Type", "application/octet-stream")
            if not body:
                return [404, "text/plain", b"Not Found", {}]
            return [200, ct.split(";")[0], body, {}]
        except Exception:
            return [404, "text/plain", b"Not Found", {}]


if __name__ == "__main__":
    import warnings
    from urllib.parse import urljoin
    warnings.filterwarnings("ignore")
    s = Spider()
    print("getName  :", s.getName())
    print("getDep   :", s.getDependence())
    s.init("")
    h = s.homeContent(False)
    print("class    :", len(h["class"]), h["class"])
    hv = s.homeVideoContent()
    print("homeVid  :", len(hv["list"]), "| sample:", hv["list"][:1])
    c = s.categoryContent("20", 1, False, {})
    print("cat p1   :", len(c["list"]), "pagecount=", c["pagecount"])
    print("  sample :", c["list"][:1])
    c2 = s.categoryContent("20", 2, False, {})
    print("cat p2   :", len(c2["list"]))
    vid = c["list"][0]["vod_id"]
    d = s.detailContent(vid)
    v = d["list"][0]
    print("detail   :", v["vod_name"], "| remark=", v["vod_remarks"], "| pic=", v["vod_pic"][:60])
    print("  content:", v["vod_content"][:70])
    print("  play   :", v["vod_play_from"], v["vod_play_url"])
    p = s.playerContent("ymbs", v["vod_play_url"].split("$")[-1], None)
    print("player   : parse=%s url=%s" % (p["parse"], p["url"]))
    m3 = requests.get(p["url"], headers={"User-Agent": UA}, timeout=20, verify=False).text
    print("m3u8     :", m3.splitlines()[0], "| lines=", len(m3.splitlines()))
    sub = re.search(r"^(?!#)\S+", m3, re.M)
    if sub:
        su = urljoin(p["url"], sub.group(0))
        st = requests.get(su, headers={"User-Agent": UA}, timeout=20, verify=False).text
        print("sublist  :", st.splitlines()[0], "| lines=", len(st.splitlines()))
        ts = re.search(r"^(?!#)\S+\.ts\S*", st, re.M)
        if ts:
            tu = urljoin(su, ts.group(0))
            tb = requests.get(tu, headers={"User-Agent": UA}, timeout=25, verify=False).content
            print("ts       :", len(tb), "bytes, head=", tb[:4].hex())
    for t in (vid, [vid], (vid,), vid.replace("|", ","), ""):
        print("ids态 %-24r -> %d 条" % (t, len(s.detailContent(t)["list"])))
    sr = s.searchContent("MBRAA", False, 1)
    print("search   :", len(sr["list"]), sr["list"][:1])
    lp = s.localProxy({"url": c["list"][0]["vod_pic"]}) if c["list"][0]["vod_pic"] else [0, "", b""]
    print("proxy    :", lp[0], lp[1], len(lp[2]) if len(lp) > 2 else 0)
    s.destroy()