#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国际服(SKPORT)连通性诊断脚本
用途：在青龙容器内运行，判断"Expecting value: line 1 column 1 (char 0)" 究竟是
      网络/地域问题，还是 token 问题。

用法：
    python diagnose_skport.py
"""
import os
import sys
import json

try:
    import requests
except ImportError:
    print("缺少 requests 库，请先安装：pip install requests")
    sys.exit(1)

# 国际服各环节接口
TARGETS = [
    ("AS授权服务器", "GET",  "https://as.gryphline.com/user/info/v1/basic",  None),
    ("TOKEN校验",   "GET",  "https://web-api.skport.com/cookie_store/account_token", None),
    ("CRED换取",    "POST", "https://zonai.skport.com/web/v1/user/auth/generate_cred_by_code",
     {"code": "RkFLRQ==", "kind": 1}),
    ("角色绑定",    "GET",  "https://zonai.skport.com/api/v1/game/player/binding", None),
    ("签到接口",    "GET",  "https://zonai.skport.com/web/v1/game/endfield/attendance", None),
]

# 国服对照组
CN_TARGETS = [
    ("国服CRED",    "POST", "https://zonai.skland.com/api/v1/user/auth/generate_cred_by_code",
     {"code": "RkFLRQ==", "kind": 1}),
    ("国服签到",    "GET",  "https://zonai.skland.com/web/v1/game/endfield/attendance", None),
]

HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://game.skport.com",
    "Referer": "https://game.skport.com/",
    "cred": "diagnostic_placeholder",
    "platform": "3",
    "vName": "1.0.0",
    "sk-language": "en",
}

# ========== 分组代理：与 auto_sign_fixed.py 保持一致 ==========
# 读取专用环境变量，未设置国际服代理则回退系统环境代理
_no_proxy = {"http": "", "https": ""}


def proxies_for(kind):
    """kind: 'cn' 直连 / 'global' 走 SKPORT_PROXY"""
    if kind == 'cn':
        return dict(_no_proxy)
    raw = (os.getenv('SKPORT_PROXY', '') or '').strip()
    if not raw:
        return None  # 交给 requests 读系统环境变量
    if not raw.startswith(('http://', 'https://', 'socks5://', 'socks5h://')):
        raw = 'http://' + raw
    return {"http": raw, "https": raw}


def probe(name, method, url, payload, proxies=None):
    try:
        kw = {"headers": HDRS, "timeout": 20}
        if method == "POST":
            kw["json"] = payload
        if proxies is not None:
            kw["proxies"] = proxies
        r = requests.request(method, url, **kw)
        ct = r.headers.get("content-type", "")
        is_json = "json" in ct.lower()
        head = r.text[:100].replace("\n", " ")
        status = "✅ JSON正常" if is_json else "❌ 非JSON(网页)"
        print(f"  [{status}] {name:10s} HTTP {r.status_code:3d}  ct={ct[:30]}")
        if not is_json:
            print(f"                内容: {head[:90]}")
        else:
            try:
                j = r.json()
                print(f"                响应: code={j.get('code', j.get('status'))} "
                      f"msg={j.get('message', j.get('msg'))}")
            except Exception:
                print(f"                内容: {head[:90]}")
        return is_json
    except Exception as e:
        print(f"  [❌ 连接异常] {name:10s} {type(e).__name__}: {str(e)[:70]}")
        return False


def main():
    print("=" * 66)
    print("国际服(SKPORT)连通性诊断")
    print("=" * 66)

    # 代理状态
    print("代理配置：")
    print("  国服→ 强制直连")
    p = proxies_for('global')
    if p is None:
        print("  国际服     → 回退系统环境代理 (http_proxy/https_proxy)")
    else:
        print(f"  国际服     → 专用代理 {p['https']}  (来自 SKPORT_PROXY)")
        print("               ⚠️ 请确认该节点在境外，境内节点会无效")
    print()

    print("【国际服接口】(若出现非JSON，说明网络被 CDN 拦截，非 token 问题)")
    gp = proxies_for('global')
    intl_ok = [probe(n, m, u, p, gp) for n, m, u, p in TARGETS]

    print()
    print("【国服接口对照】(强制直连，用于确认脚本本身和本机网络正常)")
    cp = proxies_for('cn')
    cn_ok = [probe(n, m, u, p, cp) for n, m, u, p in CN_TARGETS]

    print()
    print("=" * 66)
    zonai_ok = intl_ok[2] and intl_ok[3] and intl_ok[4]

    if not any(intl_ok) and any(cn_ok):
        print("结论：国服通、国际服全线不通 → 网络/地域访问问题，与 token 无关")
        print()
        print("解决办法：")
        if p is None:
            print("  当前未配置国际服专用代理。请在青龙面板添加环境变量：")
            print("SKPORT_PROXY = http://<你的境外代理IP>:端口")
        else:
            print(f"  已配置代理 {p['https']}，但仍然不通 →")
            print("  ⚠️ 该代理节点很可能在境内！国际服 CDN 只对境外节点返回正常数据")
            print("  请换一个境外/海外地区的代理节点再试")
        print()
        print("  验证代理是否有效：访问")
        print("    https://zonai.skport.com/web/v1/game/endfield/attendance")
        print("  返回 JSON = 节点可用；返回 SKPORT 404 页面 = 节点仍是境内的")
    elif not zonai_ok and intl_ok[0]:
        print("结论：授权服务器通，但 zonai.skport.com(游戏业务域名) 不通")
        print("     → 该域名 CDN 对当前出口 IP 返回 404 网页，必须走境外代理")
        if p is not None:
            print(f"当前代理：{p['https']} → 请确认该节点在境外")
    elif all(intl_ok):
        print("结论：国际服网络连通性正常")
        print("     → 若脚本仍报错，请检查 SKPORT_TOKEN 是否正确、是否已过期")
    else:
        print("结论：接口部分可达，请根据上方逐条结果排查")
    print("=" * 66)


if __name__ == "__main__":
    main()