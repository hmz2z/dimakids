# pip install cloudscraper beautifulsoup4 rich

import cloudscraper
import json
import re
import os
import math
import time
from bs4 import BeautifulSoup
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
    DownloadColumn
)

BASE_URL = "https://anime3rb.com"
QUALITY_PRIORITY = ["1080p", "720p", "480p"]

console = Console()
scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "desktop": True}
)


def test_connection():
    console.print("[bright_cyan]Testing connection to anime3rb.com...[/bright_cyan]")
    try:
        r = scraper.get(BASE_URL, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        csrf = soup.find("meta", {"name": "csrf-token"})
        lw = soup.find("form", {"wire:id": True})
        if csrf and lw:
            console.print("[bold bright_green]✓ Connection OK - Cloudflare bypassed, site loaded successfully.[/bold bright_green]")
            return True
        else:
            console.print("[yellow]⚠ Site loaded but missing expected elements.[/yellow]")
            return True
    except Exception as e:
        console.print(f"[bold red]✗ Connection failed: {e}[/bold red]")
        return False


def get_soup(url):
    try:
        response = scraper.get(url, timeout=20, allow_redirects=True)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        console.log(f"[red]Error fetching {url}: {e}[/red]")
        return None


def get_player_url(episode_url, soup=None):
    if soup is None:
        soup = get_soup(episode_url)
    if not soup:
        return None
    for el in soup.find_all(attrs={"wire:id": True}):
        snap_str = el.get("wire:snapshot", "")
        if "video_url" in snap_str:
            try:
                snap = json.loads(snap_str)
                return snap.get("data", {}).get("video_url")
            except (json.JSONDecodeError, KeyError):
                continue
    return None


def get_video_sources(player_url, referer_url=""):
    try:
        headers = {"Referer": referer_url} if referer_url else {}
        r = scraper.get(player_url, timeout=20, headers=headers)
        r.raise_for_status()
        matches = re.findall(r"var\s+video_sources\s*=\s*(\[[^\]]*\])", r.text)
        if len(matches) >= 2:
            raw = matches[1]
        elif matches:
            raw = matches[0]
        else:
            return []
        raw = raw.replace("\\/", "/")
        sources = json.loads(raw)
        return [s for s in sources if s.get("src") and not s.get("premium", False)]
    except Exception as e:
        console.log(f"[red]Error getting video sources: {e}[/red]")
        return []


def get_episode_download_links(episode_url, soup=None):
    player_url = get_player_url(episode_url, soup)
    if not player_url:
        return {}
    sources = get_video_sources(player_url, episode_url)
    links = {}
    for s in sources:
        label = s.get("label", "")
        src = s.get("src", "")
        if label and src:
            links[label] = src
    return links


def parse_episode_selection(user_input, total_episodes):
    selected = set()
    if user_input.lower() == "all":
        return list(range(1, total_episodes + 1))
    for part in user_input.replace(" ", "").split(","):
        try:
            if "-" in part:
                start, end = map(int, part.split("-"))
                for i in range(start, end + 1):
                    if 1 <= i <= total_episodes:
                        selected.add(i)
            else:
                num = int(part)
                if 1 <= num <= total_episodes:
                    selected.add(num)
        except ValueError:
            console.print(f"[yellow]Skipping invalid input '{part}'[/yellow]")
    return sorted(list(selected))


def format_size(size_bytes):
    if not isinstance(size_bytes, (int, float)) or size_bytes <= 0:
        return "N/A"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"


def load_json(filepath):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"anime_title": "", "episodes": {}}


def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def download_file(url, filepath, filename_for_display, referer=""):
    try:
        headers = {"Referer": referer} if referer else {}
        with scraper.get(url, stream=True, timeout=300, allow_redirects=True, headers=headers) as r:
            r.raise_for_status()
            total_size = int(r.headers.get("content-length", 0))
            with Progress(
                TextColumn("[bold bright_cyan]{task.fields[filename]}", justify="left"),
                BarColumn(bar_width=None),
                "[progress.percentage]{task.percentage:>3.1f}%",
                "•",
                DownloadColumn(),
                "•",
                TransferSpeedColumn(),
                "•",
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task("download", total=total_size, filename=filename_for_display)
                with open(filepath, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))
            downloaded_size = os.path.getsize(filepath)
            if total_size != 0 and downloaded_size < total_size:
                raise Exception("Incomplete download")
            return True
    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        raise e


def scrape_anime_page(anime_data):
    title, url = anime_data["title"], anime_data["link"]
    if not url.startswith("http"):
        url = f"{BASE_URL}{url}"
    console.print(f"\n[bright_cyan]Scraping details for: [bold]{title}[/bold]...[/bright_cyan]")
    soup = get_soup(url)
    if not soup:
        return

    episodes_list = []
    ep_links = soup.find_all("a", href=re.compile(r"/episode/"))
    for a in ep_links:
        href = a.get("href", "")
        if not href.startswith("http"):
            href = f"{BASE_URL}{href}"
        m = re.search(r"/episode/.+/(\d+)", href)
        if m:
            ep_num = int(m.group(1))
            episodes_list.append({"num": ep_num, "link": href})
    episodes_list.sort(key=lambda x: x["num"])

    status_td = soup.find("td", string=re.compile(r"\u0627\u0644\u062d\u0627\u0644\u0629:"))
    status = status_td.find_next_sibling("td").text.strip() if status_td and status_td.find_next_sibling("td") else "N/A"
    is_movie = len(episodes_list) == 1 and "( فيلم )" in (soup.find("h1").text if soup.find("h1") else "")

    panel_text = Text(f"Status: {status}\n", style="bold bright_green", justify="center")
    panel_text.append(f"Total Episodes Found: {len(episodes_list)}", style="yellow")
    console.print(Panel(panel_text, title=f"[bold bright_magenta]{title}[/bold bright_magenta]", border_style="bright_blue", expand=False))

    if not episodes_list:
        console.print("[bold red]Could not find any episodes to select.[/bold red]")
        return

    if is_movie:
        selected_numbers = [1]
    else:
        while True:
            user_choice = console.input("[bold]Enter episode numbers ([bright_cyan]e.g., 1, 3-5, all[/bright_cyan]) or '[magenta]b[/magenta]' to go back: [/bold]")
            if user_choice.lower() == "b":
                return
            selected_numbers = parse_episode_selection(user_choice, len(episodes_list))
            if selected_numbers:
                break
            console.print("[bold red]No valid episodes selected.[/bold red]")

    confirm = console.input("Proceed with download? ([bold bright_green]y[/bold bright_green]/[bold red]n[/bold red]): ")
    if confirm.lower() != "y":
        console.print("[yellow]Download cancelled.[/yellow]")
        return

    safe_folder_name = re.sub(r'[\\/*?:"<>|]', "", title)
    os.makedirs(safe_folder_name, exist_ok=True)
    json_filepath = os.path.join(safe_folder_name, "links.json")
    json_data = load_json(json_filepath)
    json_data["anime_title"] = title

    console.print(f"\n[yellow]Downloads will be saved in folder: './{safe_folder_name}/'[/yellow]")
    console.print("[dim]Mode: Extract → Save → Download (one episode at a time, links stay fresh)[/dim]\n")

    downloaded_eps, failed_eps, skipped_eps = [], [], []
    zfill_width = max(len(str(len(episodes_list))), 2)
    total_selected = len(selected_numbers)

    for idx, ep_num in enumerate(selected_numbers, 1):
        ep_data = next((ep for ep in episodes_list if ep["num"] == ep_num), None)
        if not ep_data:
            console.print(f"[yellow]⚠ Episode {ep_num} not found in list, skipping.[/yellow]")
            skipped_eps.append(ep_num)
            continue

        filename = f"{ep_num:0{zfill_width}d}.mp4"
        filepath = os.path.join(safe_folder_name, filename)

        if os.path.exists(filepath):
            existing_size = os.path.getsize(filepath)
            if existing_size > 1048576:
                console.print(f"[dim]  [{idx}/{total_selected}] Ep {ep_num:0{zfill_width}d} already exists ({format_size(existing_size)}), skipping.[/dim]")
                downloaded_eps.append(ep_num)
                continue

        console.rule(f"[bold bright_cyan]Ep {ep_num:0{zfill_width}d} [{idx}/{total_selected}][/bold bright_cyan]")

        max_retries = 3
        attempt = 0
        download_successful = False

        while not download_successful and attempt < max_retries:
            attempt += 1
            try:
                console.print(f"  [bright_cyan]Extracting link for Episode {ep_num}...[/bright_cyan]")
                links = get_episode_download_links(ep_data["link"])

                if not links:
                    console.print(f"  [red]✗ No links found for Episode {ep_num}.[/red]")
                    if attempt < max_retries:
                        console.print(f"  [yellow]Retrying ({attempt}/{max_retries})...[/yellow]")
                        time.sleep(3)
                        continue
                    break

                best_url, best_quality = None, None
                for quality in QUALITY_PRIORITY:
                    if quality in links:
                        best_url, best_quality = links[quality], quality
                        break

                if not best_url:
                    best_quality = list(links.keys())[0]
                    best_url = links[best_quality]

                json_data["episodes"][str(ep_num)] = links
                save_json(json_filepath, json_data)
                console.print(f"  [bright_green]✓ Link saved ({best_quality}) → links.json[/bright_green]")

                filename = f"{ep_num:0{zfill_width}d}-{best_quality}.mp4"
                filepath = os.path.join(safe_folder_name, filename)
                console.print(f"  [bright_cyan]Downloading {best_quality}...[/bright_cyan]")
                download_successful = download_file(best_url, filepath, filename, ep_data["link"])

            except Exception as e:
                console.print(f"  [red]❌ Error: {e}[/red]")
                if attempt < max_retries:
                    console.print(f"  [yellow]Retrying ({attempt}/{max_retries})...[/yellow]")
                    time.sleep(3)

        if download_successful:
            downloaded_eps.append(ep_num)
            console.print(f"  [bold bright_green]✅ Episode {ep_num} done.[/bold bright_green]")
        else:
            failed_eps.append(ep_num)

        time.sleep(1)

    console.print("\n--- [bold]Final Download Summary[/bold] ---")
    console.print(f"  [bright_cyan]Total selected: {total_selected}[/bright_cyan]")
    if downloaded_eps:
        console.print(f"  [bright_green]✅ Downloaded ({len(downloaded_eps)}): {', '.join(map(str, sorted(downloaded_eps)))}[/bright_green]")
    if failed_eps:
        console.print(f"  [red]❌ Failed ({len(failed_eps)}): {', '.join(map(str, sorted(failed_eps)))}[/red]")
    if skipped_eps:
        console.print(f"  [yellow]⚠ Skipped ({len(skipped_eps)}): {', '.join(map(str, sorted(skipped_eps)))}[/yellow]")
    console.print(f"  [dim]Links saved to: {json_filepath}[/dim]")


def search_and_select_anime(query):
    console.print(f"\n[yellow]Searching for '[bold bright_cyan]{query}[/bold bright_cyan]'...[/yellow]")
    soup = get_soup(BASE_URL)
    if not soup:
        return

    anime_cards = []
    try:
        lw = soup.find("form", {"wire:id": True})
        if lw:
            snapshot_str = lw.get("wire:snapshot")
            csrf = soup.find("meta", {"name": "csrf-token"})["content"]
            payload = {
                "_token": csrf,
                "components": [
                    {
                        "snapshot": snapshot_str,
                        "updates": {"query": query},
                        "calls": [],
                    }
                ],
            }
            r = scraper.post(
                f"{BASE_URL}/livewire/update",
                headers={
                    "Content-Type": "application/json",
                    "X-Livewire": "true",
                    "X-CSRF-TOKEN": csrf,
                },
                data=json.dumps(payload),
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            html = data["components"][0]["effects"].get("html", "")
            if html:
                anime_cards = BeautifulSoup(html, "html.parser").find_all("a", class_="simple-title-card")
    except Exception:
        pass

    if not anime_cards:
        console.print("[dim]Livewire search failed, trying URL fallback...[/dim]")
        search_soup = get_soup(f"{BASE_URL}/search?q={query.replace(' ', '+')}")
        if search_soup:
            anime_cards = search_soup.select("a.simple-title-card")

    if not anime_cards:
        console.print(f"[bold red]No results found for '{query}'.[/bold red]")
        return

    results_list = []
    for card in anime_cards:
        h4 = card.find("h4")
        title = h4.text.strip() if h4 else "Unknown"
        link = card.get("href", "")
        if not link.startswith("http"):
            link = f"{BASE_URL}{link}"
        results_list.append({"title": title, "link": link})

    table = Table(
        title=f"Search Results for '{query}'",
        box=box.DOUBLE_EDGE,
        header_style="bold bright_magenta",
        border_style="bright_blue",
    )
    table.add_column("No.", style="dim", justify="center", width=4)
    table.add_column("Title", style="bold bright_cyan", width=60, overflow="ellipsis", no_wrap=True)
    for i, item in enumerate(results_list, 1):
        table.add_row(f"{i:02d}", item["title"])
    console.print(table)

    while True:
        try:
            choice = console.input("[bold]Enter number (or '[magenta]b[/magenta]' to go back): [/bold]")
            if choice.lower() == "b":
                return
            selection = int(choice)
            if 1 <= selection <= len(results_list):
                scrape_anime_page(results_list[selection - 1])
                return
            else:
                console.print(f"[bold red]Enter a number between 1 and {len(results_list)}.[/bold red]")
        except ValueError:
            console.print("[bold red]Invalid input. Enter a number.[/bold red]")


def main():
    banner_text = """
 █████╗ ███╗   ██╗██╗███╗   ███╗███████╗██████╗ ██████╗ ██████╗ 
██╔══██╗████╗  ██║██║████╗ ████║██╔════╝╚════██╗██╔══██╗██╔══██╗
███████║██╔██╗ ██║██║██╔████╔██║█████╗   █████╔╝██████╔╝██████╔╝
██╔══██║██║╚██╗██║██║██║╚██╔╝██║██╔══╝   ╚═══██╗██╔══██╗██╔══██╗
██║  ██║██║ ╚████║██║██║ ╚═╝ ██║███████╗██████╔╝██║  ██║██████╔╝
╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝╚═╝     ╚═╝╚══════╝╚═════╝ ╚═╝  ╚═╝╚═════╝ 
""" 
    byline_text = "by Hamza v 2.0.1 - Line by Line"

    console.print(banner_text, style="bold bright_cyan")
    console.print(byline_text, style="bold yellow", justify="center")
    console.print()

    if not test_connection():
        console.print("[bold red]Cannot proceed without connection. Exiting.[/bold red]")
        return

    while True:
        search_query = console.input("\n[bold]Enter anime name to search (or '[magenta]q[/magenta]' to quit): [/bold]")
        if search_query.lower() in ["q", "quit"]:
            break
        if search_query:
            search_and_select_anime(search_query)
        else:
            console.print("[bold red]Search query cannot be empty.[/bold red]")

    console.print("\n[bold yellow]Thank you for using the scraper. Goodbye![/bold yellow]")


if __name__ == "__main__":
    main()
