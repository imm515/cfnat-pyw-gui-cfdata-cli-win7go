#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cfnat 本地订阅生成器 - GUI版本
"""

import subprocess
import re
import base64
import json
import http.server
import socketserver
import threading
import socket
import sys
import os
import time
import argparse
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

CFNAT_BIN = "cfnat-windows7-amd64.exe"
NODES_FILE = "nodes.txt"
SUBSCRIPTION_FILE = "subscription.txt"
LOCATION_CACHE_FILE = "location_cache.json"
DEFAULT_PORT = 8888
MAX_NODES = 20
SUB_PATH = "/sub"
LOCATION_DATA = None
LOCATION_CACHE = {}
PID_FILE = "cfnat_sub.pid"

captured_ips = []
captured_data = []
current_ip = ""
cfnat_proc = None
http_server = None
running = True
location_stats = {}
scan_start_time = 0
last_progress_done = 0
last_progress_time = 0
current_template = None

ip_switch_history = []
MIN_VALIDITY_MINUTES = 10
MAX_SHORT_VALIDITY_COUNT = 2

gui_app = None


def kill_existing_cfnat():
    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq cfnat-windows7-amd64.exe', '/FO', 'CSV', '/NH'],
                capture_output=True, text=True, encoding='gbk', errors='replace'
            )
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if 'cfnat-windows7-amd64.exe' in line:
                    parts = line.split(',')
                    if len(parts) >= 2:
                        pid = parts[1].strip('"')
                        try:
                            subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
                            print(f"[清理] 已关闭残留进程: PID {pid}")
                            gui_print(f"[清理] 已关闭残留进程: PID {pid}")
                        except:
                            pass
        except:
            pass


def check_single_instance():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pid_path = os.path.join(script_dir, PID_FILE)
    
    if os.path.exists(pid_path):
        try:
            with open(pid_path, 'r') as f:
                old_pid = int(f.read().strip())
            
            if sys.platform == 'win32':
                import ctypes
                kernel32 = ctypes.windll.kernel32
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                STILL_ACTIVE = 259
                
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, old_pid)
                if handle:
                    try:
                        exit_code = ctypes.c_ulong()
                        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                            if exit_code.value == STILL_ACTIVE:
                                kernel32.CloseHandle(handle)
                                return False
                    finally:
                        kernel32.CloseHandle(handle)
        except:
            pass
    
    try:
        with open(pid_path, 'w') as f:
            f.write(str(os.getpid()))
    except:
        pass
    
    return True


def cleanup_pid_file():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pid_path = os.path.join(script_dir, PID_FILE)
    try:
        if os.path.exists(pid_path):
            with open(pid_path, 'r') as f:
                saved_pid = int(f.read().strip())
            if saved_pid == os.getpid():
                os.remove(pid_path)
    except:
        pass


def parse_bat_config(bat_path):
    config = {
        'colo': 'HKG',
        'delay': 200,
        'task': 100,
        'num': 3,
        'ipnum': 50,
    }
    try:
        with open(bat_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        colo_match = re.search(r'-colo\s+"?(\w+)"?', content)
        if colo_match:
            config['colo'] = colo_match.group(1).upper()
        
        delay_match = re.search(r'-delay\s+(\d+)', content)
        if delay_match:
            config['delay'] = int(delay_match.group(1))
        
        task_match = re.search(r'-task\s+(\d+)', content)
        if task_match:
            config['task'] = int(task_match.group(1))
        
        num_match = re.search(r'-num\s+(\d+)', content)
        if num_match:
            config['num'] = int(num_match.group(1))
        
        ipnum_match = re.search(r'-ipnum\s+(\d+)', content)
        if ipnum_match:
            config['ipnum'] = int(ipnum_match.group(1))
        
        return config
    except:
        return config


def find_bat_files():
    bat_files = []
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for f in os.listdir(script_dir):
        if f.startswith('启动cfnat-') and f.endswith('.bat'):
            bat_files.append(f)
    return sorted(bat_files)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'


def gui_print(msg):
    if gui_app and gui_app.log_text:
        try:
            gui_app.root.after(0, lambda: _gui_print_impl(msg))
        except:
            print(msg)
    else:
        print(msg)


def _gui_print_impl(msg):
    if gui_app and gui_app.log_text:
        try:
            gui_app.log_text.configure(state='normal')
            gui_app.log_text.insert(tk.END, msg + '\n')
            gui_app.log_text.see(tk.END)
            gui_app.log_text.configure(state='disabled')
        except:
            pass


def gui_print_replace(msg):
    if gui_app and gui_app.log_text:
        try:
            gui_app.root.after(0, lambda: _gui_print_replace_impl(msg))
        except:
            print(msg)
    else:
        print(msg)


def _gui_print_replace_impl(msg):
    if gui_app and gui_app.log_text:
        try:
            gui_app.log_text.configure(state='normal')
            gui_app.log_text.delete("end-2l", "end-1l")
            gui_app.log_text.insert(tk.END, msg + '\n')
            gui_app.log_text.see(tk.END)
            gui_app.log_text.configure(state='disabled')
        except:
            pass


def load_location_data():
    global LOCATION_DATA, LOCATION_CACHE
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    cache_path = os.path.join(script_dir, LOCATION_CACHE_FILE)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                LOCATION_CACHE = json.load(f)
        except:
            LOCATION_CACHE = {}
    
    locations_path = os.path.join(script_dir, 'locations.json')
    if os.path.exists(locations_path):
        try:
            with open(locations_path, 'r', encoding='utf-8') as f:
                LOCATION_DATA = json.load(f)
        except:
            LOCATION_DATA = None


def save_location_cache():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_path = os.path.join(script_dir, LOCATION_CACHE_FILE)
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(LOCATION_CACHE, f, ensure_ascii=False, indent=2)
    except:
        pass


def get_location_code(city_name):
    global LOCATION_CACHE
    
    if city_name in LOCATION_CACHE:
        return LOCATION_CACHE[city_name]
    
    if LOCATION_DATA:
        city_lower = city_name.lower()
        for loc in LOCATION_DATA:
            if loc.get('city', '').lower() == city_lower:
                code = loc.get('iata', '---')
                LOCATION_CACHE[city_name] = code
                save_location_cache()
                return code
    
    LOCATION_CACHE[city_name] = '---'
    save_location_cache()
    return '---'


def parse_vless_url(url):
    try:
        match = re.match(r'vless://([^@]+)@([^:]+):(\d+)\?(.*)#(.*)', url)
        if not match:
            return None
        uuid, ip, port, params, name = match.groups()
        return {
            'type': 'vless',
            'uuid': uuid,
            'ip': ip,
            'port': int(port),
            'params': params,
            'name': name
        }
    except:
        return None


def build_vless_url(node):
    return f"vless://{node['uuid']}@{node['ip']}:{node['port']}?{node['params']}#{node['name']}"


def parse_vmess_url(url):
    try:
        if not url.startswith('vmess://'):
            return None
        b64_data = url[8:]
        b64_data += '=' * (4 - len(b64_data) % 4)
        json_str = base64.b64decode(b64_data).decode('utf-8')
        data = json.loads(json_str)
        return {
            'type': 'vmess',
            'data': data
        }
    except:
        return None


def build_vmess_url(node):
    json_str = json.dumps(node['data'], ensure_ascii=False)
    b64_data = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
    return f"vmess://{b64_data}"


def load_template():
    global current_template
    if current_template:
        template_path = current_template
    else:
        template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), NODES_FILE)
    
    if not os.path.exists(template_path):
        gui_print(f"[错误] 找不到模板文件: {template_path}")
        return []
    
    nodes = []
    with open(template_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith('vless://'):
                node = parse_vless_url(line)
                if node:
                    node['raw'] = line
                    nodes.append(node)
            elif line.startswith('vmess://'):
                node = parse_vmess_url(line)
                if node:
                    node['raw'] = line
                    nodes.append(node)
    return nodes


def replace_node_ip(node, new_ip, idx=None):
    if node['type'] == 'vless':
        if node['port'] == 443:
            new_node = node.copy()
            new_node['ip'] = new_ip
            return new_node
        return node
    elif node['type'] == 'vmess':
        port = int(node['data'].get('port', 0))
        if port == 443:
            new_node = {'type': 'vmess', 'data': node['data'].copy()}
            new_node['data']['add'] = new_ip
            return new_node
        return node
    return node


def check_ip_switch_too_frequent():
    global ip_switch_history, cfnat_proc, running

    current_time = time.time()
    ip_switch_history.append(current_time)

    if len(ip_switch_history) < MAX_SHORT_VALIDITY_COUNT + 1:
        return False

    recent_switches = ip_switch_history[-(MAX_SHORT_VALIDITY_COUNT + 1):]
    intervals = []
    for i in range(1, len(recent_switches)):
        interval_minutes = (recent_switches[i] - recent_switches[i-1]) / 60
        intervals.append(interval_minutes)

    short_interval_count = sum(1 for interval in intervals if interval < MIN_VALIDITY_MINUTES)

    if short_interval_count >= MAX_SHORT_VALIDITY_COUNT:
        gui_print(f"")
        gui_print(f"{'!'*60}")
        gui_print(f"[严重警告] 检测到 IP 频繁切换，强制终止 cfnat！")
        gui_print(f"[提示] 连续 {MAX_SHORT_VALIDITY_COUNT} 次切换间隔低于 {MIN_VALIDITY_MINUTES} 分钟。")
        gui_print(f"{'!'*60}")
        
        if cfnat_proc:
            cfnat_proc.terminate()
        
        running = False
        return True

    return False


def generate_subscription(ips=None, sort_by_delay=True, silent=False):
    template_nodes = load_template()
    if not template_nodes:
        return ""
    
    use_ips = []
    
    if ips:
        use_ips = ips
    elif current_ip:
        use_ips = [current_ip]
    else:
        sub_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SUBSCRIPTION_FILE)
        if os.path.exists(sub_path):
            try:
                with open(sub_path, 'r', encoding='utf-8') as f:
                    cached_content = f.read().strip()
                    if cached_content:
                        if not silent:
                            gui_print(f"[订阅文件] 使用缓存内容: {SUBSCRIPTION_FILE}")
                        return cached_content
            except:
                pass
        return base64.b64encode('\n'.join([n['raw'] for n in template_nodes]).encode()).decode()
    
    ip_index = 0
    result_lines = []
    for node in template_nodes:
        if node['type'] == 'vless' and node['port'] == 443:
            new_ip = use_ips[ip_index % len(use_ips)]
            new_node = replace_node_ip(node, new_ip, ip_index + 1)
            result_lines.append(build_vless_url(new_node))
            ip_index += 1
        elif node['type'] == 'vmess' and int(node['data'].get('port', 0)) == 443:
            new_ip = use_ips[ip_index % len(use_ips)]
            new_node = replace_node_ip(node, new_ip, ip_index + 1)
            result_lines.append(build_vmess_url(new_node))
            ip_index += 1
        else:
            if node['type'] == 'vless':
                result_lines.append(build_vless_url(node))
            elif node['type'] == 'vmess':
                result_lines.append(build_vmess_url(node))
    
    result = base64.b64encode('\n'.join(result_lines).encode()).decode()
    
    if not silent:
        sub_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SUBSCRIPTION_FILE)
        try:
            with open(sub_path, 'w', encoding='utf-8') as f:
                f.write(result)
            if ips and len(ips) == 1:
                gui_print(f"[订阅文件] 已更新为当前IP: {ips[0]}")
            else:
                gui_print(f"[订阅文件] 已更新: {SUBSCRIPTION_FILE}")
        except:
            pass
    
    return result


class SubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    
    def do_GET(self):
        if self.path == SUB_PATH or self.path == '/':
            if current_ip:
                sub_content = generate_subscription([current_ip], silent=True)
            else:
                sub_content = generate_subscription(silent=True)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', len(sub_content))
            self.end_headers()
            self.wfile.write(sub_content.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()


def start_http_server(port):
    global http_server
    try:
        http_server = socketserver.TCPServer(('0.0.0.0', port), SubHandler)
        http_server.allow_reuse_address = True
        local_ip = get_local_ip()
        gui_print(f"")
        gui_print(f"{'='*60}")
        gui_print(f"[订阅服务] 已启动")
        gui_print(f"{'='*60}")
        gui_print(f"  本地访问:   http://127.0.0.1:{port}{SUB_PATH}")
        gui_print(f"  局域网访问: http://{local_ip}:{port}{SUB_PATH}")
        gui_print(f"{'='*60}")
        gui_print(f"[提示] 将上述地址复制到代理客户端的订阅地址栏即可使用")
        gui_print(f"[提示] 订阅内容会随 IP 优选自动更新，无需手动刷新")
        gui_print(f"{'='*60}")
        gui_print(f"")
        http_server.serve_forever()
    except Exception as e:
        gui_print(f"[错误] 启动HTTP服务失败: {e}")


def cfnat_worker(args):
    global captured_ips, captured_data, cfnat_proc, running, location_stats
    global scan_start_time, current_ip
    
    location_stats = {}
    
    cfnat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CFNAT_BIN)
    if not os.path.exists(cfnat_path):
        gui_print(f"[错误] 找不到 cfnat 程序: {cfnat_path}")
        return
    
    cmd = [
        cfnat_path,
        '-ips', '4',
        '-addr', '127.0.0.1:1234',
        '-colo', args.colo,
        '-random=true',
        '-delay', str(args.delay),
        '-task', str(args.task),
        '-num', str(args.num),
        '-ipnum', str(args.ipnum),
        '-port', '443',
        '-tls=true'
    ]
    
    gui_print(f"[cfnat] 启动参数: {' '.join(cmd)}")
    gui_print(f"[cfnat] 正在扫描优选 IP，请耐心等待...")
    gui_print(f"")
    
    valid_ip_pattern = re.compile(r'发现有效IP\s+(\d{1,3}(?:\.\d{1,3}){3})\s+位置信息\s+(.+?)\s+延迟\s+(\d+)\s+毫秒')
    best_conn_pattern = re.compile(r'选择最佳连接[:：]\s*地址[:：]\s*(\d{1,3}(?:\.\d{1,3}){3}):(\d+)\s*延迟[:：]\s*(\d+)\s*ms')
    progress_pattern = re.compile(r'已完成:\s*(\d+)\s+总数:\s*(\d+)')
    listen_pattern = re.compile(r'正在监听\s+(\S+)')
    validity_start_pattern = re.compile(r'开始状态检查')
    valid_conn_pattern = re.compile(r'符合要求的连接[:：]?')
    no_valid_pattern = re.compile(r'未找到符合延迟要求的连接')
    switch_ip_pattern = re.compile(r'切换到新的有效 IP[:：]\s*(\d{1,3}(?:\.\d{1,3}){3})')
    all_ip_exhausted_pattern = re.compile(r'所有 IP 都已检查过|所有 IP 都已用尽')
    scan_complete_pattern = re.compile(r'成功提取\s+(\d+)\s+个有效IP')
    rescan_pattern = re.compile(r'主函数将退出当前循环')
    fail_check_pattern = re.compile(r'状态检查失败|连续两次状态检查失败')
    
    validity_started = False
    last_best_ip = None
    last_progress_pct = -1
    last_progress_line = ""
    scan_start_time = time.time()
    
    try:
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = 0x08000000
        
        cfnat_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            creationflags=creationflags
        )
        
        for line in iter(cfnat_proc.stdout.readline, ''):
            if not running:
                break
            line = line.strip()
            if not line:
                continue
            
            listen_match = listen_pattern.search(line)
            if listen_match:
                continue
            
            if validity_start_pattern.search(line):
                if not validity_started:
                    validity_started = True
                    gui_print(f"")
                    gui_print(f"{'='*60}")
                    gui_print(f"[优选模式] 扫描完成，进入IP优选阶段")
                    gui_print(f"{'='*60}")
                    if location_stats:
                        gui_print(f"")
                        gui_print(f"[扫描统计] 各机场 IP 分布 (≥10个):")
                        gui_print(f"{'-'*60}")
                        gui_print(f"{'机场名称':<16} {'代码':<8} {'数量':>6} {'平均延迟':>10}")
                        gui_print(f"{'-'*60}")
                        sorted_stats = sorted(location_stats.items(), key=lambda x: x[1]['count'], reverse=True)
                        total = 0
                        other_count = 0
                        for loc, stats in sorted_stats:
                            count = stats['count']
                            if count < 10:
                                other_count += count
                                continue
                            code = get_location_code(loc)
                            avg_delay = (stats['min_delay'] + stats['max_delay']) // 2
                            gui_print(f"{loc:<16} {code:<8} {count:>6} {avg_delay:>9}ms")
                            total += count
                        if other_count > 0:
                            gui_print(f"{'其他':<16} {'':<8} {other_count:>6}")
                        gui_print(f"{'-'*60}")
                        gui_print(f"{'总计':<16} {'':<8} {total + other_count:>6}")
                        gui_print(f"{'-'*60}")
                        gui_print(f"")
                    
                    gui_print(f"[扫描发现] 共发现 {len(captured_ips)} 个IP")
                    gui_print(f"[订阅地址] http://127.0.0.1:{args.port}/sub")
                    gui_print(f"[等待优选] 等待cfnat选出最佳IP...")
                    
                    if captured_ips:
                        current_ip = captured_ips[0]
                        last_best_ip = None
                continue
            
            progress_match = progress_pattern.search(line)
            if progress_match and not validity_started:
                done, total = progress_match.groups()
                done_int = int(done)
                total_int = int(total)
                pct = float(done_int) / float(total_int) * 100 if total_int > 0 else 0
                pct_int = int(pct)
                if pct_int != last_progress_pct and pct_int % 5 == 0:
                    last_progress_pct = pct_int
                    ip_count = len(captured_ips)
                    elapsed = time.time() - scan_start_time
                    speed = done_int / elapsed if elapsed > 0 else 0
                    remaining = (total_int - done_int) / speed if speed > 0 else 0
                    progress_line = f"[扫描] {pct_int}% | {ip_count}个IP | {int(speed)}/秒 | 剩余{int(remaining)}秒"
                    if last_progress_line:
                        gui_print_replace(progress_line)
                    else:
                        gui_print(progress_line)
                    last_progress_line = progress_line
                continue
            
            if valid_conn_pattern.search(line):
                continue
            
            if no_valid_pattern.search(line):
                if not validity_started:
                    gui_print(f"[警告] 未找到符合延迟要求的连接，等待下一次检测...")
                continue
            
            if fail_check_pattern.search(line):
                if '连续两次' in line:
                    gui_print(f"[状态] 连续失败，切换IP...")
                continue
            
            if all_ip_exhausted_pattern.search(line):
                gui_print(f"")
                gui_print(f"[IP耗尽] 将重新扫描...")
                validity_started = False
                continue
            
            scan_complete_match = scan_complete_pattern.search(line)
            if scan_complete_match:
                count = scan_complete_match.group(1)
                gui_print(f"")
                gui_print(f"[扫描完成] 找到 {count} 个有效IP")
                continue
            
            if rescan_pattern.search(line):
                gui_print(f"")
                gui_print(f"[重新扫描] 开始...")
                validity_started = False
                continue
            
            switch_match = switch_ip_pattern.search(line)
            if switch_match:
                new_ip = switch_match.group(1)
                
                if new_ip != current_ip:
                    current_ip = new_ip
                    if check_ip_switch_too_frequent():
                        break
                    generate_subscription([new_ip])
                    gui_print(f"")
                    gui_print(f"[订阅更新] ✓ IP切换: {new_ip}")
                continue
            
            best_match = best_conn_pattern.search(line)
            if best_match:
                ip, port, delay = best_match.groups()
                
                if validity_started:
                    if ip != last_best_ip:
                        old_best = last_best_ip
                        last_best_ip = ip
                        
                        if ip != current_ip:
                            current_ip = ip
                            if check_ip_switch_too_frequent():
                                break
                            generate_subscription([ip])
                            if old_best is None:
                                gui_print(f"")
                                gui_print(f"[订阅更新] ✓ 首选IP: {ip}:{port} | 延迟 {delay}ms")
                            else:
                                gui_print(f"")
                                gui_print(f"[订阅更新] ✓ 优选IP: {ip}:{port} | 延迟 {delay}ms")
                continue
            
            valid_match = valid_ip_pattern.search(line)
            if valid_match and not validity_started:
                ip, location, delay = valid_match.groups()
                delay_int = int(delay)
                now_time = datetime.now().strftime("%H:%M:%S")
                
                if location not in location_stats:
                    location_stats[location] = {'count': 0, 'min_delay': 9999, 'max_delay': 0, 'ips': []}
                location_stats[location]['count'] += 1
                location_stats[location]['min_delay'] = min(location_stats[location]['min_delay'], delay_int)
                location_stats[location]['max_delay'] = max(location_stats[location]['max_delay'], delay_int)
                
                entry = {'ip': ip, 'location': location, 'delay': delay_int, 'time': now_time}
                
                if ip not in [e['ip'] for e in captured_data]:
                    captured_data.append(entry)
                    captured_ips.append(ip)
                continue
                    
    except Exception as e:
        gui_print(f"[错误] cfnat 运行异常: {e}")
    finally:
        if cfnat_proc:
            cfnat_proc.terminate()


class CfnatGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("cfnat 本地订阅生成器")
        self.root.geometry("700x500")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.bat_configs = {}
        self.create_widgets()
        
    def create_widgets(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        bat_files = find_bat_files()
        
        if bat_files:
            config_frame = ttk.Frame(self.root, padding="5")
            config_frame.pack(fill=tk.X)
            
            ttk.Label(config_frame, text="配置:").pack(side=tk.LEFT)
            
            def get_sort_key(bat):
                config = parse_bat_config(os.path.join(script_dir, bat))
                is_preferred = config['colo'] == 'HKG' and config['num'] == 3 and config['delay'] == 200
                return (0 if is_preferred else 1, bat)
            
            bat_files = sorted(bat_files, key=get_sort_key)
            
            display_names = []
            for bat in bat_files:
                config = parse_bat_config(os.path.join(script_dir, bat))
                display_name = f"{bat} (机房:{config['colo']} 延迟:{config['delay']}ms)"
                display_names.append(display_name)
                self.bat_configs[display_name] = config
            
            display_names.append("自定义配置")
            
            self.bat_combo = ttk.Combobox(config_frame, values=display_names, width=45, state='readonly')
            self.bat_combo.current(0)
            self.bat_combo.pack(side=tk.LEFT, padx=2)
            self.bat_combo.bind('<<ComboboxSelected>>', self.on_bat_selected)
        
        top_frame = ttk.Frame(self.root, padding="5")
        top_frame.pack(fill=tk.X)
        
        ttk.Label(top_frame, text="IP数:").pack(side=tk.LEFT)
        self.ipnum_var = tk.StringVar(value="50")
        self.ipnum_entry = ttk.Entry(top_frame, textvariable=self.ipnum_var, width=4)
        self.ipnum_entry.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(top_frame, text="服务端口:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value="8888")
        self.port_entry = ttk.Entry(top_frame, textvariable=self.port_var, width=5)
        self.port_entry.pack(side=tk.LEFT, padx=2)
        
        self.start_btn = ttk.Button(top_frame, text="启动", command=self.start_cfnat)
        self.start_btn.pack(side=tk.LEFT, padx=10)
        
        self.colo_var = tk.StringVar(value="HKG")
        self.delay_var = tk.StringVar(value="200")
        self.task_var = tk.StringVar(value="100")
        self.num_var = tk.StringVar(value="3")
        
        self.log_text = scrolledtext.ScrolledText(self.root, state='disabled', font=('Consolas', 11))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        bottom_frame = ttk.Frame(self.root, padding="5")
        bottom_frame.pack(fill=tk.X)
        
        ttk.Label(bottom_frame, text="提示: 关闭窗口终止运行").pack(side=tk.LEFT)
        
    def on_bat_selected(self, event):
        selected = self.bat_combo.get()
        if selected in self.bat_configs:
            config = self.bat_configs[selected]
            self.colo_var.set(config['colo'])
            self.delay_var.set(str(config['delay']))
            self.task_var.set(str(config['task']))
            self.num_var.set(str(config['num']))
            self.ipnum_var.set(str(config['ipnum']))
        
    def start_cfnat(self):
        global running, current_template
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        nodes_path = os.path.join(script_dir, NODES_FILE)
        
        if not os.path.exists(nodes_path):
            messagebox.showerror("错误", f"未找到节点模版: {NODES_FILE}\n请创建节点模版文件")
            return
        
        current_template = nodes_path
        
        try:
            colo = self.colo_var.get().upper()
            delay = int(self.delay_var.get())
            task = int(self.task_var.get())
            num = int(self.num_var.get())
            ipnum = int(self.ipnum_var.get())
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")
            return
        
        self.log_text.configure(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state='disabled')
        
        gui_print(f"{'='*60}")
        gui_print(f"  cfnat 本地订阅生成器")
        gui_print(f"{'='*60}")
        
        kill_existing_cfnat()
        
        running = True
        self.start_btn.configure(state=tk.DISABLED)
        
        gui_print(f"[启动参数] 机房={colo} 延迟={delay}ms IP数={ipnum}")
        gui_print(f"[节点模版] {NODES_FILE}")
        
        class Args:
            pass
        args = Args()
        args.colo = colo
        args.delay = delay
        args.task = task
        args.num = num
        args.ipnum = ipnum
        args.port = port
        
        threading.Thread(target=start_http_server, args=(port,), daemon=True).start()
        
        time.sleep(0.5)
        sub_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SUBSCRIPTION_FILE)
        if os.path.exists(sub_path):
            try:
                with open(sub_path, 'r', encoding='utf-8') as f:
                    cached_content = f.read().strip()
                    if cached_content:
                        gui_print(f"[订阅文件] 使用缓存内容: {SUBSCRIPTION_FILE}")
            except:
                pass
        
        threading.Thread(target=cfnat_worker, args=(args,), daemon=True).start()
        
    def on_closing(self):
        result = messagebox.askyesnocancel("退出", "是否同时关闭 cfnat 进程？\n\n是 = 关闭脚本和cfnat\n否 = 仅关闭脚本\n取消 = 取消操作")
        
        if result is None:
            return
        elif result:
            global cfnat_proc, http_server, running
            running = False
            if cfnat_proc:
                cfnat_proc.terminate()
            if http_server:
                http_server.shutdown()
        
        self.root.destroy()


def cli_mode():
    global running, current_template
    
    print(f"\n{'='*60}")
    print("cfnat 本地订阅生成器 (CLI模式)")
    print(f"{'='*60}")
    
    parser = argparse.ArgumentParser(description='cfnat 本地订阅生成器')
    parser.add_argument('--colo', default='HKG', help='机房代码 (默认: HKG)')
    parser.add_argument('--delay', type=int, default=200, help='延迟阈值ms (默认: 200)')
    parser.add_argument('--task', type=int, default=100, help='并发任务数 (默认: 100)')
    parser.add_argument('--num', type=int, default=3, help='每个IP的端口数 (默认: 3)')
    parser.add_argument('--ipnum', type=int, default=50, help='IP数量 (默认: 50)')
    parser.add_argument('--port', type=int, default=8888, help='订阅服务端口 (默认: 8888)')
    parser.add_argument('--debug', '-d', action='store_true', help='调试模式，显示详细输出')
    parser.add_argument('--sub-only', action='store_true', help='仅启动订阅服务')
    parser.add_argument('--ips', type=str, help='手动指定IP列表(逗号分隔)')
    parser.add_argument('--template', type=str, help='模版文件路径')
    args = parser.parse_args()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    nodes_path = os.path.join(script_dir, NODES_FILE)
    
    if args.template:
        current_template = args.template
    else:
        current_template = nodes_path
    
    if not os.path.exists(current_template):
        print(f"[错误] 未找到节点模版: {current_template}")
        print(f"[提示] 请创建节点模版文件")
        return
    
    if args.ips:
        ip_list = [ip.strip() for ip in args.ips.split(',')]
        for ip in ip_list:
            if ip and ip not in captured_ips:
                now_time = datetime.now().strftime("%H:%M:%S")
                captured_data.append({'ip': ip, 'port': '443', 'time': now_time})
                captured_ips.append(ip)
        print(f"[手动IP] 已加载 {len(captured_ips)} 个 IP")
    
    kill_existing_cfnat()
    
    print(f"[启动参数] 机房={args.colo} 延迟={args.delay}ms IP数={args.ipnum}")
    print(f"[节点模版] {os.path.basename(current_template)}")
    
    port = args.port
    http_thread = threading.Thread(target=start_http_server_cli, args=(port,), daemon=True)
    http_thread.start()
    
    time.sleep(0.5)
    sub_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SUBSCRIPTION_FILE)
    if os.path.exists(sub_path):
        try:
            with open(sub_path, 'r', encoding='utf-8') as f:
                cached_content = f.read().strip()
                if cached_content:
                    print(f"[订阅文件] 使用缓存内容: {SUBSCRIPTION_FILE}")
        except:
            pass
    
    if args.sub_only:
        print("[模式] 仅订阅服务，按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n退出...")
    else:
        print(f"[模式] 完整服务，按 Ctrl+C 退出\n")
        try:
            cfnat_worker_cli(args)
        except KeyboardInterrupt:
            print("\n退出...")


def start_http_server_cli(port):
    global http_server
    try:
        http_server = socketserver.TCPServer(('0.0.0.0', port), SubHandler)
        http_server.allow_reuse_address = True
        local_ip = get_local_ip()
        print(f"\n{'='*60}")
        print(f"[订阅服务] 已启动")
        print(f"{'='*60}")
        print(f"  本地访问:   http://127.0.0.1:{port}{SUB_PATH}")
        print(f"  局域网访问: http://{local_ip}:{port}{SUB_PATH}")
        print(f"{'='*60}")
        print(f"[提示] 将上述地址复制到代理客户端的订阅地址栏即可使用")
        print(f"[提示] 订阅内容会随 IP 优选自动更新，无需手动刷新")
        print(f"{'='*60}\n")
        http_server.serve_forever()
    except Exception as e:
        print(f"[错误] 启动HTTP服务失败: {e}")


def cfnat_worker_cli(args):
    global captured_ips, captured_data, cfnat_proc, running, location_stats
    global scan_start_time, current_ip
    
    location_stats = {}
    
    cfnat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CFNAT_BIN)
    if not os.path.exists(cfnat_path):
        print(f"[错误] 找不到 cfnat 程序: {cfnat_path}")
        return
    
    cmd = [
        cfnat_path,
        '-ips', '4',
        '-addr', '127.0.0.1:1234',
        '-colo', args.colo,
        '-random=true',
        '-delay', str(args.delay),
        '-task', str(args.task),
        '-num', str(args.num),
        '-ipnum', str(args.ipnum),
        '-port', '443',
        '-tls=true'
    ]
    
    print(f"[cfnat] 启动参数: {' '.join(cmd)}")
    print(f"[cfnat] 正在扫描优选 IP，请耐心等待...\n")
    
    valid_ip_pattern = re.compile(r'发现有效IP\s+(\d{1,3}(?:\.\d{1,3}){3})\s+位置信息\s+(.+?)\s+延迟\s+(\d+)\s+毫秒')
    best_conn_pattern = re.compile(r'选择最佳连接[:：]\s*地址[:：]\s*(\d{1,3}(?:\.\d{1,3}){3}):(\d+)\s*延迟[:：]\s*(\d+)\s*ms')
    progress_pattern = re.compile(r'已完成:\s*(\d+)\s+总数:\s*(\d+)')
    listen_pattern = re.compile(r'正在监听\s+(\S+)')
    validity_start_pattern = re.compile(r'开始状态检查')
    valid_conn_pattern = re.compile(r'符合要求的连接[:：]?')
    no_valid_pattern = re.compile(r'未找到符合延迟要求的连接')
    switch_ip_pattern = re.compile(r'切换到新的有效 IP[:：]\s*(\d{1,3}(?:\.\d{1,3}){3})')
    all_ip_exhausted_pattern = re.compile(r'所有 IP 都已检查过|所有 IP 都已用尽')
    scan_complete_pattern = re.compile(r'成功提取\s+(\d+)\s+个有效IP')
    rescan_pattern = re.compile(r'主函数将退出当前循环')
    fail_check_pattern = re.compile(r'状态检查失败|连续两次状态检查失败')
    
    validity_started = False
    last_best_ip = None
    last_progress_pct = -1
    scan_start_time = time.time()
    
    try:
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = 0x08000000
        
        cfnat_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            creationflags=creationflags
        )
        
        for line in iter(cfnat_proc.stdout.readline, ''):
            if not running:
                break
            line = line.strip()
            if not line:
                continue
            
            listen_match = listen_pattern.search(line)
            if listen_match:
                continue
            
            if validity_start_pattern.search(line):
                if not validity_started:
                    validity_started = True
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    print(f"{'='*60}")
                    print(f"[优选模式] 扫描完成，进入IP优选阶段")
                    print(f"{'='*60}")
                    if location_stats:
                        print(f"\n[扫描统计] 各机场 IP 分布 (≥10个):")
                        print(f"{'-'*60}")
                        print(f"{'机场名称':<16} {'代码':<8} {'数量':>6} {'平均延迟':>10}")
                        print(f"{'-'*60}")
                        sorted_stats = sorted(location_stats.items(), key=lambda x: x[1]['count'], reverse=True)
                        total = 0
                        other_count = 0
                        for loc, stats in sorted_stats:
                            count = stats['count']
                            if count < 10:
                                other_count += count
                                continue
                            code = get_location_code(loc)
                            avg_delay = (stats['min_delay'] + stats['max_delay']) // 2
                            print(f"{loc:<16} {code:<8} {count:>6} {avg_delay:>9}ms")
                            total += count
                        if other_count > 0:
                            print(f"{'其他':<16} {'':<8} {other_count:>6}")
                        print(f"{'-'*60}")
                        print(f"{'总计':<16} {'':<8} {total + other_count:>6}")
                        print(f"{'-'*60}\n")
                    
                    print(f"[扫描发现] 共发现 {len(captured_ips)} 个IP")
                    print(f"[订阅地址] http://127.0.0.1:{args.port}/sub")
                    print(f"[等待优选] 等待cfnat选出最佳IP...")
                    
                    if captured_ips:
                        current_ip = captured_ips[0]
                        last_best_ip = None
                continue
            
            progress_match = progress_pattern.search(line)
            if progress_match and not validity_started:
                done, total = progress_match.groups()
                done_int = int(done)
                total_int = int(total)
                pct = float(done_int) / float(total_int) * 100 if total_int > 0 else 0
                pct_int = int(pct)
                if pct_int != last_progress_pct and pct_int % 5 == 0:
                    last_progress_pct = pct_int
                    ip_count = len(captured_ips)
                    elapsed = time.time() - scan_start_time
                    speed = done_int / elapsed if elapsed > 0 else 0
                    remaining = (total_int - done_int) / speed if speed > 0 else 0
                    print(f"\r[扫描] {pct_int}% | {ip_count}个IP | {int(speed)}/秒 | 剩余{int(remaining)}秒", end='', flush=True)
                continue
            
            if valid_conn_pattern.search(line):
                continue
            
            if no_valid_pattern.search(line):
                if not validity_started:
                    print(f"[警告] 未找到符合延迟要求的连接，等待下一次检测...")
                continue
            
            if fail_check_pattern.search(line):
                if '连续两次' in line:
                    print(f"[状态] 连续失败，切换IP...")
                continue
            
            if all_ip_exhausted_pattern.search(line):
                print(f"\n[IP耗尽] 将重新扫描...")
                validity_started = False
                continue
            
            scan_complete_match = scan_complete_pattern.search(line)
            if scan_complete_match:
                count = scan_complete_match.group(1)
                print(f"\n[扫描完成] 找到 {count} 个有效IP")
                continue
            
            if rescan_pattern.search(line):
                print(f"\n[重新扫描] 开始...")
                validity_started = False
                continue
            
            switch_match = switch_ip_pattern.search(line)
            if switch_match:
                new_ip = switch_match.group(1)
                
                check_ip_switch_too_frequent_cli()
                
                if new_ip != current_ip:
                    current_ip = new_ip
                    generate_subscription_cli([new_ip])
                    print(f"\n[订阅更新] ✓ IP切换: {new_ip}")
                continue
            
            best_match = best_conn_pattern.search(line)
            if best_match:
                ip, port, delay = best_match.groups()
                
                if validity_started:
                    if ip != last_best_ip:
                        old_best = last_best_ip
                        last_best_ip = ip
                        
                        check_ip_switch_too_frequent_cli()
                        
                        if ip != current_ip:
                            current_ip = ip
                            generate_subscription_cli([ip])
                            if old_best is None:
                                print(f"\n[订阅更新] ✓ 首选IP: {ip}:{port} | 延迟 {delay}ms")
                            else:
                                print(f"\n[订阅更新] ✓ 优选IP: {ip}:{port} | 延迟 {delay}ms")
                continue
            
            valid_match = valid_ip_pattern.search(line)
            if valid_match and not validity_started:
                ip, location, delay = valid_match.groups()
                delay_int = int(delay)
                now_time = datetime.now().strftime("%H:%M:%S")
                
                if location not in location_stats:
                    location_stats[location] = {'count': 0, 'min_delay': 9999, 'max_delay': 0, 'ips': []}
                location_stats[location]['count'] += 1
                location_stats[location]['min_delay'] = min(location_stats[location]['min_delay'], delay_int)
                location_stats[location]['max_delay'] = max(location_stats[location]['max_delay'], delay_int)
                
                entry = {'ip': ip, 'location': location, 'delay': delay_int, 'time': now_time}
                
                if ip not in [e['ip'] for e in captured_data]:
                    captured_data.append(entry)
                    captured_ips.append(ip)
                continue
                    
    except Exception as e:
        print(f"[错误] cfnat 运行异常: {e}")
    finally:
        if cfnat_proc:
            cfnat_proc.terminate()


def check_ip_switch_too_frequent_cli():
    global ip_switch_history

    current_time = time.time()
    ip_switch_history.append(current_time)

    if len(ip_switch_history) < MAX_SHORT_VALIDITY_COUNT + 1:
        return False

    recent_switches = ip_switch_history[-(MAX_SHORT_VALIDITY_COUNT + 1):]
    intervals = []
    for i in range(1, len(recent_switches)):
        interval_minutes = (recent_switches[i] - recent_switches[i-1]) / 60
        intervals.append(interval_minutes)

    short_interval_count = sum(1 for interval in intervals if interval < MIN_VALIDITY_MINUTES)

    if short_interval_count >= MAX_SHORT_VALIDITY_COUNT:
        print(f"\n{'='*60}")
        print("[警告] IP切换过于频繁!")
        print(f"最近{len(intervals)}次切换间隔:")
        for i, interval in enumerate(intervals, 1):
            print(f"  第{i}次: {interval:.1f}分钟")
        print(f"\n[提示] 连续{MAX_SHORT_VALIDITY_COUNT}次切换间隔低于{MIN_VALIDITY_MINUTES}分钟")
        print("[建议] 请增加 --delay 参数值（如改为 300 或更高）")
        print("[建议] 避免频繁切换导致封号")
        print("[操作] 按 Ctrl+C 退出程序")
        print(f"{'='*60}\n")
        return True

    return False


def generate_subscription_cli(ips=None, sort_by_delay=True, silent=False):
    template_nodes = load_template()
    if not template_nodes:
        return ""
    
    use_ips = []
    
    if ips:
        use_ips = ips
    elif current_ip:
        use_ips = [current_ip]
    else:
        sub_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SUBSCRIPTION_FILE)
        if os.path.exists(sub_path):
            try:
                with open(sub_path, 'r', encoding='utf-8') as f:
                    cached_content = f.read().strip()
                    if cached_content:
                        if not silent:
                            print(f"[订阅文件] 使用缓存内容: {SUBSCRIPTION_FILE}")
                        return cached_content
            except:
                pass
        return base64.b64encode('\n'.join([n['raw'] for n in template_nodes]).encode()).decode()
    
    ip_index = 0
    result_lines = []
    for node in template_nodes:
        if node['type'] == 'vless' and node['port'] == 443:
            new_ip = use_ips[ip_index % len(use_ips)]
            new_node = replace_node_ip(node, new_ip, ip_index + 1)
            result_lines.append(build_vless_url(new_node))
            ip_index += 1
        elif node['type'] == 'vmess' and int(node['data'].get('port', 0)) == 443:
            new_ip = use_ips[ip_index % len(use_ips)]
            new_node = replace_node_ip(node, new_ip, ip_index + 1)
            result_lines.append(build_vmess_url(new_node))
            ip_index += 1
        else:
            if node['type'] == 'vless':
                result_lines.append(build_vless_url(node))
            elif node['type'] == 'vmess':
                result_lines.append(build_vmess_url(node))
    
    result = base64.b64encode('\n'.join(result_lines).encode()).decode()
    
    if not silent:
        sub_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SUBSCRIPTION_FILE)
        try:
            with open(sub_path, 'w', encoding='utf-8') as f:
                f.write(result)
            if ips and len(ips) == 1:
                print(f"[订阅文件] 已更新为当前IP: {ips[0]}")
            else:
                print(f"[订阅文件] 已更新: {SUBSCRIPTION_FILE}")
        except:
            pass
    
    return result


def main():
    if not check_single_instance():
        print("[错误] 程序已在运行")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "程序已在运行！", "cfnat订阅生成器", 0x40)
        except:
            pass
        return
    
    load_location_data()
    
    try:
        if len(sys.argv) > 1:
            cli_mode()
        else:
            global gui_app
            gui_app = CfnatGUI()
            gui_app.root.mainloop()
    finally:
        cleanup_pid_file()


if __name__ == '__main__':
    main()
