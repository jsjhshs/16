# -*- coding: utf-8 -*-
# 影视壳py站源 - x18r.tv
# 适用于影视壳、TVBox等影视聚合软件

import requests
import re
import json
import time
from urllib.parse import urljoin, quote, unquote

# 配置
SITE_URL = "https://x18r.tv"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": SITE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 缓存
cache = {}
cache_time = {}

def get_html(url, params=None, timeout=10):
    """获取网页HTML"""
    try:
        full_url = url if not params else url + "?" + "&".join([f"{k}={v}" for k, v in params.items()])
        
        # 检查缓存
        if full_url in cache and time.time() - cache_time.get(full_url, 0) < 60:
            return cache[full_url]
        
        resp = requests.get(full_url, headers=HEADERS, timeout=timeout)
        resp.encoding = "utf-8"
        html = resp.text
        
        # 更新缓存
        cache[full_url] = html
        cache_time[full_url] = time.time()
        
        return html
    except Exception as e:
        return ""

def parse_vod_item(element):
    """解析单个视频项"""
    result = {}
    
    # 提取链接和标题
    link_match = re.search(r'href=["\']([^"\']+)["\']', element)
    title_match = re.search(r'title=["\']([^"\']+)["\']', element)
    img_match = re.search(r'src=["\']([^"\']+\.(?:jpg|jpeg|png|webp))["\']', element)
    
    if link_match:
        result["vod_url"] = urljoin(SITE_URL, link_match.group(1))
    
    if title_match:
        result["vod_name"] = title_match.group(1)
    
    if img_match:
        result["vod_pic"] = urljoin(SITE_URL, img_match.group(1))
    
    # 提取简介
    desc_match = re.search(r'(?:<[^>]*>)?([^<>]{10,200})</', element)
    if desc_match:
        result["vod_content"] = desc_match.group(1).strip()
    
    return result

def home_filter():
    """首页筛选数据"""
    filter_data = {
        "filters": {
            "1": [
                {"key": "type", "name": "分类"},
                {"key": "area", "name": "地区"},
                {"key": "year", "name": "年份"}
            ]
        },
        "filter_data": {
            "1": {
                "type": [{"n": "全部", "v": ""}],
                "area": [{"n": "全部", "v": ""}],
                "year": [{"n": "全部", "v": ""}]
            }
        }
    }
    return json.dumps(filter_data, ensure_ascii=False)

def home_content():
    """首页内容"""
    html = get_html(SITE_URL)
    
    # 解析首页视频列表
    pattern = r'<(?:a|div)[^>]*class=["\'](?:[^"\']*?(?:video|movie|item|list)[^"\']*)["\'][^>]*>.*?</(?:a|div)>'
    items = re.findall(pattern, html, re.DOTALL)
    
    vod_list = []
    for item in items[:20]:
        vod_info = parse_vod_item(item)
        if vod_info:
            vod_list.append(vod_info)
    
    # 如果没有解析到，尝试通用匹配
    if not vod_list:
        # 尝试匹配视频卡片
        pattern2 = r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(?:<[^>]*>)*([^<>]+)(?:<[^>]*>)*</a>'
        matches = re.findall(pattern2, html)
        for url, title in matches:
            if len(title) > 2 and not title.startswith("<"):
                vod_list.append({
                    "vod_name": title.strip(),
                    "vod_url": urljoin(SITE_URL, url),
                    "vod_pic": f"{SITE_URL}/static/images/default.jpg"
                })
    
    result = {
        "class": [{"type_id": "1", "type_name": "全部视频"}],
        "vod_list": vod_list[:20],
        "page": 1,
        "pagecount": 1,
        "limit": 20,
        "total": 20
    }
    
    return json.dumps(result, ensure_ascii=False)

def category_content(tid, pg=1, ext=None):
    """分类内容"""
    # 构建分类URL
    if pg <= 1:
        url = f"{SITE_URL}/vodtype/{tid}.html"
    else:
        url = f"{SITE_URL}/vodtype/{tid}-{pg}.html"
    
    html = get_html(url)
    
    # 提取视频列表
    pattern = r'<li[^>]*>.*?<a[^>]*href=["\']([^"\']+)["\'][^>]*>(?:<[^>]*>)*([^<>]+).*?</li>'
    matches = re.findall(pattern, html, re.DOTALL)
    
    vod_list = []
    for vod_url, vod_name in matches:
        vod_name = vod_name.strip()
        if len(vod_name) > 2 and not vod_name.startswith("<"):
            vod_list.append({
                "vod_name": vod_name,
                "vod_url": urljoin(SITE_URL, vod_url),
                "vod_pic": f"{SITE_URL}/static/images/default.jpg"
            })
    
    # 提取总页数
    page_count = 1
    total = len(vod_list)
    page_match = re.search(r'共(\d+)页', html)
    if page_match:
        page_count = int(page_match.group(1))
    
    result = {
        "class": [{"type_id": tid, "type_name": f"分类{tid}"}],
        "vod_list": vod_list,
        "page": pg,
        "pagecount": page_count,
        "limit": 20,
        "total": total if total > 0 else page_count * 20
    }
    
    return json.dumps(result, ensure_ascii=False)

def detail_content(ids):
    """详情内容"""
    url = urljoin(SITE_URL, ids)
    html = get_html(url)
    
    if not html:
        return json.dumps({"vod": {}}, ensure_ascii=False)
    
    vod = {}
    
    # 提取标题
    title_match = re.search(r'<h[1-6][^>]*>([^<>]+)</h[1-6]>', html)
    if title_match:
        vod["vod_name"] = title_match.group(1).strip()
    else:
        title_match = re.search(r'<title>([^<>]+)</title>', html)
        if title_match:
            vod["vod_name"] = title_match.group(1).split("-")[0].strip()
    
    # 提取封面
    img_match = re.search(r'<(?:img|video)[^>]*(?:src|poster)=["\']([^"\']+\.(?:jpg|jpeg|png|webp))["\']', html)
    if img_match:
        vod["vod_pic"] = urljoin(SITE_URL, img_match.group(1))
    else:
        vod["vod_pic"] = f"{SITE_URL}/static/images/default.jpg"
    
    # 提取简介
    content_match = re.search(r'<div[^>]*class=["\'](?:content|desc|summary|intro)[^"\']*["\'][^>]*>([\s\S]*?)</div>', html)
    if content_match:
        vod["vod_content"] = re.sub(r'<[^>]+>', '', content_match.group(1)).strip()
    
    # 提取播放地址
    play_urls = []
    
    # 尝试多种播放器模式
    play_patterns = [
        r'<iframe[^>]*src=["\']([^"\']+)["\']',
        r'<video[^>]*src=["\']([^"\']+)["\']',
        r'<source[^>]*src=["\']([^"\']+)["\']',
        r'data-src=["\']([^"\']+\.(?:m3u8|mp4|flv))["\']',
        r'href=["\']([^"\']+\.(?:m3u8|mp4|flv))["\']'
    ]
    
    for pattern in play_patterns:
        play_matches = re.findall(pattern, html)
        for play_url in play_matches:
            if play_url and not play_url.startswith('#'):
                full_url = urljoin(url, play_url)
                if full_url not in play_urls:
                    play_urls.append(full_url)
    
    if play_urls:
        # 使用第一个可用的播放地址
        vod["vod_play_from"] = "x18r"
        vod["vod_play_url"] = "播放地址$" + play_urls[0]
        # 如果有多个地址，添加到同一条
        if len(play_urls) > 1:
            for i, pu in enumerate(play_urls[1:], 1):
                vod["vod_play_url"] += f"#{i}$" + pu
    else:
        # 如果没有找到，尝试直接访问视频页面
        vod["vod_play_from"] = "x18r"
        vod["vod_play_url"] = "在线播放$" + url
    
    result = {
        "vod": vod,
        "vod_id": ids
    }
    
    return json.dumps(result, ensure_ascii=False)

def search_content(keyword, pg=1):
    """搜索内容"""
    encoded_keyword = quote(keyword)
    search_url = f"{SITE_URL}/vodsearch/{encoded_keyword}-{pg}.html"
    
    html = get_html(search_url)
    
    if not html:
        # 尝试其他搜索URL格式
        search_url = f"{SITE_URL}/search.php?key={encoded_keyword}&page={pg}"
        html = get_html(search_url)
    
    vod_list = []
    
    # 解析搜索结果
    pattern = r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>([^<>]+)</a>'
    matches = re.findall(pattern, html)
    
    for vod_url, vod_name in matches:
        vod_name = vod_name.strip()
        if len(vod_name) > 2 and not vod_name.startswith("<"):
            vod_list.append({
                "vod_name": vod_name,
                "vod_url": urljoin(SITE_URL, vod_url),
                "vod_pic": f"{SITE_URL}/static/images/default.jpg"
            })
    
    # 限制结果数量
    vod_list = vod_list[:20]
    
    result = {
        "vod_list": vod_list,
        "page": pg,
        "pagecount": 1,
        "limit": 20,
        "total": len(vod_list)
    }
    
    return json.dumps(result, ensure_ascii=False)

def main():
    """主入口 - 解析请求参数"""
    import sys
    
    if len(sys.argv) < 2:
        return
    
    req = sys.argv[1]
    
    try:
        params = json.loads(req)
    except:
        params = {"ac": req}
    
    ac = params.get("ac", "")
    
    if ac == "home":
        print(home_content())
    elif ac == "homelist":
        print(home_content())
    elif ac == "cate":
        tid = params.get("t", "1")
        pg = int(params.get("pg", "1"))
        ext = params.get("ext", {})
        print(category_content(tid, pg, ext))
    elif ac == "detail":
        ids = params.get("ids", "")
        print(detail_content(ids))
    elif ac == "search":
        keyword = params.get("wd", "")
        pg = int(params.get("pg", "1"))
        print(search_content(keyword, pg))
    elif ac == "filter":
        print(home_filter())
    else:
        print(json.dumps({"code": 0, "msg": "未知操作"}))

if __name__ == "__main__":
    main()