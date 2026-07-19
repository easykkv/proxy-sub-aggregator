#!/usr/bin/env python3
"""
Proxy Subscription Aggregator & Converter
全自动节点订阅聚合、去重、存活测试与格式转换脚本
运行环境: GitHub Actions (Ubuntu)
"""

import os
import re
import sys
import json
import time
import base64
import socket
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

# ============================================================
# 配置区
# ============================================================

OUTPUT_DIR = Path("/tmp/proxy_output")
CLASH_FILE = OUTPUT_DIR / "clash.yaml"
SINGBOX_FILE = OUTPUT_DIR / "singbox.json"
RAW_FILE = OUTPUT_DIR / "raw.txt"

TEST_TIMEOUT = 5
TEST_CONCURRENCY = 20
ENABLE_ALIVE_TEST = os.getenv("ENABLE_ALIVE_TEST", "true").lower() == "true"
SUB_URLS_ENV = "SUB_URLS"

# ============================================================
# 数据结构
# ============================================================

@dataclass
class ProxyNode:
    raw_line: str
    protocol: str
    name: str
    server: str
    port: int
    fingerprint: str = field(default="")

# ============================================================
# 工具函数
# ============================================================

def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def fetch_url(url: str, timeout: int = 30) -> Optional[str]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            text = data.decode("utf-8", errors="ignore")
            stripped = text.strip()
            if re.match(r'^[A-Za-z0-9+/=]+$', stripped) and len(stripped) > 100:
                try:
                    decoded = base64.b64decode(stripped).decode("utf-8", errors="ignore")
                    if any(p in decoded for p in ["vmess://", "vless://", "ss://", "trojan://"]):
                        log(f"  [Base64解码成功] 长度: {len(decoded)}")
                        return decoded
                except Exception:
                    pass
            return text
    except urllib.error.URLError as e:
        log(f"  [抓取失败] {url} - {e}")
        return None
    except Exception as e:
        log(f"  [抓取异常] {url} - {e}")
        return None

def extract_nodes(text: str) -> List[ProxyNode]:
    nodes = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        protocol = None
        for p in ["vmess://", "vless://", "ss://", "ssr://", "trojan://", "hysteria2://"]:
            if line.startswith(p):
                protocol = p.replace("://", "")
                break
        if not protocol:
            continue
        node = parse_node(line, protocol)
        if node:
            nodes.append(node)
    return nodes

def parse_node(raw_line: str, protocol: str) -> Optional[ProxyNode]:
    name = "unnamed"
    server = ""
    port = 0
    try:
        if protocol == "vmess":
            b64 = raw_line[8:]
            info = json.loads(base64.b64decode(b64).decode("utf-8"))
            name = info.get("ps", info.get("add", "vmess"))
            server = info.get("add", "")
            port = int(info.get("port", 0))
            fp = f"{protocol}:{server}:{port}:{info.get('id','')[:8]}"
        elif protocol == "vless":
            m = re.match(r'vless://([^@]+)@([^:]+):(\d+)', raw_line)
            if m:
                server = m.group(2); port = int(m.group(3))
                name = raw_line.split("#")[-1] if "#" in raw_line else f"vless-{server}"
                fp = f"{protocol}:{server}:{port}:{m.group(1)[:8]}"
        elif protocol == "ss":
            if "@" in raw_line:
                rest = raw_line[5:]
                m = re.match(r'[^@]*@([^:]+):(\d+)', rest)
                if m:
                    server = m.group(1); port = int(m.group(2))
                    name = raw_line.split("#")[-1] if "#" in raw_line else f"ss-{server}"
                    fp = f"{protocol}:{server}:{port}"
        elif protocol == "trojan":
            m = re.match(r'trojan://[^@]*@([^:]+):(\d+)', raw_line)
            if m:
                server = m.group(1); port = int(m.group(2))
                name = raw_line.split("#")[-1] if "#" in raw_line else f"trojan-{server}"
                fp = f"{protocol}:{server}:{port}"
        elif protocol == "hysteria2":
            m = re.match(r'hysteria2://[^@]*@([^:]+):(\d+)', raw_line)
            if m:
                server = m.group(1); port = int(m.group(2))
                name = raw_line.split("#")[-1] if "#" in raw_line else f"hys2-{server}"
                fp = f"{protocol}:{server}:{port}"
        elif protocol == "ssr":
            b64 = raw_line[6:]
            decoded = base64.b64decode(b64).decode("utf-8")
            parts = decoded.split(":")
            if len(parts) >= 3:
                server = parts[0]; port = int(parts[1]); name = f"ssr-{server}"
                fp = f"{protocol}:{server}:{port}"
        else:
            return None
        if server and port > 0:
            return ProxyNode(raw_line=raw_line, protocol=protocol, name=name,
                             server=server, port=port, fingerprint=fp)
    except Exception as e:
        log(f"  [解析失败] {protocol}: {e}")
    return None

def deduplicate(nodes: List[ProxyNode]) -> List[ProxyNode]:
    seen: Set[str] = set()
    unique = []
    dup_count = 0
    for node in nodes:
        if node.fingerprint not in seen:
            seen.add(node.fingerprint)
            unique.append(node)
        else:
            dup_count += 1
    log(f"[去重] 原始: {len(nodes)}, 去重后: {len(unique)}, 重复: {dup_count}")
    return unique

def test_alive_tcp(node: ProxyNode) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TEST_TIMEOUT)
        result = sock.connect_ex((node.server, node.port))
        sock.close()
        return result == 0
    except Exception:
        return False

def filter_alive(nodes: List[ProxyNode]) -> List[ProxyNode]:
    if not ENABLE_ALIVE_TEST:
        log("[存活测试] 已禁用，跳过")
        return nodes
    log(f"[存活测试] 开始测试 {len(nodes)} 个节点 (超时={TEST_TIMEOUT}s, 并发={TEST_CONCURRENCY})")
    alive_nodes = []
    dead_count = 0
    with ThreadPoolExecutor(max_workers=TEST_CONCURRENCY) as executor:
        future_to_node = {executor.submit(test_alive_tcp, node): node for node in nodes}
        for future in as_completed(future_to_node, timeout=TEST_TIMEOUT + 2):
            node = future_to_node[future]
            try:
                is_alive = future.result(timeout=1)
                if is_alive:
                    alive_nodes.append(node)
                else:
                    dead_count += 1
                    if dead_count <= 5:
                        log(f"  [死节点] {node.name} ({node.server}:{node.port})")
            except (TimeoutError, Exception):
                dead_count += 1
    log(f"[存活测试] 存活: {len(alive_nodes)}, 死亡: {dead_count}")
    return alive_nodes

def generate_demo_nodes() -> List[ProxyNode]:
    """当所有抓取失败时，生成示例节点用于验证全流程"""
    log("[演示模式] 生成示例节点以验证完整流程...")
    demos = []
    vmess_info = {
        "v": "2", "ps": "\U0001f1ed\U0001f1f0 HK-\u793a\u4f8a\u8282\u70b901", "add": "hk.example.com",
        "port": "443", "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "aid": "0", "scy": "auto", "net": "ws", "type": "none",
        "host": "hk.example.com", "path": "/ws", "tls": "tls", "sni": "hk.example.com"
    }
    vmess_b64 = base64.b64encode(json.dumps(vmess_info, ensure_ascii=False).encode()).decode()
    demos.append(ProxyNode(
        raw_line=f"vmess://{vmess_b64}", protocol="vmess",
        name="\U0001f1ed\U0001f1f0 HK-\u793a\u4f8a\u8282\u70b901", server="hk.example.com", port=443,
        fingerprint=f"vmess:hk.example.com:443:a1b2c3d4"
    ))
    vmess_info2 = dict(vmess_info)
    vmess_info2["ps"] = "\U0001f1ef\U0001f1f5 JP-\u793a\u4f8a\u8282\u70b902"
    vmess_info2["add"] = "jp.example.com"
    vmess_b64_2 = base64.b64encode(json.dumps(vmess_info2, ensure_ascii=False).encode()).decode()
    demos.append(ProxyNode(
        raw_line=f"vmess://{vmess_b64_2}", protocol="vmess",
        name="\U0001f1ef\U0001f1f5 JP-\u793a\u4f8a\u8282\u70b902", server="jp.example.com", port=443,
        fingerprint=f"vmess:jp.example.com:443:a1b2c3d4"
    ))
    demos.append(ProxyNode(
        raw_line="trojan://password123@sg.example.com:443?sni=sg.example.com#\U0001f1ec\U0001f1e8 SG-\u793a\u4f8a\u8282\u70b903",
        protocol="trojan", name="\U0001f1ec\U0001f1e8 SG-\u793a\u4f8a\u8282\u70b903",
        server="sg.example.com", port=443,
        fingerprint="trojan:sg.example.com:443"
    ))
    # FIX: use str.encode() not bytes.encode()
    ss_method_pwd = base64.b64encode("aes-256-gcm:testpwd123==".encode()).decode()
    demos.append(ProxyNode(
        raw_line=f"ss://{ss_method_pwd}@us.example.com:8388#\U0001f1fa\U0001f1f8 US-\u793a\u4f8a\u8282\u70b904",
        protocol="ss", name="\U0001f1fa\U0001f1f8 US-\u793a\u4f8a\u8282\u70b904",
        server="us.example.com", port=8388,
        fingerprint="ss:us.example.com:8388"
    ))
    log(f"[演示模式] 生成了 {len(demos)} \u4e2a\u793a\u4f8a\u8282\u70b9\uff08\u8bf7\u66ff\u6362\u4e3a\u771f\u5b9e\u8ba2\u9605\u94fe\u63a5\uff09")
    return demos

# ============================================================
# 格式转换输出
# ============================================================

def generate_clash_yaml(nodes: List[ProxyNode]) -> str:
    proxies = []
    proxy_names = []
    for i, node in enumerate(nodes):
        name = f"\u8282\u70b9{i+1}_{node.name}"[:40]
        if node.protocol == "vmess":
            try:
                b64 = node.raw_line[8:]
                info = json.loads(base64.b64decode(b64).decode("utf-8"))
                proxy = {
                    "name": name, "type": "vmess", "server": node.server, "port": node.port,
                    "cidr": info.get("cidr", "auto"), "tls": info.get("tls", "").lower() == "true",
                    "sni": info.get("sni", ""), "network": info.get("net", "tcp"),
                    "uuid": info.get("id", ""), "alterId": int(info.get("aid", 0)), "cipher": "auto",
                }
                net = info.get("net", "tcp")
                if net == "ws":
                    proxy["ws-opts"] = {"path": info.get("path", "/"), "headers": {"Host": info.get("host", "")}}
                elif net == "grpc":
                    proxy["grpc-opts"] = {"grpc-service-name": info.get("path", "")}
                if proxy["tls"]:
                    proxy["tls-opts"] = {"allow-insecure": False}
                    if info.get("sni"):
                        proxy["tls-opts"]["server-name"] = info["sni"]
                proxies.append(proxy)
            except:
                pass
        elif node.protocol == "vless":
            m = re.match(r'vless://([^@]+)@([^:]+):(\d+)\??(.*)', node.raw_line)
            if m:
                uuid_val, _, _, params_str = m.groups()
                from urllib.parse import parse_qs, unquote
                params = {}
                if params_str:
                    for item in params_str.split("&"):
                        if "=" in item:
                            k, v = item.split("=", 1)
                            params[k] = unquote(v)
                proxy = {
                    "name": name, "type": "vless", "server": node.server, "port": node.port,
                    "uuid": uuid_val, "network": params.get("network", "tcp"),
                    "tls": params.get("security", "") in ("tls", "reality"), "udp": True,
                }
                if params.get("sni"): proxy["servername"] = params["sni"]
                if params.get("flow"): proxy["flow"] = params["flow"]
                proxies.append(proxy)
        elif node.protocol == "ss":
            rest = node.raw_line[5:]
            method_password = ""
            if "@" in rest:
                mp_part = rest.split("@")[0]
                try:
                    decoded = base64.b64decode(mp_part + "==").decode("utf-8")
                    if ":" in decoded: method_password = decoded
                except: method_password = mp_part
                if ":" in method_password:
                    method, password = method_password.split(":", 1)
                    proxies.append({"name": name, "type": "ss", "server": node.server,
                                    "port": node.port, "cipher": method, "password": password, "udp": True})
        elif node.protocol == "trojan":
            m = re.match(r'trojan://([^@]+)@([^:]+):(\+?\d+)', node.raw_line)
            if m:
                proxies.append({"name": name, "type": "trojan", "server": node.server,
                                "port": node.port, "password": m.group(1),
                                "sni": "", "skip-cert-verify": False, "udp": True})
        elif node.protocol == "hysteria2":
            m = re.match(r'hysteria2://([^@]+)@([^:]+):(\+?\d+)', node.raw_line)
            if m:
                proxies.append({"name": name, "type": "hysteria2", "server": node.server,
                                "port": node.port, "password": m.group(1)})
        proxy_names.append(name)

    config = {
        "mixed-port": 7890, "allow-lan": False, "mode": "rule", "log-level": "info",
        "unified-delay": True,
        "dns": {"enable": True, "listen": "0.0.0.0:53", "enhanced-mode": "fake-ip",
               "fake-ip-range": "198.18.0.1/16",
               "nameserver": ["https://dns.alidns.com/dns-query", "https://doh.pub/dns-query"]},
        "proxies": proxies,
        "proxy-groups": [
            {"name": "\u2728 \u81ea\u52a8\u9009\u62e9", "type": "url-test", "proxies": proxy_names,
             "url": "http://www.gstatic.com/generate_204", "interval": 300, "tolerance": 50},
            {"name": "\U0001f52f \u8282\u70b9\u9009\u62e9", "type": "select",
             "proxies": ["\u2728 \u81ea\u52a8\u9009\u62e9", "DIRECT"] + proxy_names},
        ],
        "rules": ["GEOIP,CN,DIRECT", "MATCH,\U0001f52f \u8282\u70b9\u9009\u62e9"],
    }
    import yaml
    return yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)

def generate_singbox_json(nodes: List[ProxyNode]) -> str:
    outbounds = []
    tag_index = 1
    for node in nodes:
        tag = f"proxy_{tag_index}"
        tag_index += 1
        try:
            if node.protocol == "vmess":
                b64 = node.raw_line[8:]
                info = json.loads(base64.b64decode(b64).decode("utf-8"))
                outbound = {"type": "vmess", "tag": tag, "server": node.server,
                            "server_port": node.port, "uuid": info.get("id", ""),
                            "security": "auto", "alter_id": int(info.get("aid", 0))}
                tls_map = {"tcp": "tcp", "ws": "ws", "grpc": "grpc"}
                transport_type = tls_map.get(info.get("net", "tcp"), "tcp")
                outbound["transport"] = {"type": transport_type}
                if transport_type == "ws":
                    outbound["transport"]["path"] = info.get("path", "/")
                    if info.get("host"): outbound["transport"]["headers"] = {"Host": info["host"]}
                if info.get("tls", "").lower() == "true":
                    outbound["tls"] = {"enabled": True, "server_name": info.get("sni", "")}
                outbounds.append(outbound)
            elif node.protocol == "vless":
                m = re.match(r'vless://([^@]+)@([^:]+):(\d+)\??(.*)', node.raw_line)
                if m:
                    uuid_val, _, _, params_str = m.groups()
                    from urllib.parse import parse_qs, unquote
                    params = {}
                    if params_str:
                        for item in params_str.split("&"):
                            if "=" in item:
                                k, v = item.split("=", 1)
                                params[k] = unquote(v)
                    outbound = {"type": "vless", "tag": tag, "server": node.server,
                               "server_port": node.port, "uuid": uuid_val}
                    network = params.get("network", "tcp")
                    outbound["transport"] = {"type": network}
                    security = params.get("security", "")
                    if security == "tls":
                        outbound["tls"] = {"enabled": True, "server_name": params.get("sni", "")}
                    elif security == "reality":
                        outbound["tls"] = {"enabled": True, "server_name": params.get("sni", ""),
                                           "reality": {"enabled": True, "public_key": params.get("pbk", ""),
                                                       "short_id": params.get("sid", "")}}
                    if params.get("flow"): outbound["flow"] = params["flow"]
                    outbounds.append(outbound)
            elif node.protocol == "ss":
                rest = node.raw_line[5:]
                if "@" in rest:
                    mp_part = rest.split("@")[0]
                    try:
                        decoded = base64.b64decode(mp_part + "==").decode("utf-8")
                        if ":" in decoded:
                            method, password = decoded.split(":", 1)
                            outbounds.append({"type": "shadowsocks", "tag": tag,
                                              "server": node.server, "server_port": node.port,
                                              "method": method, "password": password})
                    except: pass
            elif node.protocol == "trojan":
                m = re.match(r'trojan://([^@]+)@([^:]+):(\+?\d+)', node.raw_line)
                if m:
                    outbounds.append({"type": "trojan", "tag": tag, "server": node.server,
                                      "server_port": node.port, "password": m.group(1)})
            elif node.protocol == "hysteria2":
                m = re.match(r'hysteria2://([^@]+)@([^:]+):(\+?\d+)', node.raw_line)
                if m:
                    outbounds.append({"type": "hysteria2", "tag": tag, "server": node.server,
                                      "server_port": node.port, "password": m.group(1)})
        except Exception as e:
            log(f"  [sing-box\u8f6c\u6362\u8df3\u8fc7] {node.name}: {e}")

    tags = [o["tag"] for o in outbounds]
    config = {
        "log": {"level": "info", "timestamp": True},
        "dns": {"servers": [{"address": "223.5.5.5"}, {"address": "119.29.29.29"}]},
        "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "::", "listen_port": 2080}],
        "outbounds": [
            {"type": "urltest", "tag": "\u81ea\u52a8\u9009\u62e9", "outbounds": tags,
             "url": "http://www.gstatic.com/generate_204", "interval": "5m"},
            {"type": "select", "tag": "\u8282\u70b9\u9009\u62e9", "outbounds": ["\u81ea\u52a8\u9009\u62e9", "direct"] + tags},
        ] + outbounds,
        "route": {
            "rules": [{"ip_is_private": True, "outbound": "direct"},
                      {"geoip": {"code": "CN", "reverse": True}, "outbound": "\u81ea\u52a8\u9009\u62e9"}],
            "final": "\u81ea\u52a8\u9009\u62e9",
        },
    }
    return json.dumps(config, indent=2, ensure_ascii=False)

def generate_raw_text(nodes: List[ProxyNode]) -> str:
    return "\n".join(node.raw_line for node in nodes) + "\n"

# ============================================================
# 主流程
# ============================================================

def main():
    log("=" * 60)
    log("Proxy Subscription Aggregator Started")
    log("=" * 60)

    sub_urls_raw = os.getenv(SUB_URLS_ENV, "")
    if not sub_urls_raw:
        log("[\u8b66\u544a] \u672a\u8bbe\u7f6e SUB_URLS \u73af\u5883\u53d8\u91cf! \u4f7f\u7528\u6f14\u793a\u6a21\u5f0f...")
        nodes = generate_demo_nodes()
    else:
        sub_urls = re.split(r'[,\n\s]+', sub_urls_raw.strip())
        sub_urls = [u.strip() for u in sub_urls if u.strip()]
        log(f"[\u8ba2\u9605\u94fe\u63a5] \u5171 {len(sub_urls)} \u4e2a:")
        for i, url in enumerate(sub_urls, 1):
            try:
                domain = url.split("//")[1].split("/")[0] if "//" in url else url
                log(f"  {i}. {domain}")
            except:
                log(f"  {i}. ***")

        log("\n[\u6b65\u9aa41] \u5f00\u59cb\u6293\u53d6\u8ba2\u9605...")
        all_text = ""
        total_fetched = 0
        for url in sub_urls:
            log(f"  \u6b63\u5728\u6293\u53d6: ...")
            content = fetch_url(url)
            if content:
                all_text += content + "\n"
                total_fetched += 1

        if not all_text.strip():
            log("[\u8b66\u544a] \u6240\u6709\u8ba2\u9605\u94fe\u63a5\u5747\u6293\u53d6\u5931\u8d25! \u5207\u6362\u5230\u6f14\u793a\u6a21\u5f0f\u751f\u6210\u793a\u4f8a\u8282\u70b9...")
            log("> \u8bf7\u68c0\u67e5 SUB_URLS \u662f\u5426\u914d\u7f6e\u4e86\u6b63\u786e\u7684\u8ba2\u9605\u94fe\u63a5")
            nodes = generate_demo_nodes()
        else:
            log(f"[\u6293\u53d6\u5b8c\u6210] \u6210\u529f: {total_fetched}/{len(sub_urls)}, \u603b\u957f\u5ea6: {len(all_text)}")
            log("\n[\u6b65\u9aa42] \u63d0\u53d6\u8282\u70b9...")
            nodes = extract_nodes(all_text)
            log(f"[\u63d0\u53d6\u5b8c\u6210] \u5171\u53d1\u73b0 {len(nodes)} \u4e2a\u8282\u70b9")
            if not nodes:
                log("[\u8b66\u544a] \u672a\u63d0\u53d6\u5230\u4efb\u4f55\u6709\u6548\u8282\u70b9! \u5207\u6362\u5230\u6f14\u793a\u6a21\u5f0f...")
                nodes = generate_demo_nodes()

    if not nodes:
        log("[\u9519\u8bef] \u65e0\u53ef\u7528\u8282\u70b9!")
        sys.exit(1)

    log("\n[\u6b65\u9aa43] \u91cd\u5904\u7406...")
    nodes = deduplicate(nodes)

    log("\n[\u6b65\u9aa44] \u5b58\u6d3b\u6d4b\u8bd5...")
    nodes_before_test = len(nodes)
    nodes = filter_alive(nodes)
    if len(nodes) == 0 and nodes_before_test > 0:
        log("[\u63d0\u793a] \u6240\u6709\u8282\u70b9TCP\u63e1\u624b\u5931\u8d25(\u53ef\u80fd\u4e3a\u793a\u4f8a\u8282\u70b9/\u7f51\u7edc\u9650\u5236)\uff0c\u4fdd\u7559\u53bb\u91cd\u540e\u5168\u90e8\u8282\u70b9\u7ee7\u7eed\u751f\u6210\u914d\u7f6e\u6587\u4ef6...")
        if sub_urls_raw:
            sub_urls = re.split(r'[,\n\s]+', sub_urls_raw.strip())
            sub_urls = [u.strip() for u in sub_urls if u.strip()]
            all_text = ""
            for url in sub_urls:
                content = fetch_url(url)
                if content: all_text += content + "\n"
            if all_text.strip():
                nodes = deduplicate(extract_nodes(all_text))
            else:
                nodes = generate_demo_nodes()
        else:
            nodes = generate_demo_nodes()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log("\n[\u6b65\u9aa45] \u751f\u6210\u8f93\u51fa\u6587\u4ef6...")

    raw_content = generate_raw_text(nodes)
    RAW_FILE.write_text(raw_content, encoding="utf-8")
    log(f"  \u2713 raw.txt ({len(nodes)} \u4e2a\u8282\u70b9)")

    try:
        import yaml
        clash_content = generate_clash_yaml(nodes)
        CLASH_FILE.write_text(clash_content, encoding="utf-8")
        log(f"  \u2713 clash.yaml")
    except ImportError:
        log("  \u5b89\u88c5 PyYAML...")
        os.system("pip install pyyaml -q")
        import yaml
        clash_content = generate_clash_yaml(nodes)
        CLASH_FILE.write_text(clash_content, encoding="utf-8")
        log(f"  \u2713 clash.yaml (\u5df2\u5b89\u88c5 PyYAML)")

    singbox_content = generate_singbox_json(nodes)
    SINGBOX_FILE.write_text(singbox_content, encoding="utf-8")
    log(f"  \u2713 singbox.json")

    log("\n" + "=" * 60)
    log(f"\u5b8c\u6210! \u6700\u7ec8\u6709\u6548\u8282\u70b9\u6570: {len(nodes)}")
    log(f"\u8f93\u51fa\u76ee\u5f55: {OUTPUT_DIR}")
    log("=" * 60)

if __name__ == "__main__":
    main()
