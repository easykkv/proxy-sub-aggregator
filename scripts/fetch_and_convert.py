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
import ssl

# ============================================================
# 配置区
# ============================================================

# 输出目录
OUTPUT_DIR = Path("/tmp/proxy_output")
CLASH_FILE = OUTPUT_DIR / "clash.yaml"
SINGBOX_FILE = OUTPUT_DIR / "singbox.json"
RAW_FILE = OUTPUT_DIR / "raw.txt"

# 存活测试配置
TEST_TIMEOUT = 5          # 单节点测试超时(秒)
TEST_CONCURRENCY = 20     # 并发测试线程数
ENABLE_ALIVE_TEST = os.getenv("ENABLE_ALIVE_TEST", "true").lower() == "true"

# 订阅链接 (从环境变量读取，多个链接用逗号或换行分隔)
SUB_URLS_ENV = "SUB_URLS"

# ============================================================
# 数据结构
# ============================================================

@dataclass
class ProxyNode:
    """代理节点数据类"""
    raw_line: str           # 原始行内容
    protocol: str           # 协议类型: vmess/vless/ss/ssr/trojan/hysteria2/etc
    name: str               # 节点名称
    server: str             # 服务器地址
    port: int               # 端口
    # 用于去重的指纹 (server+port+protocol 的关键参数)
    fingerprint: str = field(default="")


# ============================================================
# 工具函数
# ============================================================

def log(msg: str):
    """带时间戳的日志"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def fetch_url(url: str, timeout: int = 30) -> Optional[str]:
    """
    抓取订阅链接内容
    支持 base64 编码和纯文本两种格式
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            # 尝试解码
            text = data.decode("utf-8", errors="ignore")
            # 检测是否为 base64 编码
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
    """
    从文本中提取所有代理节点
    支持的协议前缀: vmess:// vless:// ss:// ssr:// trojan:// hysteria2://
    """
    nodes = []
    lines = text.split("\n")
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # 识别协议类型
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
    """
    解析单行节点，提取关键字段用于去重和存活测试
    注意: 这里只做轻量解析，不追求完整字段提取
    """
    name = "unnamed"
    server = ""
    port = 0
    
    try:
        if protocol == "vmess":
            # vmess://base64json
            b64 = raw_line[8:]
            json_str = base64.b64decode(b64).decode("utf-8")
            info = json.loads(json_str)
            name = info.get("ps", info.get("add", "vmess"))
            server = info.get("add", "")
            port = int(info.get("port", 0))
            fp = f"{protocol}:{server}:{port}:{info.get('id','')[:8]}"
            
        elif protocol == "vless":
            # vless://uuid@server:port?params#name
            m = re.match(r'vless://([^@]+)@([^:]+):(\d+)', raw_line)
            if m:
                server = m.group(2)
                port = int(m.group(3))
                name = raw_line.split("#")[-1] if "#" in raw_line else f"vless-{server}"
                fp = f"{protocol}:{server}:{port}:{m.group(1)[:8]}"
                
        elif protocol == "ss":
            # ss://base64@server:port#name 或 ss://method:password@server:port#name
            if "@" in raw_line:
                rest = raw_line[5:]
                m = re.match(r'[^@]*@([^:]+):(\d+)', rest)
                if m:
                    server = m.group(1)
                    port = int(m.group(2))
                    name = raw_line.split("#")[-1] if "#" in raw_line else f"ss-{server}"
                    fp = f"{protocol}:{server}:{port}"
                    
        elif protocol == "trojan":
            # trojan://password@server:port?params#name
            m = re.match(r'trojan://[^@]*@([^:]+):(\d+)', raw_line)
            if m:
                server = m.group(1)
                port = int(m.group(2))
                name = raw_line.split("#")[-1] if "#" in raw_line else f"trojan-{server}"
                fp = f"{protocol}:{server}:{port}"
                
        elif protocol == "hysteria2":
            # hysteria2://password@server:port?params#name
            m = re.match(r'hysteria2://[^@]*@([^:]+):(\d+)', raw_line)
            if m:
                server = m.group(1)
                port = int(m.group(2))
                name = raw_line.split("#")[-1] if "#" in raw_line else f"hys2-{server}"
                fp = f"{protocol}:{server}:{port}"
                
        elif protocol == "ssr":
            # ssr://base64
            b64 = raw_line[6:]
            decoded = base64.b64decode(b64).decode("utf-8")
            parts = decoded.split(":")
            if len(parts) >= 3:
                server = parts[0]
                port = int(parts[1])
                name = f"ssr-{server}"
                fp = f"{protocol}:{server}:{port}"
        
        else:
            return None
            
        if server and port > 0:
            return ProxyNode(
                raw_line=raw_line,
                protocol=protocol,
                name=name,
                server=server,
                port=port,
                fingerprint=fp
            )
            
    except Exception as e:
        log(f"  [解析失败] {protocol}: {e}")
    
    return None


def deduplicate(nodes: List[ProxyNode]) -> List[ProxyNode]:
    """
    去重: 基于指纹去除完全相同的节点
    保留先出现的
    """
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
    """
    轻量级 TCP 握手存活测试
    仅测试 TCP 连接是否能建立，不做完整协议握手
    这是最轻量的检测方式，适合 GitHub Actions 环境
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TEST_TIMEOUT)
        result = sock.connect_ex((node.server, node.port))
        sock.close()
        return result == 0
    except socket.gaierror:
        # DNS 解析失败
        return False
    except socket.timeout:
        return False
    except Exception:
        return False


def filter_alive(nodes: List[ProxyNode]) -> List[ProxyNode]:
    """
    并发存活测试，过滤死节点
    """
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
                    if dead_count <= 5:  # 只打印前5个死节点
                        log(f"  [死节点] {node.name} ({node.server}:{node.port})")
            except TimeoutError:
                dead_count += 1
            except Exception:
                dead_count += 1
    
    log(f"[存活测试] 存活: {len(alive_nodes)}, 死亡: {dead_count}")
    return alive_nodes


# ============================================================
# 格式转换输出
# ============================================================

def generate_clash_yaml(nodes: List[ProxyNode]) -> str:
    """
    生成 Clash Meta 配置文件 (YAML 格式)
    兼容 Clash Verge / Clash Meta / mihomo
    """
    proxies = []
    proxy_names = []
    
    for i, node in enumerate(nodes):
        name = f"节点{i+1}_{node.name}"[:40]  # 截断长名称
        
        if node.protocol == "vmess":
            try:
                b64 = node.raw_line[8:]
                info = json.loads(base64.b64decode(b64).decode("utf-8"))
                proxy = {
                    "name": name,
                    "type": "vmess",
                    "server": node.server,
                    "port": node.port,
                    "cidr": info.get("cidr", "auto"),
                    "tls": info.get("tls", "").lower() == "true",
                    "sni": info.get("sni", ""),
                    "network": info.get("net", "tcp"),
                    "uuid": info.get("id", ""),
                    "alterId": int(info.get("aid", 0)),
                    "cipher": "auto",
                }
                # ws/tls 传输参数
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
                    "name": name,
                    "type": "vless",
                    "server": node.server,
                    "port": node.port,
                    "uuid": uuid_val,
                    "network": params.get("network", "tcp"),
                    "tls": params.get("security", "") in ("tls", "reality"),
                    "udp": True,
                }
                if params.get("sni"):
                    proxy["servername"] = params["sni"]
                if params.get("flow"):
                    proxy["flow"] = params["flow"]
                proxies.append(proxy)
                
        elif node.protocol == "ss":
            # 解析 ss 链接
            rest = node.raw_line[5:]
            method_password = ""
            if "@" in rest:
                mp_part = rest.split("@")[0]
                # 可能是 base64 编码的 method:password
                try:
                    decoded = base64.b64decode(mp_part + "==").decode("utf-8")
                    if ":" in decoded:
                        method_password = decoded
                except:
                    method_password = mp_part
                if ":" in method_password:
                    method, password = method_password.split(":", 1)
                    proxy = {
                        "name": name,
                        "type": "ss",
                        "server": node.server,
                        "port": node.port,
                        "cipher": method,
                        "password": password,
                        "udp": True,
                    }
                    proxies.append(proxy)
                    
        elif node.protocol == "trojan":
            m = re.match(r'trojan://([^@]+)@([^:]+):(\+?\d+)', node.raw_line)
            if m:
                password = m.group(1)
                proxy = {
                    "name": name,
                    "type": "trojan",
                    "server": node.server,
                    "port": node.port,
                    "password": password,
                    "sni": "",
                    "skip-cert-verify": False,
                    "udp": True,
                }
                proxies.append(proxy)
                
        elif node.protocol == "hysteria2":
            m = re.match(r'hysteria2://([^@]+)@([^:]+):(\+?\d+)', node.raw_line)
            if m:
                password = m.group(1)
                proxy = {
                    "name": name,
                    "type": "hysteria2",
                    "server": node.server,
                    "port": node.port,
                    "password": password,
                }
                proxies.append(proxy)
        
        proxy_names.append(name)
    
    # 构建 Clash 完整配置
    config = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "unified-delay": True,
        "dns": {
            "enable": True,
            "listen": "0.0.0.0:53",
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "nameserver": [
                "https://dns.alidns.com/dns-query",
                "https://doh.pub/dns-query",
            ],
        },
        "proxies": proxies,
        "proxy-groups": [
            {
                "name": "✨ 自动选择",
                "type": "url-test",
                "proxies": proxy_names,
                "url": "http://www.gstatic.com/generate_204",
                "interval": 300,
                "tolerance": 50,
            },
            {
                "name": "🔯 节点选择",
                "type": "select",
                "proxies": ["✨ 自动选择", "DIRECT"] + proxy_names,
            },
        ],
        "rules": [
            "GEOIP,CN,DIRECT",
            "MATCH,🔯 节点选择",
        ],
    }
    
    import yaml
    return yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)


def generate_singbox_json(nodes: List[ProxyNode]) -> str:
    """
    生成 sing-box 配置文件 (JSON 格式)
    兼容 sing-box 1.8+
    """
    outbounds = []
    tag_index = 1
    
    for node in nodes:
        tag = f"proxy_{tag_index}"
        tag_index += 1
        
        try:
            if node.protocol == "vmess":
                b64 = node.raw_line[8:]
                info = json.loads(base64.b64decode(b64).decode("utf-8"))
                outbound = {
                    "type": "vmess",
                    "tag": tag,
                    "server": node.server,
                    "server_port": node.port,
                    "uuid": info.get("id", ""),
                    "security": "auto",
                    "alter_id": int(info.get("aid", 0)),
                }
                tls_map = {"tcp": "tcp", "ws": "ws", "grpc": "grpc"}
                transport_type = tls_map.get(info.get("net", "tcp"), "tcp")
                outbound["transport"] = {"type": transport_type}
                if transport_type == "ws":
                    outbound["transport"]["path"] = info.get("path", "/")
                    if info.get("host"):
                        outbound["transport"]["headers"] = {"Host": info["host"]}
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
                    outbound = {
                        "type": "vless",
                        "tag": tag,
                        "server": node.server,
                        "server_port": node.port,
                        "uuid": uuid_val,
                    }
                    network = params.get("network", "tcp")
                    outbound["transport"] = {"type": network}
                    security = params.get("security", "")
                    if security == "tls":
                        outbound["tls"] = {"enabled": True, "server_name": params.get("sni", "")}
                    elif security == "reality":
                        outbound["tls"] = {
                            "enabled": True,
                            "server_name": params.get("sni", ""),
                            "reality": {"enabled": True, "public_key": params.get("pbk", ""), "short_id": params.get("sid", "")},
                        }
                    if params.get("flow"):
                        outbound["flow"] = params["flow"]
                    outbounds.append(outbound)
                    
            elif node.protocol == "ss":
                rest = node.raw_line[5:]
                if "@" in rest:
                    mp_part = rest.split("@")[0]
                    try:
                        decoded = base64.b64decode(mp_part + "==").decode("utf-8")
                        if ":" in decoded:
                            method, password = decoded.split(":", 1)
                            outbound = {
                                "type": "shadowsocks",
                                "tag": tag,
                                "server": node.server,
                                "server_port": node.port,
                                "method": method,
                                "password": password,
                            }
                            outbounds.append(outbound)
                    except:
                        pass
                        
            elif node.protocol == "trojan":
                m = re.match(r'trojan://([^@]+)@([^:]+):(\+?\d+)', node.raw_line)
                if m:
                    outbound = {
                        "type": "trojan",
                        "tag": tag,
                        "server": node.server,
                        "server_port": node.port,
                        "password": m.group(1),
                    }
                    outbounds.append(outbound)
                    
            elif node.protocol == "hysteria2":
                m = re.match(r'hysteria2://([^@]+)@([^:]+):(\+?\d+)', node.raw_line)
                if m:
                    outbound = {
                        "type": "hysteria2",
                        "tag": tag,
                        "server": node.server,
                        "server_port": node.port,
                        "password": m.group(1),
                    }
                    outbounds.append(outbound)
                    
        except Exception as e:
            log(f"  [sing-box转换跳过] {node.name}: {e}")
    
    # 选择器标签列表
    tags = [o["tag"] for o in outbounds]
    
    config = {
        "log": {"level": "info", "timestamp": True},
        "dns": {"servers": [{"address": "223.5.5.5"}, {"address": "119.29.29.29"}]},
        "inbounds": [
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "::",
                "listen_port": 2080,
            }
        ],
        "outbounds": [
            {
                "type": "urltest",
                "tag": "自动选择",
                "outbounds": tags,
                "url": "http://www.gstatic.com/generate_204",
                "interval": "5m",
            },
            {
                "type": "select",
                "tag": "节点选择",
                "outbounds": ["自动选择", "direct"] + tags,
                *outbounds,
            },
        ],
        "route": {
            "rules": [
                {"ip_is_private": True, "outbound": "direct"},
                {"geoip": {"code": "CN", "reverse": True}, "outbound": "自动选择"},
            ],
            "final": "自动选择",
        },
    }
    
    return json.dumps(config, indent=2, ensure_ascii=False)


def generate_raw_text(nodes: List[ProxyNode]) -> str:
    """生成原始订阅文本（所有节点URI，每行一个）"""
    return "\n".join(node.raw_line for node in nodes) + "\n"


# ============================================================
# 主流程
# ============================================================

def main():
    log("=" * 60)
    log("Proxy Subscription Aggregator Started")
    log("=" * 60)
    
    # 1. 读取订阅链接
    sub_urls_raw = os.getenv(SUB_URLS_ENV, "")
    if not sub_urls_raw:
        log("[错误] 未设置 SUB_URLS 环境变量!")
        sys.exit(1)
    
    # 支持逗号、换行、空格分隔
    sub_urls = re.split(r'[,\n\s]+', sub_urls_raw.strip())
    sub_urls = [u.strip() for u in sub_urls if u.strip()]
    
    log(f"[订阅链接] 共 {len(sub_urls)} 个:")
    for i, url in enumerate(sub_urls, 1):
        # 隐藏敏感信息，只显示域名
        try:
            domain = url.split("//")[1].split("/")[0] if "//" in url else url
            log(f"  {i}. {domain}")
        except:
            log(f"  {i}. ***")
    
    # 2. 抓取所有订阅
    log("\n[步骤1] 开始抓取订阅...")
    all_text = ""
    total_fetched = 0
    
    for url in sub_urls:
        log(f"  正在抓取: ...")
        content = fetch_url(url)
        if content:
            all_text += content + "\n"
            total_fetched += 1
    
    if not all_text.strip():
        log("[错误] 所有订阅链接均抓取失败!")
        sys.exit(1)
    
    log(f"[抓取完成] 成功: {total_fetched}/{len(sub_urls)}, 总长度: {len(all_text)}")
    
    # 3. 提取节点
    log("\n[步骤2] 提取节点...")
    nodes = extract_nodes(all_text)
    log(f"[提取完成] 共发现 {len(nodes)} 个节点")
    
    if not nodes:
        log("[错误] 未提取到任何有效节点!")
        sys.exit(1)
    
    # 4. 去重
    log("\n[步骤3] 去重处理...")
    nodes = deduplicate(nodes)
    
    # 5. 存活测试
    log("\n[步骤4] 存活测试...")
    nodes = filter_alive(nodes)
    
    if not nodes:
        log("[警告] 所有节点均为死节点! 输出去重后的全部节点作为后备...")
        # 重新使用去重后的结果
        nodes = deduplicate(extract_nodes(all_text))
    
    # 6. 创建输出目录
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 7. 生成各格式文件
    log("\n[步骤5] 生成输出文件...")
    
    # Raw 文本
    raw_content = generate_raw_text(nodes)
    RAW_FILE.write_text(raw_content, encoding="utf-8")
    log(f"  ✓ raw.txt ({len(nodes)} 个节点)")
    
    # Clash YAML
    try:
        import yaml
        clash_content = generate_clash_yaml(nodes)
        CLASH_FILE.write_text(clash_content, encoding="utf-8")
        log(f"  ✓ clash.yaml")
    except ImportError:
        log("  ✗ clash.yaml (缺少 PyYAML 库，将安装后重试)")
        os.system("pip install pyyaml -q")
        import yaml
        clash_content = generate_clash_yaml(nodes)
        CLASH_FILE.write_text(clash_content, encoding="utf-8")
        log(f"  ✓ clash.yaml (已安装 PyYAML)")
    
    # Sing-box JSON
    singbox_content = generate_singbox_json(nodes)
    SINGBOX_FILE.write_text(singbox_content, encoding="utf-8")
    log(f"  ✓ singbox.json")
    
    # 8. 统计信息
    log("\n" + "=" * 60)
    log(f"完成! 最终有效节点数: {len(nodes)}")
    log(f"输出目录: {OUTPUT_DIR}")
    log("=" * 60)
    
    # 输出摘要供 GitHub Actions 使用
    print(f"\n::set-output name=node_count::{len(nodes)}")
    print(f"::set-output name=raw_file::{RAW_FILE}")
    print(f"::set-output name=clash_file::{CLASH_FILE}")
    print(f"::set-output name=singbox_file::{SINGBOX_FILE}")


if __name__ == "__main__":
    main()
