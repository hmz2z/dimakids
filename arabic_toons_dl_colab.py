#!/usr/bin/env python3
"""
Arabic Toons / DimaKids Downloader — Google Colab
arabic-toons.com · dimakids.com · Gratuit & Membres · MP4 direct
After download: auto-zip + upload to Gofile.io
"""

# ─────────────────────────────────────────────────────────
# Package bootstrap
# ─────────────────────────────────────────────────────────
import importlib.util, subprocess, sys

_REQUIRED = {
    "requests":          "requests",
    "rich":              "rich",
    "requests_toolbelt": "requests-toolbelt",
    "cloudscraper":      "cloudscraper",
}

def _ensure_packages():
    missing = [pip for mod, pip in _REQUIRED.items()
               if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"[*] Installing: {', '.join(missing)} ...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q",
             "--disable-pip-version-check", *missing]
        )

_ensure_packages()

# ─────────────────────────────────────────────────────────
# Imports
# ─────────────────────────────────────────────────────────
import os, re, time, json, zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import cloudscraper
from requests_toolbelt.multipart.encoder import (
    MultipartEncoder, MultipartEncoderMonitor,
)

# One cloudscraper instance for Cloudflare-protected sites (dimakids.com)
_scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)
_CLOUDFLARE_DOMAINS = {"www.dimakids.com", "dimakids.com"}

from rich.console  import Console
from rich.table    import Table
from rich.panel    import Panel
from rich.rule     import Rule
from rich.prompt   import Prompt, Confirm
from rich.progress import (Progress, BarColumn, TextColumn,
                           TransferSpeedColumn, TimeRemainingColumn,
                           TaskProgressColumn)
from rich.text     import Text
from rich.theme    import Theme
from rich          import box

# ─────────────────────────────────────────────────────────
# Colab detection
# ─────────────────────────────────────────────────────────
def _is_colab() -> bool:
    try:
        import google.colab  # noqa: F401
        return True
    except ImportError:
        return False

IS_COLAB = _is_colab()

# ─────────────────────────────────────────────────────────
# Theme & console
# ─────────────────────────────────────────────────────────
console = Console(theme=Theme({
    "info":    "cyan",
    "success": "bold green",
    "warn":    "yellow",
    "error":   "bold red",
    "title":   "bold magenta",
    "ep":      "bold white",
    "url":     "dim cyan underline",
    "member":  "bold yellow",
    "gofile":  "bold bright_green",
}), highlight=False)

def info(msg):    console.print(f"[info]ℹ  {msg}[/]")
def success(msg): console.print(f"[success]✔  {msg}[/]")
def warn(msg):    console.print(f"[warn]⚠  {msg}[/]")
def error(msg):   console.print(f"[error]✘  {msg}[/]")

# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(_SCRIPT_DIR, "at_session.txt")

# In Colab save to /content; locally use ~/arabic-toons
DEFAULT_OUT = "/content/arabic-toons" if IS_COLAB else os.path.expanduser("~/arabic-toons")

HEADERS_TPL = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "",   # filled dynamically per request
}
CHUNK = 1024 * 512   # 512 KB

# ─────────────────────────────────────────────────────────
# Session management (PHPSESSID)
# ─────────────────────────────────────────────────────────
def load_session() -> str | None:
    if os.path.exists(SESSION_FILE):
        v = Path(SESSION_FILE).read_text().strip()
        return v if v else None
    return None

def save_session(phpsessid: str):
    Path(SESSION_FILE).write_text(phpsessid.strip())

def apply_session(session: requests.Session, phpsessid: str, domain: str):
    session.cookies.set("PHPSESSID", phpsessid, domain=domain)

def ask_for_session() -> str | None:
    console.print()
    console.print(Panel(
        "[bold yellow]🔑 Members-only series[/]\n\n"
        "Log in on arabic-toons.com or dimakids.com, then:\n\n"
        "[dim]1. F12 → Network tab[/]\n"
        "[dim]2. Reload the page (F5)[/]\n"
        "[dim]3. Click any request to the site[/]\n"
        "[dim]4. Headers → Request Headers → copy [bold]Cookie[/] value[/]\n\n"
        "[dim]Example : PHPSESSID=abc123xyz...[/]",
        border_style="yellow", title="[member]Members access[/]",
    ))
    raw = Prompt.ask("[yellow]Cookie / PHPSESSID[/] [dim](or 'cancel')[/]").strip()
    if not raw or raw.lower() in ("cancel", "annuler", "n", ""):
        return None
    m = re.search(r'PHPSESSID=([^;,\s]+)', raw)
    return m.group(1) if m else raw

def _get_or_ask_session(saved_session: list) -> str | None:
    if saved_session:
        return saved_session[0]
    phpsessid = load_session()
    if phpsessid:
        info("Session loaded from cache.")
        saved_session.append(phpsessid)
        return phpsessid
    phpsessid = ask_for_session()
    if phpsessid:
        save_session(phpsessid)
        saved_session.append(phpsessid)
    return phpsessid

# ─────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────
def _headers(base_url: str) -> dict:
    h = dict(HEADERS_TPL)
    h["Referer"] = base_url + "/"
    return h

def _client(domain: str, session: requests.Session):
    """Return cloudscraper for Cloudflare-protected domains, requests.Session otherwise."""
    return _scraper if domain in _CLOUDFLARE_DOMAINS else session

def get_page(url: str, session: requests.Session, base_url: str) -> str:
    url    = url.split("#")[0]
    domain = urlparse(url).netloc
    r = _client(domain, session).get(url, headers=_headers(base_url), timeout=30)
    r.raise_for_status()
    return r.content.decode("utf-8", errors="replace")

def fetch_play_url(series_id: str, ep_id: str,
                   session: requests.Session, base_url: str) -> str | None:
    url    = f"{base_url}/{series_id}/{ep_id}-play.html"
    domain = urlparse(url).netloc
    try:
        r    = _client(domain, session).get(url, headers=_headers(base_url), timeout=15)
        text = r.text.strip()
        if not text or text == "login":
            return None
        if "foupix.com" in text or text.startswith("http"):
            return text + f"&_={int(time.time() * 1000)}"
    except Exception:
        pass
    return None

# ─────────────────────────────────────────────────────────
# Scrapers
# ─────────────────────────────────────────────────────────
def get_canonical(html: str) -> str | None:
    m = re.search(r'rel="canonical"\s+href="([^"]+)"', html)
    return m.group(1) if m else None

def extract_series_name(html: str) -> str:
    m = re.search(r'<h1[^>]*>\s*([^<]+?)\s*</h1>', html)
    if m:
        return m.group(1).strip()
    m = re.search(r'<title>([^<]+)</title>', html)
    if m:
        t = re.sub(r'\s*[-|]\s*(الحلقة|arabic.toons|dimakids|streaming).*',
                   '', m.group(1).strip(), flags=re.IGNORECASE)
        return t.strip() or "serie"
    return "serie"

def extract_episode_list(html: str, series_id: str) -> list[dict]:
    pattern = rf'href="([^"]*{re.escape(series_id)}-(\d+)\.html)[^"]*"\s+title="([^"]*)"'
    seen, episodes = set(), []
    for path, _, title in re.findall(pattern, html):
        path = path.split("#")[0]
        if path in seen:
            continue
        seen.add(path)
        m = re.search(r'الحلقة\s+(\d+)', title) or re.search(r'(\d+)\s*$', title)
        episodes.append({"num": int(m.group(1)) if m else 0,
                         "title": title.strip(), "path": path, "play": False})
    episodes.sort(key=lambda e: e["num"])
    for i, ep in enumerate(episodes, 1):
        if ep["num"] == 0:
            ep["num"] = i
    return episodes

def extract_episode_list_member(html: str, series_id: str) -> list[dict]:
    pattern = rf'href="[/]?{re.escape(series_id)}[/](\d+)-play\.html"[^>]*title="([^"]*)"'
    seen, episodes = set(), []
    for ep_id, title in re.findall(pattern, html):
        if ep_id in seen:
            continue
        seen.add(ep_id)
        m = re.search(r'الحلقة\s+(\d+)', title) or re.search(r'(\d+)\s*$', title)
        episodes.append({"num": int(m.group(1)) if m else 0,
                         "title": title.strip(),
                         "path": f"{series_id}/{ep_id}-play.html",
                         "ep_id": ep_id, "play": True})
    if not episodes:
        for ep_id in re.findall(rf'{re.escape(series_id)}[/](\d+)-play', html):
            if ep_id not in seen:
                seen.add(ep_id)
                episodes.append({"num": 0, "title": f"Episode {ep_id}",
                                  "path": f"{series_id}/{ep_id}-play.html",
                                  "ep_id": ep_id, "play": True})
    episodes.sort(key=lambda e: e["num"])
    for i, ep in enumerate(episodes, 1):
        if ep["num"] == 0:
            ep["num"] = i
    return episodes

def extract_video_src(html: str) -> str | None:
    patterns = [
        r'const\s+videoSrc\s*=\s*"([^"]+)"',
        r"const\s+videoSrc\s*=\s*'([^']+)'",
        r'"videoSrc"\s*:\s*"([^"]+)"',
        r'file\s*:\s*"([^"]+\.mp4[^"]*)"',
        r"file\s*:\s*'([^']+\.mp4[^']*)'",
        r'src\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
        r"src\s*:\s*'(https?://[^']+\.mp4[^']*)'",
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            url = m.group(1)
            return url if "_=" in url else url + f"&_={int(time.time() * 1000)}"
    return None

# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────
def sanitize(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()

def detect_season(series_name: str) -> int:
    m = re.search(r'(?:الجزء|الموسم|الفصل)\s+(\d+)', series_name)
    return int(m.group(1)) if m else 1

def is_series_url(url: str) -> bool:
    return bool(re.search(r'-(anime|movies|series|cartoon)-streaming', url)) or \
           bool(re.search(r'serie-\d+', url))

def base_url_from(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"

# ─────────────────────────────────────────────────────────
# Episode selection
# ─────────────────────────────────────────────────────────
def parse_ep_selection(raw: str, max_ep: int) -> set[int]:
    raw = raw.strip().lower()
    if raw in ("all", "tout", "*", ""):
        return set(range(1, max_ep + 1))
    nums: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if "-" in part:
            a, _, b = part.partition("-")
            try: nums.update(range(int(a), int(b) + 1))
            except ValueError: pass
        else:
            try: nums.add(int(part))
            except ValueError: pass
    return nums

# ─────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────
def download_episode(url: str, dest: Path, label: str,
                     session: requests.Session, base_url: str) -> bool:
    domain = urlparse(url).netloc
    r = _client(domain, session).get(url, headers=_headers(base_url), stream=True, timeout=60)
    if r.status_code == 302 or not r.ok:
        return False
    total = int(r.headers.get("Content-Length", 0))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with Progress(
        TextColumn(f"  [ep]{label}[/]"),
        BarColumn(bar_width=36, style="cyan", complete_style="bold cyan"),
        TaskProgressColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console, transient=False,
    ) as prog:
        task = prog.add_task("", total=total or None)
        try:
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=CHUNK):
                    f.write(chunk)
                    prog.update(task, advance=len(chunk))
        except KeyboardInterrupt:
            tmp.unlink(missing_ok=True)
            raise
    tmp.rename(dest)
    return True

def get_video_url_for_ep(ep: dict, series_id: str,
                          session: requests.Session, base_url: str) -> str | None:
    if ep.get("play"):
        return fetch_play_url(series_id, ep["ep_id"], session, base_url)
    try:
        ep_html = get_page(urljoin(base_url + "/", ep["path"]), session, base_url)
        url = extract_video_src(ep_html)
        if url:
            return url
        ep_id_m = re.search(r'-(\d+)\.html$', ep["path"])
        if ep_id_m:
            return fetch_play_url(series_id, ep_id_m.group(1), session, base_url)
    except Exception:
        pass
    return None

# ─────────────────────────────────────────────────────────
# Gofile upload
# ─────────────────────────────────────────────────────────
def _gofile_server() -> str:
    try:
        r = requests.get("https://api.gofile.io/servers", timeout=10)
        data = r.json()
        servers = data.get("data", {}).get("servers", [])
        if servers:
            return servers[0]["name"]
    except Exception:
        pass
    return "store1"

def _zip_folder(folder: Path, zip_path: Path):
    files = [f for f in folder.rglob("*") if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    done  = 0
    with Progress(
        TextColumn("  [ep]Zipping…[/]"),
        BarColumn(bar_width=36, style="magenta", complete_style="bold magenta"),
        TaskProgressColumn(),
        console=console, transient=False,
    ) as prog:
        task = prog.add_task("", total=total or None)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                             allowZip64=True) as zf:
            for f in files:
                arcname = str(f.relative_to(folder.parent))
                with open(f, "rb") as src, zf.open(arcname, "w") as dst:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        dst.write(chunk)
                        done += len(chunk)
                        prog.update(task, advance=len(chunk))

def upload_to_gofile(zip_path: Path) -> str | None:
    server = _gofile_server()
    upload_url = f"https://{server}.gofile.io/contents/uploadfile"
    info(f"Uploading to Gofile ({server})…")
    try:
        with open(zip_path, "rb") as f:
            encoder = MultipartEncoder(
                fields={"file": (zip_path.name, f, "application/octet-stream")}
            )
            start = time.time()
            last  = [0.0]

            with Progress(
                TextColumn("  [ep]Uploading…[/]"),
                BarColumn(bar_width=36, style="green", complete_style="bold green"),
                TaskProgressColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console, transient=False,
            ) as prog:
                task = prog.add_task("", total=encoder.len)

                def _cb(monitor):
                    prog.update(task, completed=monitor.bytes_read)

                mon = MultipartEncoderMonitor(encoder, _cb)
                r = requests.post(
                    upload_url,
                    data=mon,
                    headers={"Content-Type": mon.content_type},
                    timeout=3600,
                )
                r.raise_for_status()

        data = r.json()
        if str(data.get("status", "")).lower() in ("ok", "success"):
            d = data.get("data", {})
            for key in ("downloadPage", "pageLink", "directLink"):
                if isinstance(d.get(key), str) and d[key].startswith("http"):
                    return d[key]
            if isinstance(d.get("code"), str):
                return f"https://gofile.io/d/{d['code']}"
    except Exception as e:
        error(f"Upload error: {e}")
    return None

def zip_and_upload(out_dir: Path, series_name: str):
    """Zip the series folder and upload it to Gofile."""
    console.print()
    console.print(Rule("[green]Gofile Upload[/]"))

    # Zip the series root folder (parent of Season XX)
    series_folder = out_dir.parent
    zip_path = series_folder.parent / f"{sanitize(series_name)}.zip"

    info(f"Creating zip: [url]{zip_path.name}[/]")
    _zip_folder(series_folder, zip_path)
    success(f"Zip created ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")

    link = upload_to_gofile(zip_path)
    console.print()
    if link:
        console.print(Panel(
            f"[gofile]✔  Upload complete![/]\n\n"
            f"[bold white]Download link:[/]\n"
            f"[bold cyan underline]{link}[/]",
            border_style="bright_green", title="[gofile]Gofile.io[/]",
            padding=(1, 4),
        ))
        # Clean up zip after upload
        zip_path.unlink(missing_ok=True)
    else:
        error("Upload failed — zip file kept at:")
        info(f"[url]{zip_path}[/]")

# ─────────────────────────────────────────────────────────
# Banner & episode table
# ─────────────────────────────────────────────────────────
def banner():
    colab_tag = "  [dim]· Google Colab[/]" if IS_COLAB else ""
    console.print()
    console.print(Panel.fit(
        Text.from_markup(
            "[bold cyan]📺  ARABIC TOONS / DIMAKIDS DOWNLOADER[/]\n"
            f"[dim]arabic-toons.com  ·  dimakids.com  ·  Gofile upload{colab_tag}[/]"
        ),
        border_style="cyan", padding=(0, 4),
    ))
    console.print()

def print_episode_table(episodes: list[dict], series_name: str, member: bool = False):
    badge = "  [member]🔒 Members[/]" if member else ""
    t = Table(
        title=f"[title]{series_name}[/]{badge}  [dim]({len(episodes)} episodes)[/]",
        box=box.ROUNDED, border_style="cyan", show_lines=False, padding=(0, 1),
    )
    t.add_column("#",     style="bold cyan", justify="right", width=4)
    t.add_column("Title", style="white",     no_wrap=False,   min_width=30)
    for ep in episodes[:50]:
        t.add_row(str(ep["num"]), ep["title"])
    if len(episodes) > 50:
        t.add_row("…", f"[dim]+ {len(episodes) - 50} more[/]")
    console.print(t)
    console.print()

# ─────────────────────────────────────────────────────────
# Core flow
# ─────────────────────────────────────────────────────────
def run_download(session: requests.Session, saved_session: list):
    console.print(Rule("[cyan]New download[/]"))
    console.print()
    url = Prompt.ask("[cyan]URL[/] [dim](arabic-toons.com or dimakids.com)[/]").strip()
    if not url:
        return
    if not url.startswith("http"):
        url = "https://" + url

    base_url = base_url_from(url)
    domain   = urlparse(url).netloc  # e.g. www.arabic-toons.com

    console.print()
    info(f"Site: [url]{base_url}[/]")
    info("Analysing page…")

    # ── Single episode ────────────────────────────────────
    if not is_series_url(url):
        try:
            html = get_page(url, session, base_url)
        except Exception as e:
            error(f"Could not load page: {e}"); return

        series_name = extract_series_name(html)
        m           = re.search(r'الحلقة\s+(\d+)', html)
        ep_num      = int(m.group(1)) if m else 0
        video_url   = extract_video_src(html)

        if not video_url:
            sid_m = re.search(r'-(\d+)-', url)
            ep_m  = re.search(r'-(\d+)\.html$', url)
            if sid_m and ep_m:
                video_url = fetch_play_url(sid_m.group(1), ep_m.group(1), session, base_url)
                if not video_url:
                    phpsessid = _get_or_ask_session(saved_session)
                    if phpsessid:
                        apply_session(session, phpsessid, domain)
                        video_url = fetch_play_url(sid_m.group(1), ep_m.group(1), session, base_url)

        if not video_url:
            error("No video source found."); return

        success(f"Series  : {series_name}")
        success(f"Episode : {ep_num or '?'}")
        console.print()

        default_s = detect_season(series_name)
        season  = int(Prompt.ask("[cyan]Season number[/]", default=str(default_s)))
        base    = Path(Prompt.ask("[cyan]Root folder[/]", default=DEFAULT_OUT))
        out_dir = base / sanitize(series_name) / f"Season {season:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)

        tag   = f"S{season:02d}E{ep_num:02d}" if ep_num else "S01E00"
        fname = f"{sanitize(series_name)} - {tag}.mp4"
        dest  = out_dir / fname

        if dest.exists():
            if not Confirm.ask(f"[warn]{fname}[/] already exists — overwrite?", default=False):
                warn("Cancelled."); return

        console.print(Rule("[cyan]Downloading[/]"))
        ok = download_episode(video_url, dest, tag, session, base_url)
        if not ok:
            warn("Token expired, retrying…")
            video_url = extract_video_src(get_page(url, session, base_url))
            if video_url:
                ok = download_episode(video_url, dest, tag, session, base_url)

        console.print()
        if ok:
            success(f"Saved: [url]{dest}[/]")
            if IS_COLAB or Confirm.ask("[green]Upload to Gofile.io?[/]", default=IS_COLAB):
                zip_and_upload(out_dir, series_name)
        else:
            error("Download failed.")
        return

    # ── Series ────────────────────────────────────────────
    sid_m = re.search(r'-(\d+)-(anime|movies|series|cartoon)-streaming', url)
    if not sid_m:
        error("Invalid URL: series ID not found."); return
    series_id = sid_m.group(1)

    try:
        html = get_page(url, session, base_url)
    except Exception as e:
        error(f"Could not load page: {e}"); return

    member_mode = False

    if series_id not in html:
        warn("This series requires an account (members only).")
        phpsessid = _get_or_ask_session(saved_session)
        if not phpsessid:
            error("Session cancelled."); return
        apply_session(session, phpsessid, domain)
        try:
            html = get_page(url, session, base_url)
        except Exception as e:
            error(f"Error after login: {e}"); return
        if series_id not in html:
            error("Still inaccessible — please check your session."); return
        member_mode = True
        info("Session active — members access OK ✔")

    canonical = get_canonical(html)
    if canonical and canonical != url and \
       re.search(r'-(anime|movies|series|cartoon)-streaming', canonical):
        info(f"Canonical: {canonical}")
        try:
            html = get_page(canonical, session, base_url)
            url  = canonical
        except Exception:
            pass

    series_name = extract_series_name(html)
    episodes    = extract_episode_list(html, series_id)

    if not episodes and member_mode:
        episodes = extract_episode_list_member(html, series_id)

    # Movie / single-video page: no episode links, video is on the series page itself
    if not episodes:
        video_url = extract_video_src(html)
        if video_url:
            info("Single video detected (movie) — downloading directly…")
            season  = 1
            base    = Path(Prompt.ask("[cyan]Root folder[/]", default=DEFAULT_OUT))
            out_dir = base / sanitize(series_name) / f"Season {season:02d}"
            out_dir.mkdir(parents=True, exist_ok=True)
            tag   = f"S{season:02d}E01"
            fname = f"{sanitize(series_name)} - {tag}.mp4"
            dest  = out_dir / fname
            console.print(Rule("[cyan]Downloading[/]"))
            ok = download_episode(video_url, dest, tag, session, base_url)
            console.print()
            if ok:
                success(f"Saved: [url]{dest}[/]")
                do_upload = IS_COLAB or Confirm.ask("[green]Upload to Gofile.io?[/]", default=False)
                if do_upload:
                    zip_and_upload(out_dir, series_name)
            else:
                error("Download failed.")
            return
        error("No episodes and no video source found on this page."); return

    success(f"Series   : [title]{series_name}[/]")
    success(f"Episodes : {len(episodes)} found")
    console.print()
    print_episode_table(episodes, series_name, member=member_mode)

    max_ep  = max(ep["num"] for ep in episodes)
    sel_raw = Prompt.ask(
        "[cyan]Episodes to download[/] [dim](all · 1-5 · 1,3,5)[/]",
        default="all"
    )
    to_dl = [ep for ep in episodes if ep["num"] in parse_ep_selection(sel_raw, max_ep)]

    if not to_dl:
        warn("No matching episodes."); return

    info(f"{len(to_dl)} episode(s) selected")
    console.print()

    default_s = detect_season(series_name)
    season  = int(Prompt.ask("[cyan]Season number[/]", default=str(default_s)))
    base    = Path(Prompt.ask("[cyan]Root folder[/]", default=DEFAULT_OUT))
    out_dir = base / sanitize(series_name) / f"Season {season:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    info(f"Folder   : [url]{out_dir}[/]")
    console.print()

    console.print(Rule("[cyan]Downloading[/]"))
    console.print()

    done = skipped = failed = 0

    for ep in to_dl:
        tag   = f"S{season:02d}E{ep['num']:02d}"
        fname = f"{sanitize(series_name)} - {tag}.mp4"
        dest  = out_dir / fname

        if dest.exists():
            console.print(f"  [success]✔[/] [dim]{tag} already present — skipped[/]")
            skipped += 1
            continue

        video_url = get_video_url_for_ep(ep, series_id, session, base_url)

        if not video_url:
            console.print(f"  [error]✘[/] {tag} — source not found")
            failed += 1
            continue

        ok = download_episode(video_url, dest, tag, session, base_url)
        if not ok:
            warn(f"  Token expired for {tag} — retrying…")
            video_url = get_video_url_for_ep(ep, series_id, session, base_url)
            if video_url:
                ok = download_episode(video_url, dest, f"{tag} (retry)", session, base_url)

        if ok: done += 1
        else:
            console.print(f"  [error]✘[/] {tag} — failed")
            failed += 1

        time.sleep(0.8)

    console.print()
    console.print(Rule("[cyan]Summary[/]"))
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column("", style="bold cyan", width=22)
    t.add_column("")
    t.add_row("✔  Downloaded",      f"[success]{done}[/]")
    t.add_row("⏭  Already present", f"[dim]{skipped}[/]")
    t.add_row("✘  Failed",          f"[error]{failed}[/]" if failed else "[dim]0[/]")
    t.add_row("📁  Folder",         f"[url]{out_dir}[/]")
    console.print(t)
    console.print()

    if done > 0:
        do_upload = IS_COLAB or Confirm.ask("[green]Upload to Gofile.io?[/]", default=False)
        if do_upload:
            zip_and_upload(out_dir, series_name)

# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────
def main():
    banner()
    session       = requests.Session()
    saved_session = []

    existing = load_session()
    if existing:
        # Session domain will be set properly when a URL is entered
        saved_session.append(existing)
        info("Member session loaded ✔  [dim](at_session.txt)[/]")
    else:
        info("No member session — free series only")
        info("[dim]Session will be requested if needed[/]")
    console.print()

    if IS_COLAB:
        info(f"[gofile]Colab mode[/] — files saved to [url]{DEFAULT_OUT}[/]")
        info("[gofile]After download: auto-zip + upload to Gofile.io[/]")
        console.print()

    while True:
        try:
            run_download(session, saved_session)
        except KeyboardInterrupt:
            console.print()
            warn("Interrupted.")
        console.print()
        if not Confirm.ask("[cyan]New download?[/]", default=True):
            break

    console.print()
    console.print("[dim]Goodbye 👋[/]")
    console.print()

if __name__ == "__main__":
    main()
