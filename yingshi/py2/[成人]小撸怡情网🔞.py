# -*- coding: utf-8 -*-
from base.spider import Spider
import re
try:
    from urllib.parse import quote, unquote, urljoin
    import urllib.request as _urlreq
except ImportError:
    from urllib import quote, unquote
    from urlparse import urljoin
    import urllib2 as _urlreq
try:
    import requests
    _REQ = True
except ImportError:
    _REQ = False
try:
    import json as _json
    _JSON = True
except ImportError:
    _JSON = False


class Spider(Spider):
    def getName(self):
        return '小撸怡情网'

    def init(self, extend=''):
        self.host = 'https://egwycx.xlyqw6001.top'
        self.base = self.host + '/xiaolu'
        self.ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
        self.headers = {'User-Agent': self.ua, 'Referer': self.base + '/'}
        self.sess = None
        if _REQ:
            try:
                self.sess = requests.Session()
                self.sess.headers.update(self.headers)
            except:
                self.sess = None

    def _decode(self, raw):
        for enc in ('utf-8', 'gbk', 'gb2312'):
            try:
                return raw.decode(enc)
            except:
                continue
        return raw.decode('utf-8', 'ignore')

    def _get(self, url):
        if self.sess is not None:
            try:
                r = self.sess.get(url, timeout=15)
                return self._decode(r.content)
            except:
                pass
        try:
            req = _urlreq.Request(url, headers=self.headers)
            raw = _urlreq.urlopen(req, timeout=15).read()
            return self._decode(raw)
        except:
            return ''

    def _abs(self, u):
        if not u:
            return ''
        u = u.strip()
        if u.startswith('http'):
            return u
        return urljoin(self.base + '/', u)

    def _clean(self, s):
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s or '')).strip()

    def _cats(self):
        html = self._get(self.base + '/')
        cats = []
        seen = set()
        for m in re.finditer(r'<a[^>]*href="/xiaolu/index\.php/vod/type/id/(\d+)\.html"[^>]*>(.*?)</a>', html, re.S):
            tid, name = m.group(1), self._clean(m.group(2))
            if tid not in seen and name and name != '更多':
                seen.add(tid)
                cats.append({'type_id': tid, 'type_name': name})
        return cats

    def homeContent(self, filter):
        return {'class': self._cats(), 'filters': {}}

    def _items(self, html):
        vods = []
        seen = set()
        for b in re.split(r'<li>', html)[1:]:
            try:
                hm = re.search(r'<a class="thumbnail"[^>]*href="([^"]+)"', b)
                if not hm:
                    continue
                vm = re.search(r'/vod/detail/id/(\d+)\.html', hm.group(1))
                vid = vm.group(1) if vm else hm.group(1)
                if vid in seen:
                    continue
                seen.add(vid)
                pm = re.search(r'data-original="([^"]+)"', b)
                if not pm:
                    pm = re.search(r'<img[^>]+src="([^"]+)"', b)
                pic = pm.group(1).strip() if pm else ''
                if 'loading.svg' in pic or pic.startswith('data:'):
                    pic = ''
                tm = re.search(r'<h5>\s*<a[^>]*>([^<]+)</a>', b)
                name = tm.group(1).strip() if tm else ''
                if not name:
                    continue
                rm = re.search(r'<p class="vodtitle">(.*?)</p>', b, re.S)
                remarks = self._clean(rm.group(1)) if rm else ''
                vods.append({'vod_id': vid, 'vod_name': name, 'vod_pic': pic, 'vod_remarks': remarks})
            except:
                continue
        return vods

    def homeVideoContent(self):
        cats = self._cats()
        if not cats:
            return {'list': []}
        html = self._get('{0}/index.php/vod/type/id/{1}.html'.format(self.base, cats[0]['type_id']))
        return {'list': self._items(html)}

    def categoryContent(self, tid, pg, filter, extend):
        tid = str(tid)
        try:
            pg = int(pg)
        except:
            pg = 1
        if pg < 1:
            pg = 1
        if pg <= 1:
            url = '{0}/index.php/vod/type/id/{1}.html'.format(self.base, tid)
        else:
            url = '{0}/index.php/vod/type/id/{1}/page/{2}.html'.format(self.base, tid, pg)
        html = self._get(url)
        pages = re.findall(r'/vod/type/id/{0}/page/(\d+)\.html'.format(tid), html)
        pagecount = max([int(p) for p in pages]) if pages else pg
        return {'list': self._items(html), 'page': pg, 'pagecount': pagecount, 'limit': 0, 'total': 0}

    def _norm_ids(self, ids):
        if isinstance(ids, (list, tuple)):
            ids = ','.join(str(i) for i in ids)
        return [i.strip() for i in str(ids).split(',') if i.strip()]

    def detailContent(self, ids):
        vods = []
        for vid in self._norm_ids(ids):
            try:
                html = self._get('{0}/index.php/vod/detail/id/{1}.html'.format(self.base, vid))
                if not html:
                    continue
                nm = re.search(r'片名：([^<]+)</li>', html)
                if not nm:
                    nm = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
                if not nm:
                    nm = re.search(r'<title>([^<\-_]+)', html)
                name = nm.group(1).strip() if nm else ''
                pm = re.search(r'<div class="detail-poster">.*?<img[^>]+src="([^"]+)"', html, re.S)
                if not pm:
                    pm = re.search(r'<meta property="og:image" content="([^"]+)"', html)
                pic = self._abs(pm.group(1)) if pm else ''
                tm = re.search(r'类型：([^<]+)</li>', html)
                um = re.search(r'更新：([^<]+)</li>', html)
                remarks = self._clean((tm.group(1) if tm else '') + ' ' + (um.group(1) if um else ''))
                segm = html.split('id="detail-content"', 1)
                seg = segm[1] if len(segm) > 1 else html
                pfrom, purl = [], []
                seen_pairs = set()
                for m in re.finditer(r'href="/xiaolu/index\.php/vod/play/id/{0}/sid/(\d+)/nid/(\d+)\.html"[^>]*>([^<]+)</a>'.format(vid), seg):
                    sid, nid, txt = m.group(1), m.group(2), self._clean(m.group(3))
                    key = (sid, nid, txt)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    pfrom.append(txt or '在线播放')
                    purl.append('正片${0}_{1}_{2}'.format(vid, sid, nid))
                if not pfrom:
                    pfrom, purl = ['在线播放'], ['正片${0}_1_1'.format(vid)]
                vods.append({
                    'vod_id': vid, 'vod_name': name, 'vod_pic': pic, 'vod_remarks': remarks,
                    'vod_actor': '', 'vod_director': '', 'vod_content': '',
                    'vod_play_from': '$$$'.join(pfrom), 'vod_play_url': '$$$'.join(purl),
                })
            except:
                continue
        return {'list': vods}

    def _play_url(self, html):
        if _JSON:
            try:
                m = re.search(r'var player_aaaa=(\{.*?\});?\s*</script>', html, re.S)
                if m:
                    data = _json.loads(m.group(1))
                    u = str(data.get('url', '')).strip()
                    if u:
                        return u
            except:
                pass
        m = re.search(r'var player_aaaa=.*?["\']url["\']\s*:\s*["\']([^"\']+)["\']', html, re.S)
        if m:
            return m.group(1).replace('\\/', '/')
        return ''

    def playerContent(self, flag, id, vipFlags):
        pid = str(id)
        vid, sid, nid = pid, '1', '1'
        m = re.match(r'^(\d+)_(\d+)_(\d+)$', pid)
        if m:
            vid, sid, nid = m.groups()
        play_url = '{0}/index.php/vod/play/id/{1}/sid/{2}/nid/{3}.html'.format(self.base, vid, sid, nid)
        html = self._get(play_url)
        url = self._play_url(html)
        if not url:
            for c in re.findall(r'<iframe[^>]+src="([^"]+)"', html, re.I) + [html]:
                mu = re.search(r'[?&]url=(https?%3A[^"\'\s&]+|https?://[^"\'\s&]+\.m3u8[^"\'\s&]*)', c)
                if mu:
                    url = unquote(mu.group(1))
                    break
                mu = re.search(r'(https?://[^"\'\s<>]+\.m3u8[^"\'\s<>]*)', c)
                if mu:
                    url = mu.group(1)
                    break
                mu = re.search(r'(https?://[^"\'\s<>]+\.mp4[^"\'\s<>]*)', c)
                if mu:
                    url = mu.group(1)
                    break
        if url.startswith('http'):
            low = url.lower()
            if '.m3u8' in low or '.mp4' in low or '.flv' in low:
                return {'parse': 0, 'url': url, 'header': {'User-Agent': self.ua, 'Referer': play_url}}
            return {'parse': 1, 'url': url}
        return {'parse': 1, 'url': play_url}

    def searchContent(self, key, quick, pg):
        try:
            pg = int(pg)
        except:
            pg = 1
        if pg < 1:
            pg = 1
        wd = quote(str(key))
        if pg <= 1:
            url = '{0}/index.php/vod/search.html?wd={1}'.format(self.base, wd)
        else:
            url = '{0}/index.php/vod/search/wd/{1}/page/{2}.html'.format(self.base, wd, pg)
        html = self._get(url)
        return {'list': self._items(html), 'page': pg}

    def localProxy(self, param):
        try:
            u = param.get('url', '') if isinstance(param, dict) else ''
            if not u:
                return [200, 'text/plain', b'']
            if self.sess is not None:
                data = self.sess.get(u, timeout=15).content
            else:
                data = _urlreq.urlopen(_urlreq.Request(u, headers=self.headers), timeout=15).read()
            mime = 'image/jpeg'
            if data[:4] == b'\x89PNG':
                mime = 'image/png'
            elif data[:4] == b'RIFF':
                mime = 'image/webp'
            elif data[:3] == b'GIF':
                mime = 'image/gif'
            return [200, mime, data]
        except:
            return [200, 'text/plain', b'']

    def isVideoFormat(self, url):
        return any(x in str(url).lower() for x in ['.m3u8', '.mp4', '.flv', '.ts'])

    def manualVideoCheck(self):
        return False

    def destroy(self):
        return True

    def action(self, *args, **kwargs):
        return None
