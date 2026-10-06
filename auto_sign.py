#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
File: auto_sign.py
Author: sjtt2 (原始作者) / 改造：国服直连 + 国际服分组代理
cron: 0 30 8 * * *
new Env('终末地签到');
Update: 2026/10/6

本版本修复的问题：
  国际服(zonai.skport.com) 的 CDN 对中国大陆出口 IP 返回 404 网页，
  原代码直接 .json() 导致报错 "Expecting value: line 1 column 1 (char 0)"
  —— 这是"拿到 HTML 而非 JSON"造成的，与 token 无关。

两点改造：
  1. 分组代理：国服强制直连，国际服可单独指定代理(SKPORT_PROXY)
  2. 错误可读：safe_json() 把天书报错翻译成可读原因+ 修复建议

环境变量：
  SKYLAND_TOKEN   国服 token，多个用 ; 或 , 分隔
  SKPORT_TOKEN    国际服 token，多个用 ; 或 , 分隔
  SKPORT_PROXY    国际服专用代理（需境外节点），如 http://192.168.1.100:7890
  SKYLAND_PROXY   国服专用代理，一般留空
  SKPORT_NOTIFY / SKYLAND_NOTIFY  设为 true 开启推送

诊断网络问题请运行： python3 diagnose_skport.py
"""
import hashlib
import hmac
import json
import os
import sys
import random
import time
import re
from urllib import parse
import requests

# ========== 动态加载青龙 notify 模块 ==========
# 遍历青龙常见的脚本根目录，将其加入 Python 的模块搜索路径，尽量适配不同版本的青龙面板
ql_script_paths = [
    '/ql/data/scripts',  # 新版青龙脚本路径
    '/ql/scripts',       # 老版青龙脚本路径
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # 当前脚本的上级目录
]

for p_path in ql_script_paths:
    if os.path.exists(p_path) and p_path not in sys.path:
        sys.path.append(p_path)

try:
    import notify
    HAS_NOTIFY = True
except ImportError:
    HAS_NOTIFY = False
    print("⚠️ 未找到青龙面板的 notify.py，本次执行将仅打印日志，不会触发推送。")


# ========== 防封号配置 ==========
# 请求间隔范围（秒）：随机等待以模拟人类行为
REQUEST_DELAY_MIN = 2
REQUEST_DELAY_MAX = 8
# 账号之间的额外等待时间
ACCOUNT_DELAY_MIN = 15
ACCOUNT_DELAY_MAX = 45
# 签到请求前的等待时间
SIGN_DELAY_MIN = 3
SIGN_DELAY_MAX = 10

# User-Agent 池（模拟不同设备）
USER_AGENTS = [
    'Skland/1.0.0 (com.skland.grass; Android; SDK_INT 33; Build/TQ3A.230901.001)',
    'Skland/1.0.0 (com.skland.grass; Android; SDK_INT 34; Build/UP1A.231005.004)',
    'Skland/1.0.0 (com.skland.grass; Android; SDK_INT 35; Build/AP2A.240405.002)',
    'Skland/1.0.1 (skport; Android; SDK_INT 33; Build/TQ3A.230901.001)',
    'Skland/1.0.1 (skport; Android; SDK_INT 34; Build/UP1A.231005.004)',
]

# 初始化变量
skyland_notify = os.getenv('SKPORT_NOTIFY') or os.getenv('SKYLAND_NOTIFY') or ''
run_message: str = ''
account_num: int = 1
sign_token = ''
PLATFORM = '3'
VNAME = '1.0.0'

# ========== 代理配置 ==========
# 背景：国际服(zonai.skport.com)的 CDN 对中国大陆出口 IP 返回 404 网页，
#      导致 requests 拿到 HTML 而非 JSON，报 "Expecting value: line 1 column 1"。
#      国服(zonai.skland.com) 在国内可直连，不应走代理。
#
# 优先级：专用环境变量 >脚本内硬编码 > 系统环境变量(http_proxy/https_proxy)
#注意：代理节点必须在境外/海外地区，境内节点无效！

# 专用环境变量（在青龙面板中添加，推荐方式）
ENV_PROXY_CN = "SKYLAND_PROXY"      # 国服专用，留空/不设置 = 直连
ENV_PROXY_GLOBAL = "SKPORT_PROXY"   # 国际服专用，填境外代理，如 http://192.168.1.100:7890

# 也可在下方直接硬编码代理地址（优先级高于环境变量），留空则用环境变量
MANUAL_PROXY_CN = ""                # 例: "http://192.168.1.100:7890"
MANUAL_PROXY_GLOBAL = ""            # 例: "http://192.168.1.100:7890"

# 绕过代理的写法：空字符串表示"显式禁用代理"，可覆盖系统环境变量
NO_PROXY = {'http': '', 'https': ''}


def get_proxies(server_key):
    """按服务器分组返回 proxies 参数

    实测结论（requests 库）：
      - 传 NO_PROXY（空字符串字典）可可靠绕过系统环境代理，直连
      - Session(trust_env=False) 只对Session 内新建请求生效，
        但显式 proxies 优先级更高，两者混用容易踩坑，故统一用 proxies 参数
    """
    if server_key == 'cn':
        raw = MANUAL_PROXY_CN or os.getenv(ENV_PROXY_CN, '')
    else:
        raw = MANUAL_PROXY_GLOBAL or os.getenv(ENV_PROXY_GLOBAL, '')

    raw = (raw or '').strip()
    # 未配置专用代理 → 国服直连；国际服回退到系统环境代理
    if not raw:
        if server_key == 'cn':
            return dict(NO_PROXY)   # 国服强制直连
        # 国际服：交给 requests 自行读取环境变量（返回 None 表示不干预）
        return None

    # 补全协议头
    if not raw.startswith(('http://', 'https://', 'socks5://', 'socks5h://')):
        raw = 'http://' + raw
    return {'http': raw, 'https': raw}


def describe_proxy_setup():
    """启动时打印代理配置，方便确认"""
    print("─" * 60)
    print("代理配置：")
    for key, label, env in (('cn', '国服', ENV_PROXY_CN),
                            ('global', '国际服', ENV_PROXY_GLOBAL)):
        p = get_proxies(key)
        if p is None:
            print(f"  {label:6s} → 回退系统环境代理(http_proxy/https_proxy)")
        elif p == NO_PROXY:
            print(f"  {label:6s} → 强制直连(不走任何代理)")
        else:
            print(f"  {label:6s} → 专用代理 {p['https']}  (来自 {env})")
    print("─" * 60)


# ========== 各服务器接口配置 ==========
SERVER_CONFIG = {
    "cn": {
        "name": "国服",
        "ENV_TOKEN": "SKYLAND_TOKEN",
        "MANUAL_TOKENS": "",  # 如果你想直接写token就在这里填，多个用 ; 分隔
        "APP_CODE": "4ca99fa6b56cc2ba",
        "GRANT_URL": "https://as.hypergryph.com/user/oauth2/v2/grant",
        "CRED_URL": "https://zonai.skland.com/api/v1/user/auth/generate_cred_by_code",
        "BIND_URL": "https://zonai.skland.com/api/v1/game/player/binding",
        "SIGN_URL": "https://zonai.skland.com/api/v1/game/endfield/attendance",
    },
    "global": {
        "name": "国际服",
        "ENV_TOKEN": "SKPORT_TOKEN",
        "MANUAL_TOKENS": "",  # 如果你想直接写token就在这里填，多个用 ; 分隔
        "APP_CODE": "6eb76d4e13aa36e6",
        "GRANT_URL": "https://as.gryphline.com/user/oauth2/v2/grant",
        "CRED_URL": "https://zonai.skport.com/web/v1/user/auth/generate_cred_by_code",
        "BIND_URL": "https://zonai.skport.com/api/v1/game/player/binding",
        "SIGN_URL": "https://zonai.skport.com/web/v1/game/endfield/attendance",
    }
}

# 请求头配置（优化，防封号）
BASE_HEADER = {
    'cred': '',
    'User-Agent': random.choice(USER_AGENTS),
    'Accept-Encoding': 'gzip',
    'Connection': 'keep-alive',
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}


def get_random_header():
    """获取随机化的请求头"""
    return {
        **BASE_HEADER,
        'User-Agent': random.choice(USER_AGENTS)
    }


def random_delay(min_sec=REQUEST_DELAY_MIN, max_sec=REQUEST_DELAY_MAX):
    """随机延迟，模拟人类操作"""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)

def send_notify(title, content):
    """
    消息推送，兼容青龙推送脚本
    """
    # 如果未设置环境变量，或者明确设置为 false，则不推送
    if not skyland_notify or skyland_notify.strip().lower() == 'false':
        return
        
    if HAS_NOTIFY:
        try:
            notify.send(title, content)
        except Exception as e:
            print(f"❌ 调用 notify.py 发送推送失败: {e}")
    else:
        print(f"\n【模拟推送】\n标题: {title}\n正文:\n{content}")

    
def generate_sign(token, path, body):
    """生成接口签名"""
    t = str(int(time.time()))
    token = token.encode('utf-8')
    sign_header = {
        "platform": PLATFORM,
        "timestamp": t,
        "dId": "",
        "vName": VNAME
    }
    sign_header_str = json.dumps(sign_header, separators=(',', ':'))
    sign_str = path + body + t + sign_header_str
    hmac_hex = hmac.new(
        token,
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    md5_sign = hashlib.md5(hmac_hex.encode('utf-8')).hexdigest()
    return md5_sign, sign_header

def safe_json(resp, url, proxy_hint=''):
    """安全解析 JSON —— 核心修复

    原代码直接 .json()，当服务器返回 HTML 错误页时会抛出
    "Expecting value: line 1 column 1 (char 0)" 这种天书报错。
    这里改为识别常见的非 JSON 响应并给出可读的原因。
    """
    try:
        return resp.json()
    except ValueError:
        ct = resp.headers.get('content-type', '')
        body = resp.text[:200].replace('\n', ' ')
        # CDN / 地域拦截：返回的是 HTML 页面（SKPORT 404 页）
        if 'html' in ct.lower() or body.lstrip().startswith('<!doctype') or body.lstrip().startswith('<html'):
            if resp.status_code == 404:
                raise Exception(
                    f'接口返回 404 网页而非 JSON（URL: {url}）。'
                    f'这是访问链路被 CDN 拦截，通常是所在网络无法直连国际服服务器。'
                    f'当前代理设置：{proxy_hint or "未使用代理(直连)"}。'
                    f'若已配代理，请确认该代理节点在境外/海外地区（境内节点无效）。'
                    f'可先运行 diagnose_skport.py 诊断。'
                )
            raise Exception(
                f'接口返回 HTML 错误页（HTTP {resp.status_code}, URL: {url}）。'
                f'通常是网络/代理问题，而非 token 问题。诊断脚本：diagnose_skport.py'
            )
        # 空响应体
        if not resp.text.strip():
            raise Exception(
                f'接口返回空响应（HTTP {resp.status_code}, URL: {url}）。'
                f'可能是代理配置错误或接口限流，请稍后重试。'
            )
        raise Exception(
            f'接口返回非 JSON 数据（HTTP {resp.status_code}, URL: {url}），'
            f'内容开头：{body[:120]}'
        )


def describe_proxies(proxies):
    """把 proxies 参数转成人能读的描述，用于错误提示"""
    if not proxies:
        return '未使用代理(由requests 读取系统环境变量)'
    v = proxies.get('https') or proxies.get('http') or ''
    if not v:
        return '强制直连(不走任何代理)'
    return v


def get_grant_code(token,cfg):
    """通过token获取grant code"""
    random_delay(1, 3)  # 请求前随机延迟
    try:
        t = json.loads(token)
        token = t['data']['content']
    except:
        pass
    server_key = 'cn' if cfg is SERVER_CONFIG['cn'] else 'global'
    proxies = get_proxies(server_key)
    try:
        r = requests.post(
            cfg["GRANT_URL"],
            json={'appCode': cfg["APP_CODE"], 'token': token, 'type': 0},
            headers=get_random_header(),
            proxies=proxies,
            timeout=15
        )
    except requests.exceptions.ProxyError as e:
        raise Exception(f'代理连接失败（{describe_proxies(proxies)}）：{str(e)[:100]}')
    resp = safe_json(r, cfg["GRANT_URL"], describe_proxies(proxies))
    if resp.get('status') != 0:
        raise Exception(f'获取grant code失败：{resp.get("msg", resp.get("message"))}')
    return resp['data']['code']

def get_cred(grant_code,cfg):
    """通过grant code获取cred和sign token"""
    global sign_token
    random_delay(1, 3)
    server_key = 'cn' if cfg is SERVER_CONFIG['cn'] else 'global'
    proxies = get_proxies(server_key)
    try:
        r = requests.post(
            cfg["CRED_URL"],
            json={'code': grant_code, 'kind': 1},
            headers=get_random_header(),
            proxies=proxies,
            timeout=15
        )
    except requests.exceptions.ProxyError as e:
        raise Exception(f'代理连接失败（{describe_proxies(proxies)}）：{str(e)[:100]}')
    resp = safe_json(r, cfg["CRED_URL"], describe_proxies(proxies))
    if resp['code'] != 0:
        raise Exception(f'获取cred失败：{resp["message"]}')
    sign_token = resp['data']['token']
    return resp['data']['cred']

def login(token,cfg):
    """森空岛登录逻辑"""
    grant = get_grant_code(token,cfg)
    cred = get_cred(grant,cfg)
    return cred

def get_endfield_roles(cred,cfg):
    """获取终末地绑定角色"""
    random_delay(2, 5)
    # 生成签名头
    parse_url = parse.urlparse(cfg["BIND_URL"])
    sign, sign_header = generate_sign(sign_token, parse_url.path, '')
    header = {
        'cred': cred,
        'platform': PLATFORM,
        'vName': VNAME,
        'timestamp': sign_header['timestamp'],
        'sign': sign,
        'Content-Type': 'application/json'
    }
    server_key = 'cn' if cfg is SERVER_CONFIG['cn'] else 'global'
    proxies = get_proxies(server_key)
    try:
        r = requests.get(cfg["BIND_URL"], headers=header, proxies=proxies, timeout=15)
    except requests.exceptions.ProxyError as e:
        raise Exception(f'代理连接失败（{describe_proxies(proxies)}）：{str(e)[:100]}')
    resp = safe_json(r, cfg["BIND_URL"], describe_proxies(proxies))
    if resp['code'] != 0:
        raise Exception(f'获取角色失败：{resp["message"]}')
    binding = None
    for app in resp['data']['list']:
        if app.get('appCode') == 'endfield' and app.get('bindingList'):
            binding = app['bindingList'][0]
            break
    if not binding:
        raise Exception('未绑定终末地角色')
    return binding

def do_daily_sign(cred,cfg):
    """执行签到"""
    global run_message, account_num
    try:
        roles = get_endfield_roles(cred,cfg)
        role = roles.get('defaultRole') or (roles.get('roles') and roles['roles'][0])
        role_str = f"3_{role['roleId']}_{role['serverId']}"
        # 签到前随机延迟，模拟人类行为
        random_delay(SIGN_DELAY_MIN, SIGN_DELAY_MAX)
        # 生成签到签名
        parse_url = parse.urlparse(cfg["SIGN_URL"])
        sign, sign_header = generate_sign(sign_token, parse_url.path, '')
        # 组装签到头
        header = {
            'cred': cred,
            'platform': PLATFORM,
            'vName': VNAME,
            'timestamp': sign_header['timestamp'],
            'sign': sign,
            'sk-game-role': role_str,
            'Content-Type': 'application/json'
        }
        # 发送签到请求（body为空）
        server_key = 'cn' if cfg is SERVER_CONFIG['cn'] else 'global'
        proxies = get_proxies(server_key)
        try:
            r = requests.post(cfg["SIGN_URL"], headers=header, json=None,
                              proxies=proxies, timeout=15)
        except requests.exceptions.ProxyError as e:
            raise Exception(f'代理连接失败（{describe_proxies(proxies)}）：{str(e)[:100]}')
        resp = safe_json(r, cfg["SIGN_URL"], describe_proxies(proxies))
        # 结果处理
        role_name = roles.get('defaultRole', {}).get('nickname', '未知角色')
        channel = roles.get('defaultRole', {}).get('serverName', '未知服务器')
        if resp['code'] == 0:
            # 获取奖励ID列表和奖励详情映射
            award_ids = resp['data'].get('awardIds', [])
            resource_map = resp['data'].get('resourceInfoMap', {})
            if award_ids and resource_map:
                # 遍历奖励ID，匹配详情并拼接奖励文本
                award_text = []
                for award in award_ids:
                    award_id = award.get('id')
                    if award_id and award_id in resource_map:
                        res = resource_map[award_id]
                        award_text.append(f'{res["name"]}x{res.get("count", 1)}')
                if award_text:
                    msg = f'[账号{account_num}] {role_name}({channel}) - 每日签到成功！获得：{"、".join(award_text)}'
                else:
                    msg = f'[账号{account_num}] {role_name}({channel}) - 每日签到成功（未识别到奖励信息）'
            else:
                msg = f'[账号{account_num}] {role_name}({channel}) - 每日签到成功（无奖励信息）'
        else:
            # 错误处理逻辑（保持不变）
            error_msg = resp.get("message", "未知错误")
            if "请勿重复签到" in error_msg or "Please do not sign in again!" in error_msg:
                msg = f'[账号{account_num}] {role_name}({channel}) - 今日已签到，请勿重复签到'
            else:
                msg = f'[账号{account_num}] {role_name}({channel}) - 签到失败：{error_msg}'

        run_message += msg + '\n'
        print(msg)
    except Exception as e:
        msg = f'[账号{account_num}] 角色处理失败：{str(e)}'
        run_message += msg + '\n'
        print(msg)
    finally:
        account_num += 1

def main():
    """脚本主入口"""
    global run_message, account_num
    print(f"终末地签到启动 {time.strftime('%Y-%m-%d %H:%M:%S')}")
    describe_proxy_setup()
    for cfg in SERVER_CONFIG.values():
        token_env = cfg.get("MANUAL_TOKENS") or os.getenv(cfg["ENV_TOKEN"], "")
        
        # 同时支持换行符、逗号和分号分割，现在可以一行一个token
        tokens = [t.strip() for t in re.split(r'[\n,;]+', token_env) if t.strip()]
        
        if not tokens:
            # 仅在控制台打印跳过日志，不将其加入 run_message 推送消息中
            print(f"[*] {cfg['name']} 未配置 {cfg['ENV_TOKEN']} 环境变量，自动跳过。")
            continue
            
        for idx, token in enumerate(tokens, 1):
            print(f"\n===== 处理{cfg['name']} 第{idx}个账号 =====")
            try:
                cred = login(token,cfg)
                do_daily_sign(cred,cfg)
            except Exception as e:
                err_msg = f"[{cfg['name']}] 账号{account_num} 异常：{str(e)}"
                run_message += err_msg + '\n'
                print(err_msg)
                account_num += 1
            # 账号之间增加随机延迟，避免高频请求被检测
            if idx < len(tokens):
                account_delay = random.uniform(ACCOUNT_DELAY_MIN, ACCOUNT_DELAY_MAX)
                print(f"等待 {account_delay:.1f} 秒后处理下一个账号...")
                time.sleep(account_delay)
                
    if run_message:
        send_notify('终末地签到结果', run_message)
    else:
        print("\n未执行任何签到，没有产生推送消息。")
        
    print(f"\n脚本执行结束 - {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\n签到结果汇总：\n{run_message if run_message else '无'}")

if __name__ == "__main__":
    main()
