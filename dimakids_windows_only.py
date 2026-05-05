# ============================================================
#  DimaKids Downloader — Windows-Only Edition
#  Handles M3U8/HLS by downloading .ts segments + merging
#  No ffmpeg install needed (uses yt-dlp bundled ffmpeg)
# ============================================================
import os, sys, re, json, time, math, zipfile, subprocess, importlib.util, tempfile
from pathlib import Path
from urllib.parse import urljoin, urljoin as urljoin2, urlparse

REQUIRED_PACKAGES = {
    "requests": "requests",
    "cloudscraper": "cloudscraper",
    "bs4": "beautifulsoup4",
    "rich": "rich",
    "requests_toolbelt": "requests-toolbelt",
    "yt_dlp": "yt-dlp",
}

def ensure_packages():
    missing = [pip for mod, pip in REQUIRED_PACKAGES.items() if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"Installing: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", *missing])

ensure_packages()

import requests, cloudscraper
from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
import yt_dlp

try:
    os.system("")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

console = Console(highlight=False)
scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
CHUNK_SIZE = 256 * 1024
BAR_WIDTH = 18
HIDDEN_FOLDERS = {"sample_data", ".ipynb_checkpoints", "__pycache__", ".git"}
BASE_DIR = Path.cwd()

def clean_name(name):
    return re.sub(r'[\\/*?:"<>|]', "", (name or "").strip()) or "Video"

def format_bytes(n):
    n = float(n)
    for u in ["B","KB","MB","GB"]:
        if n < 1024 or u == "GB": return f"{n:.1f}{u}" if u != "B" else f"{int(n)}B"
        n /= 1024

def format_eta(s):
    if s is None or s < 0 or math.isinf(s): return "--:--"
    s = int(s); m, s = divmod(s, 60); h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def render_bar(prefix, current, total, start_time):
    elapsed = max(time.time() - start_time, 0.001)
    speed = current / elapsed
    if total and total > 0:
        pct = max(0.0, min(100.0, current / total * 100))
        filled = int(BAR_WIDTH * current / total)
        bar = "#" * filled + "-" * (BAR_WIDTH - filled)
        eta = (total - current) / speed if speed > 0 else None
        line = f"\r{prefix} [{bar}] {pct:5.1f}% {format_bytes(current)}/{format_bytes(total)} {format_bytes(speed)}/s ETA {format_eta(eta)}"
    else:
        pulse = int((time.time() * 4) % (BAR_WIDTH + 1))
        bar = "#" * pulse + "-" * (BAR_WIDTH - pulse)
        line = f"\r{prefix} [{bar}] {format_bytes(current)} {format_bytes(speed)}/s"
    sys.stdout.write(line[:120]); sys.stdout.flush()

def finish_bar():
    sys.stdout.write("\n"); sys.stdout.flush()

# ---- Get ffmpeg from yt-dlp bundled binary ----
def get_ffmpeg():
    try:
        import yt_dlp.utils as utils
        ffmpeg_path = utils.find_ffmpeg()[0]
        if ffmpeg_path: return ffmpeg_path
    except Exception:
        pass
    # fallback: check PATH
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        if result.returncode == 0: return "ffmpeg"
    except Exception:
        pass
    return None

def get_soup(url):
    try:
        r = scraper.get(url, timeout=20); r.raise_for_status(); r.encoding = "utf-8"
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        console.print(f"[bold bright_red]Error fetching page:[/bold bright_red] {e}"); return None

def get_video_link(soup):
    if not soup: return None
    for tag in ["source", "video"]:
        el = soup.find(tag)
        if el and el.get("src"): return el["src"]
    patterns = [
        r'const\s+videoSrc\s*=\s*"([^"]+)"', r"const\s+videoSrc\s*=\s*'([^']+)'",
        r'"videoSrc"\s*:\s*"([^"]+)"',         r"'videoSrc'\s*:\s*'([^']+)'",
        r'file\s*:\s*"([^"]+)"',
        r"src\s*:\s*\"(https?://[^\"]+\.(?:mp4|m3u8)[^\"]*?)\"",
        r"src\s*:\s*'(https?://[^']+\.(?:mp4|m3u8)[^']*?)'",
    ]
    for script in soup.find_all("script"):
        content = script.string or script.get_text() or ""
        for p in patterns:
            m = re.search(p, content)
            if m: return m.group(1)
    return None

def is_m3u8(url):
    return ".m3u8" in url.lower() or "playlist" in url.lower()

def discover_episodes(page_url, soup):
    episodes = []
    selectors = [("div.episode-item","div.episode-number"),("li.episode-item",".episode-number"),(".episode-item",".episode-number")]
    for item_sel, num_sel in selectors:
        items = soup.select(item_sel)
        if not items: continue
        for item in items:
            num_el = item.select_one(num_sel)
            link_el = item.find_parent("a") or item.find("a")
            if not num_el or not link_el or not link_el.get("href"): continue
            num_text = re.sub(r"\D+", "", num_el.get_text(strip=True))
            if num_text: episodes.append({"num": int(num_text), "link": urljoin(page_url, link_el["href"])})
        if episodes: break
    if not episodes: episodes = [{"num": 1, "link": page_url}]
    unique = {ep["num"]: ep["link"] for ep in episodes}
    return sorted([{"num": k, "link": v} for k, v in unique.items()], key=lambda x: x["num"])

def parse_selection(choice, total):
    choice = choice.strip().lower()
    if choice == "all": return list(range(1, total + 1))
    selected = set()
    for part in choice.replace(" ", "").split(","):
        if not part: continue
        if "-" in part:
            try:
                a, b = map(int, part.split("-", 1))
                if a > b: a, b = b, a
                selected.update(i for i in range(a, b + 1) if 1 <= i <= total)
            except: pass
        else:
            try:
                n = int(part)
                if 1 <= n <= total: selected.add(n)
            except: pass
    return sorted(selected)

# ---- Parse M3U8 and get absolute segment URLs ----
def parse_m3u8(m3u8_url, referer):
    headers = {
        "Referer": referer, "Origin": "https://www.dimakids.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    r = scraper.get(m3u8_url, headers=headers, timeout=30)
    r.raise_for_status(); r.encoding = "utf-8"
    text = r.text

    # Check if master playlist pointing to sub-playlists
    sub_urls = re.findall(r"^(?!#)(.+\.m3u8.*)$", text, re.MULTILINE)
    if sub_urls:
        best = urljoin(m3u8_url, sub_urls[0].strip())
        return parse_m3u8(best, referer)

    base_url = m3u8_url.rsplit("/", 1)[0] + "/"
    segments = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        segments.append(urljoin(base_url, line))

    return segments

# ---- Download M3U8 via yt-dlp (primary) ----
def download_m3u8_ytdlp(url, filepath, display_name, referer):
    console.print(f"[bold bright_yellow]M3U8/HLS stream — using yt-dlp for {display_name}[/bold bright_yellow]")
    filepath.parent.mkdir(parents=True, exist_ok=True)
    start_time = [time.time()]

    def progress_hook(d):
        if d.get("status") == "downloading":
            current = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            render_bar(f"Downloading {display_name}", current, total, start_time[0])
        elif d.get("status") == "finished":
            finish_bar()

    ydl_opts = {
        "outtmpl": str(filepath.with_suffix("")) + ".%(ext)s",
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "http_headers": {"Referer": referer, "Origin": "https://www.dimakids.com",
                         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        "quiet": True, "no_warnings": True,
        "progress_hooks": [progress_hook],
        "retries": 5, "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        for candidate in [filepath, filepath.with_suffix(".mp4"), filepath.with_suffix(".mkv")]:
            if candidate.exists() and candidate.stat().st_size > 1024 * 50:
                if candidate != filepath: candidate.replace(filepath)
                return True

        for candidate in filepath.parent.iterdir():
            if candidate.stem == filepath.stem and candidate.stat().st_size > 1024 * 50:
                candidate.replace(filepath); return True

        return False
    except Exception as e:
        console.print(f"[bold bright_red]yt-dlp error:[/bold bright_red] {e}")
        return False

# ---- Fallback: manual .ts segment download + ffmpeg concat ----
def download_m3u8_manual(m3u8_url, filepath, display_name, referer):
    console.print(f"[bold bright_yellow]Trying manual segment download for {display_name}...[/bold bright_yellow]")
    headers = {
        "Referer": referer, "Origin": "https://www.dimakids.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    try:
        segments = parse_m3u8(m3u8_url, referer)
    except Exception as e:
        console.print(f"[bold bright_red]M3U8 parse error:[/bold bright_red] {e}"); return False

    if not segments:
        console.print("[bold bright_red]No segments found in M3U8.[/bold bright_red]"); return False

    console.print(f"[bold bright_cyan]Found {len(segments)} segments — downloading...[/bold bright_cyan]")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        ts_files = []
        total_segments = len(segments)
        start_time = time.time()

        for idx, seg_url in enumerate(segments, 1):
            seg_file = tmp_path / f"seg_{idx:05d}.ts"
            seg_ok = False
            for attempt in range(3):
                try:
                    seg_resp = scraper.get(seg_url, headers=headers, timeout=30, stream=True)
                    seg_resp.raise_for_status()
                    with open(seg_file, "wb") as sf:
                        for chunk in seg_resp.iter_content(chunk_size=CHUNK_SIZE):
                            if chunk: sf.write(chunk)
                    if seg_file.stat().st_size > 100:
                        seg_ok = True; break
                except Exception:
                    time.sleep(1)

            if not seg_ok:
                console.print(f"\n[bold bright_red]Failed segment {idx}[/bold bright_red]")
                continue

            ts_files.append(str(seg_file))
            elapsed = max(time.time() - start_time, 0.001)
            done = idx / total_segments
            filled = int(BAR_WIDTH * done)
            bar = "#" * filled + "-" * (BAR_WIDTH - filled)
            eta_s = (elapsed / done) * (1 - done) if done > 0 else None
            line = f"\rSegments [{bar}] {idx}/{total_segments} ETA {format_eta(eta_s)}"
            sys.stdout.write(line[:120]); sys.stdout.flush()

        finish_bar()

        if not ts_files:
            console.print("[bold bright_red]No segments downloaded.[/bold bright_red]"); return False

        # Write concat list for ffmpeg
        concat_file = tmp_path / "concat.txt"
        with open(concat_file, "w", encoding="utf-8") as cf:
            for ts in ts_files:
                cf.write(f"file '{ts}'\n")

        # Try ffmpeg merge
        ffmpeg = get_ffmpeg()
        if ffmpeg:
            console.print("[bold bright_cyan]Merging segments with ffmpeg...[/bold bright_cyan]")
            filepath.parent.mkdir(parents=True, exist_ok=True)
            cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
                   "-c", "copy", str(filepath)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0 and filepath.exists() and filepath.stat().st_size > 1024 * 50:
                return True
            console.print(f"[bold bright_red]ffmpeg error:[/bold bright_red] {result.stderr[-300:]}")

        # Fallback: binary concat (no re-encode)
        console.print("[bold bright_cyan]Binary merging segments (no ffmpeg)...[/bold bright_cyan]")
        filepath.parent.mkdir(parents=True, exist_ok=True)
        total_bytes = sum(Path(ts).stat().st_size for ts in ts_files)
        merged = 0; start_merge = time.time(); last_draw = 0
        with open(filepath, "wb") as out:
            for ts in ts_files:
                with open(ts, "rb") as seg:
                    while True:
                        chunk = seg.read(CHUNK_SIZE)
                        if not chunk: break
                        out.write(chunk); merged += len(chunk)
                        now = time.time()
                        if now - last_draw >= 0.15:
                            render_bar(f"Merging {display_name}", merged, total_bytes, start_merge)
                            last_draw = now
        render_bar(f"Merging {display_name}", merged, total_bytes, start_merge); finish_bar()
        return filepath.exists() and filepath.stat().st_size > 1024 * 50

# ---- Smart download dispatcher ----
def download_video(url, filepath, display_name, referer):
    if is_m3u8(url):
        ok = download_m3u8_ytdlp(url, filepath, display_name, referer)
        if not ok:
            console.print("[bold bright_yellow]yt-dlp failed, trying manual segment download...[/bold bright_yellow]")
            ok = download_m3u8_manual(url, filepath, display_name, referer)
        return ok

    # Direct MP4
    temp_path = filepath.with_suffix(filepath.suffix + ".part")
    try:
        headers = {
            "Referer": referer, "Origin": "https://www.dimakids.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        }
        r = scraper.get(url, stream=True, headers=headers, timeout=90, allow_redirects=True)
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        total = int(r.headers.get("content-length", 0) or 0)
        if "text/html" in ctype:
            console.print("[bold bright_red]Server returned HTML, not video.[/bold bright_red]"); return False
        filepath.parent.mkdir(parents=True, exist_ok=True)
        downloaded = 0; start_time = time.time(); last_draw = 0; first_chunk_data = b""
        with open(temp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk: continue
                if not first_chunk_data:
                    first_chunk_data = chunk[:300]
                    if b"<html" in first_chunk_data.lower() or b"<!doctype" in first_chunk_data.lower():
                        temp_path.unlink(missing_ok=True)
                        console.print("[bold bright_red]Received HTML instead of video.[/bold bright_red]"); return False
                f.write(chunk); downloaded += len(chunk)
                now = time.time()
                if now - last_draw >= 0.15:
                    render_bar(f"Downloading {display_name}", downloaded, total, start_time); last_draw = now
        render_bar(f"Downloading {display_name}", downloaded, total, start_time); finish_bar()
        if downloaded < 1024 * 100:
            console.print(f"[bold bright_red]File too small ({format_bytes(downloaded)}) — likely blocked.[/bold bright_red]")
            temp_path.unlink(missing_ok=True); return False
        if filepath.exists(): filepath.unlink()
        temp_path.replace(filepath); return True
    except Exception as e:
        temp_path.unlink(missing_ok=True)
        console.print(f"[bold bright_red]Download error:[/bold bright_red] {e}"); return False

# ---- Zip ----
def zip_folder_with_progress(folder_path, zip_path):
    files = [p for root, _, fnames in os.walk(folder_path) for fname in fnames if (p := Path(root) / fname).is_file()]
    total_bytes = sum(f.stat().st_size for f in files)
    if zip_path.exists(): zip_path.unlink()
    start_time = time.time(); processed = 0; last_draw = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for fp in (files or []):
            arcname = str(fp.relative_to(folder_path.parent))
            with open(fp, "rb") as src, zf.open(arcname, "w") as dst:
                while True:
                    chunk = src.read(CHUNK_SIZE)
                    if not chunk: break
                    dst.write(chunk); processed += len(chunk)
                    now = time.time()
                    if now - last_draw >= 0.15:
                        render_bar(f"Zipping {folder_path.name}", processed, total_bytes, start_time); last_draw = now
    render_bar(f"Zipping {folder_path.name}", processed, total_bytes, start_time); finish_bar()
    console.print(f"[bold bright_green]✔ Success: {zip_path} created.[/bold bright_green]")
    return zip_path

# ---- Gofile upload ----
def get_gofile_server():
    for ep in ["https://api.gofile.io/servers", "https://api.gofile.io/getServer"]:
        try:
            d = requests.get(ep, timeout=15).json()
            if isinstance(d.get("data"), dict):
                if isinstance(d["data"].get("servers"), list) and d["data"]["servers"]:
                    name = d["data"]["servers"][0].get("name")
                    if name: return name
                if isinstance(d["data"].get("server"), str): return d["data"]["server"]
        except: pass
    return "store1"

def extract_gofile_link(data):
    if not isinstance(data, dict): return None
    d = data.get("data") or {}
    for key in ("downloadPage","pageLink","directLink"):
        v = d.get(key)
        if isinstance(v, str) and v.startswith("http"): return v
    if isinstance(d.get("code"), str): return f"https://gofile.io/d/{d['code']}"
    return None

def upload_to_gofile(file_path):
    server = get_gofile_server()
    urls = [f"https://{server}.gofile.io/contents/uploadfile", f"https://{server}.gofile.io/uploadFile",
            "https://store1.gofile.io/contents/uploadfile", "https://store1.gofile.io/uploadFile"]
    last_error = None
    for url in urls:
        try:
            with open(file_path, "rb") as f:
                encoder = MultipartEncoder(fields={"file": (file_path.name, f, "application/octet-stream")})
                start_time = time.time(); last_draw = [0.0]
                def cb(monitor):
                    now = time.time()
                    if now - last_draw[0] >= 0.15:
                        render_bar(f"Uploading {file_path.name}", monitor.bytes_read, encoder.len, start_time)
                        last_draw[0] = now
                monitor = MultipartEncoderMonitor(encoder, cb)
                resp = requests.post(url, data=monitor,
                                     headers={"Content-Type": monitor.content_type, "Accept": "application/json"},
                                     timeout=3600)
                resp.raise_for_status(); data = resp.json()
            render_bar(f"Uploading {file_path.name}", encoder.len, encoder.len, start_time); finish_bar()
            if str(data.get("status","")).lower() in {"ok","success"} or "data" in data:
                link = extract_gofile_link(data)
                if link: return link
            last_error = f"Gofile error: {json.dumps(data, ensure_ascii=False)}"
        except Exception as e:
            finish_bar(); last_error = str(e)
    console.print(f"[bold bright_red]Upload failed:[/bold bright_red] {last_error}")
    return None

# ---- Folder chooser ----
def list_folders(base_dir):
    return sorted([p for p in base_dir.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in HIDDEN_FOLDERS], key=lambda x: x.name.lower())

def choose_folder(base_dir, preferred_folder=None):
    folders = list_folders(base_dir)
    if not folders:
        console.print("[bold bright_red]No folders found.[/bold bright_red]"); return None
    console.print(f"\n[bold bright_cyan]Folders in {base_dir}[/bold bright_cyan]")
    for i, folder in enumerate(folders, 1):
        mark = "->" if preferred_folder and folder.resolve() == preferred_folder.resolve() else "  "
        console.print(f"[bright_white]{mark} {i} : {folder.name}[/bright_white]")
    console.print("\n[bright_yellow]Choose folder number, full path, or press Enter to use the arrow folder.[/bright_yellow]")
    choice = Prompt.ask("[bold bright_cyan]Folder[/bold bright_cyan]", default="").strip()
    if choice == "" and preferred_folder and preferred_folder.exists(): return preferred_folder
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(folders): return folders[idx]
    s = Path(choice)
    if s.exists() and s.is_dir(): return s
    console.print("[bold bright_red]Invalid selection.[/bold bright_red]"); return None

# ---- Main flow ----
def process_page(url):
    soup = get_soup(url)
    if not soup: return
    title_tag = soup.find("h1", class_="series-title") or soup.find("h1")
    title = clean_name(title_tag.get_text(strip=True) if title_tag else "Video")
    episodes = discover_episodes(url, soup)
    console.print(f"\n[bold bright_green]Title:[/bold bright_green] {title}")
    console.print(f"[bold bright_cyan]Total Episodes Found:[/bold bright_cyan] {len(episodes)}")
    choice = Prompt.ask("[bold bright_cyan]Enter episodes to download (e.g. 1,3-5,all)[/bold bright_cyan]")
    selected_nums = parse_selection(choice, len(episodes))
    if not selected_nums:
        console.print("[bold bright_red]No valid episodes selected.[/bold bright_red]"); return
    output_folder = BASE_DIR / title
    output_folder.mkdir(parents=True, exist_ok=True)
    for num in selected_nums:
        ep = next((e for e in episodes if e["num"] == num), None)
        if not ep: continue
        console.print(f"\n[bold yellow]Processing Episode {num}...[/bold yellow]")
        ep_soup = get_soup(ep["link"])
        video_link = get_video_link(ep_soup)
        if not video_link:
            console.print(f"[bold bright_red]Link not found for Episode {num}[/bold bright_red]"); continue
        file_path = output_folder / f"{num}.mp4"
        ok = download_video(video_link, file_path, f"{num}.mp4", ep["link"])
        if ok: console.print(f"[bold bright_green]Saved:[/bold bright_green] {file_path.name}")
    console.print(f"\n[bold bright_green]Download folder:[/bold bright_green] {output_folder.name}")
    selected_folder = choose_folder(BASE_DIR, preferred_folder=output_folder)
    if not selected_folder: return
    zip_path = BASE_DIR / f"{selected_folder.name}.zip"
    zip_folder_with_progress(selected_folder, zip_path)
    link = upload_to_gofile(zip_path)
    if link:
        console.print("\n[bold bright_green]Link Generated Successfully![/bold bright_green]")
        console.print(f"[bold bright_white on green] Download Link: {link} [/bold bright_white on green]")
    else:
        console.print("[bold bright_red]Could not get link. Please try again later.[/bold bright_red]")

def main():
    console.print(f"[bold bright_cyan]Windows edition — Base folder:[/bold bright_cyan] {BASE_DIR}")
    ffmpeg = get_ffmpeg()
    if ffmpeg:
        console.print(f"[bold bright_green]ffmpeg found:[/bold bright_green] {ffmpeg}")
    else:
        console.print("[bold bright_yellow]ffmpeg not found — binary concat fallback will be used for M3U8 streams.[/bold bright_yellow]")
    while True:
        url = Prompt.ask("\n[bold bright_cyan]DimaKids URL (q to quit)[/bold bright_cyan]").strip()
        if url.lower() == "q": break
        if not url.startswith("http"):
            console.print("[bold bright_red]Please enter a valid URL.[/bold bright_red]"); continue
        process_page(url)

if __name__ == "__main__":
    main()
