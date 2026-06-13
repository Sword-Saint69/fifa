#!/usr/bin/env python3
import os
import sys
import re
import argparse
import urllib.request
import urllib.error
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Console colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

# Enable Windows virtual terminal colors
if os.name == 'nt':
    os.system('')

def parse_m3u(filepath):
    """Parses M3U file returning a list of dicts: {'name': name, 'url': url, 'line_header': extinf}"""
    channels = []
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' not found.{RESET}")
        return []

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    lines = content.splitlines()
    current_header = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            current_header = line
        elif not line.startswith("#"):
            # This is a stream URL
            # Get name from extinf if available
            name = "Unnamed Channel"
            if current_header:
                comma_idx = current_header.rfind(',')
                if comma_idx != -1:
                    name = current_header[comma_idx + 1:].strip()
            
            channels.append({
                "name": name,
                "url": line,
                "header": current_header
            })
            current_header = None
            
    return channels

def parse_txt(filepath):
    """Parses plain text file with one URL per line."""
    channels = []
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' not found.{RESET}")
        return []

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            # Basic validation to ensure it's a URL
            if line.startswith(("http://", "https://", "rtmp://", "rtsp://")):
                channels.append({
                    "name": f"Channel {idx}",
                    "url": line,
                    "header": f"#EXTINF:-1,Channel {idx}"
                })
    return channels

def check_link(channel, timeout=5):
    """Verifies a single channel URL by sending an HTTP HEAD or GET request."""
    url_with_cookie = channel['url']
    name = channel['name']
    
    # Extract url and cookies if separator '|cookie=' is present
    url = url_with_cookie.split('|cookie=')[0].strip()
    cookie = url_with_cookie.split('|cookie=')[1].strip() if '|cookie=' in url_with_cookie else None
    
    start_time = time.time()
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        if cookie:
            req.add_header('Cookie', cookie)

        # Try HEAD request first for speed, fallback to GET if HEAD is not supported/allowed
        try:
            req.method = 'HEAD'
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status = response.status
        except Exception:
            req.method = 'GET'
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status = response.status

        elapsed = (time.time() - start_time) * 1000
        
        # Valid statuses for stream server response
        if status in (200, 206, 301, 302, 307, 308):
            return {
                "channel": channel,
                "online": True,
                "status": status,
                "latency": elapsed,
                "message": f"ONLINE ({status})"
            }
        else:
            return {
                "channel": channel,
                "online": False,
                "status": status,
                "latency": elapsed,
                "message": f"BAD STATUS ({status})"
            }
            
    except urllib.error.HTTPError as e:
        elapsed = (time.time() - start_time) * 1000
        # Some servers might return 403/401 but are still online (auth/referrer restriction)
        if e.code in (401, 403):
            return {
                "channel": channel,
                "online": True,  # Count as online since the resource exists but requires auth
                "status": e.code,
                "latency": elapsed,
                "message": f"RESTRICTED ({e.code})"
            }
        return {
            "channel": channel,
            "online": False,
            "status": e.code,
            "latency": elapsed,
            "message": f"HTTP ERROR ({e.code})"
        }
    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return {
            "channel": channel,
            "online": False,
            "status": 0,
            "latency": elapsed,
            "message": str(e)
        }

def main():
    parser = argparse.ArgumentParser(description="Standalone IPTV Stream URL Link Checker")
    parser.add_argument("input", help="Path to input file (.m3u, .m3u8, or .txt with one URL per line)")
    parser.add_argument("-o", "--output", help="Path to output file for working links", default="working_channels.m3u")
    parser.add_argument("-t", "--threads", type=int, help="Number of concurrent check threads", default=10)
    parser.add_argument("-w", "--timeout", type=int, help="HTTP connection timeout in seconds", default=5)
    
    args = parser.parse_args()

    # Detect file type
    filename = args.input.lower()
    is_m3u = filename.endswith((".m3u", ".m3u8"))
    
    print(f"\n{BOLD}{CYAN}=== IPTV Link Checker ==={RESET}")
    print(f"Reading: {args.input}")
    
    if is_m3u:
        channels = parse_m3u(args.input)
    else:
        print("M3U format not detected. Parsing as plain text list of URLs...")
        channels = parse_txt(args.input)
        
    total = len(channels)
    if total == 0:
        print(f"{RED}No channels/URLs found to check.{RESET}")
        sys.exit(0)
        
    print(f"Found {total} channels. Starting concurrent scan using {args.threads} threads...\n")
    
    working_channels = []
    completed = 0
    total_latency = 0
    working_count = 0
    
    # Open output file immediately to save online streams as they are discovered
    out_file = None
    try:
        out_file = open(args.output, 'w', encoding='utf-8')
        if is_m3u or args.output.endswith((".m3u", ".m3u8")):
            out_file.write("#EXTM3U\n")
            out_file.flush()
    except Exception as e:
        print(f"{RED}Warning: Could not open output file '{args.output}' for writing: {e}{RESET}")
        
    try:
        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            futures = {executor.submit(check_link, ch, args.timeout): ch for ch in channels}
            
            for future in as_completed(futures):
                res = future.result()
                completed += 1
                ch = res["channel"]
                
                # Print live results
                status_color = GREEN if res["online"] else RED
                indicator = "✔" if res["online"] else "✘"
                
                print(f"[{completed}/{total}] {status_color}{indicator} {ch['name']}{RESET}")
                print(f"    URL: {ch['url']}")
                print(f"    Result: {status_color}{res['message']}{RESET} | Latency: {res['latency']:.1f}ms\n")
                
                if res["online"]:
                    working_channels.append(ch)
                    working_count += 1
                    total_latency += res["latency"]
                    
                    if out_file:
                        try:
                            if is_m3u or args.output.endswith((".m3u", ".m3u8")):
                                if ch.get("header"):
                                    out_file.write(f"{ch['header']}\n")
                                else:
                                    out_file.write(f"#EXTINF:-1,{ch['name']}\n")
                                out_file.write(f"{ch['url']}\n")
                            else:
                                out_file.write(f"{ch['url']}\n")
                            out_file.flush()
                        except Exception as write_err:
                            print(f"{RED}Error writing to output file: {write_err}{RESET}")
    finally:
        if out_file:
            out_file.close()
            
    # Summary
    success_rate = (working_count / total) * 100 if total > 0 else 0
    avg_latency = (total_latency / working_count) if working_count > 0 else 0
    
    print(f"{BOLD}{CYAN}=== Scan Summary ==={RESET}")
    print(f"Total checked: {total}")
    print(f"Working:       {GREEN}{working_count}{RESET}")
    print(f"Failed:        {RED}{total - working_count}{RESET}")
    print(f"Success Rate:  {success_rate:.1f}%")
    if working_count > 0:
        print(f"Average Ping:  {avg_latency:.1f}ms")
        print(f"\n{GREEN}Saved {working_count} working links to '{args.output}'.{RESET}\n")
    else:
        print(f"\n{YELLOW}No working channels found.{RESET}\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{RED}Process interrupted by user.{RESET}")
        sys.exit(0)
