
import requests
import json
import re
import os
import shutil
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text
from rich.align import Align

# --- Global objects ---
console = Console()
session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})


def get_soup(url):
    """Fetches a URL and returns a BeautifulSoup object."""
    try:
        response = session.get(url, timeout=15, allow_redirects=True)
        response.raise_for_status()
        response.encoding = 'utf-8'
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        console.log(f"[red]Error fetching page from {url}: {e}[/red]")
        return None

def get_m3u8_link_from_js(soup):
    """Parses JavaScript variables from a BeautifulSoup object to reconstruct the .m3u8 link."""
    try:
        if not soup: return None
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'stream.foupix.com' in script.string:
                script_content = script.string
                match = re.search(r'const\s+\w+\s*=\s*(\{[\s\S]*?\});', script_content)
                if match:
                    js_object_str = match.group(1)
                    protocol = re.search(r'jC1kO:\s*"([^"]+)"', js_object_str)
                    domain = re.search(r'hF3nV:\s*"([^"]+)"', js_object_str)
                    path = re.search(r'iA5pX:\s*"([^"]+)"', js_object_str)
                    params = re.search(r'tN4qY:\s*"([^"]+)"', js_object_str)
                    if all([protocol, domain, path, params]):
                        return f"{protocol.group(1)}://{domain.group(1)}/{path.group(1)}?{params.group(1)}"
        return None
    except Exception as e:
        console.log(f"[red]Error parsing JavaScript: {e}[/red]")
        return None

def format_speed(byte_speed):
    """Formats bytes per second into a human-readable string."""
    if byte_speed < 1024:
        return f"{byte_speed:.1f} B/s"
    elif byte_speed < 1024 * 1024:
        return f"{byte_speed / 1024:.1f} KB/s"
    else:
        return f"{byte_speed / (1024 * 1024):.1f} MB/s"

def download_with_custom_progress(url, filepath, filename_for_display, referer_url):
    """Downloads an M3U8 stream with a custom text-based progress bar."""
    temp_filepath = None
    try:
        headers = {'Referer': referer_url}
        playlist_response = session.get(url, timeout=15, headers=headers)
        playlist_response.raise_for_status()

        lines = playlist_response.text.split('\n')
        segment_urls = [urljoin(url, line.strip()) for line in lines if line and not line.startswith('#')]

        if not segment_urls:
            raise ValueError("No video segments found in the M3U8 playlist.")

        total_segments = len(segment_urls)
        temp_filepath = filepath + ".tmp"

        downloaded_bytes = 0
        start_time = time.time()

        with open(temp_filepath, 'wb') as f:
            for i, segment_url in enumerate(segment_urls):
                segment_response = session.get(segment_url, timeout=30, headers=headers)
                segment_response.raise_for_status()

                segment_data = segment_response.content
                f.write(segment_data)

                # Update progress
                downloaded_bytes += len(segment_data)
                elapsed_time = time.time() - start_time
                speed = downloaded_bytes / elapsed_time if elapsed_time > 0 else 0

                percentage = (i + 1) / total_segments
                bar_length = 20
                filled_length = int(bar_length * percentage)
                bar = '#' * filled_length + '-' * (bar_length - filled_length)

                # Use carriage return `\r` to update the line in place
                print(f"\rDownloading {filename_for_display}: [{bar}] {percentage:.1%} | {format_speed(speed)}", end="")

        shutil.move(temp_filepath, filepath)
        print() # Move to the next line after download is complete
        return True

    except Exception as e:
        print() # Move to the next line on error
        if os.path.exists(filepath): os.remove(filepath)
        if temp_filepath and os.path.exists(temp_filepath): os.remove(temp_filepath)
        raise e

def parse_episode_selection(user_input, total_episodes):
    """Parses user input like '1, 3-5, 10, all' into a list of episode numbers."""
    selected_episodes = set()
    if user_input.lower() == 'all': return list(range(1, total_episodes + 1))
    for part in user_input.replace(' ', '').split(','):
        try:
            if '-' in part:
                start, end = map(int, part.split('-'))
                for i in range(start, end + 1):
                    if 1 <= i <= total_episodes: selected_episodes.add(i)
            else:
                num = int(part)
                if 1 <= num <= total_episodes: selected_episodes.add(num)
        except ValueError: console.print(f"[yellow]Warning: Skipping invalid input '{part}'[/yellow]")
    return sorted(list(selected_episodes))

def process_page(page_url):
    """Scrapes, selects, and downloads episodes from a DimaKids page."""
    console.print(f"\n[bright_cyan]Scraping details from URL...[/bright_cyan]")
    soup = get_soup(page_url)
    if not soup: return

    title_tag = soup.find('h1', class_='text-center')
    title = title_tag.text.strip() if title_tag else "Unknown Title"

    episodes_list = []
    episodes_container = soup.find('div', class_='moviesBlocks')
    is_movie = episodes_container is None

    if not is_movie:
        for movie_div in episodes_container.find_all('div', class_='movie'):
            link_tag = movie_div.find('a')
            badge = movie_div.find('div', class_='badge-overd')
            if link_tag and badge:
                try:
                    ep_num = int(re.search(r'\d+', badge.text).group())
                    ep_link = urljoin(page_url, link_tag['href'])
                    episodes_list.append({'num': ep_num, 'link': ep_link})
                except (AttributeError, ValueError): continue
    else:
        episodes_list.append({'num': 1, 'link': page_url})

    panel_content = Text(f"Total Episodes Found: {len(episodes_list)}", style="yellow")
    console.print(Panel(Align.center(panel_content), title=f"[bold bright_magenta]{title}[/bold bright_magenta]", border_style="bright_blue"))

    if not episodes_list:
        console.print("[bold red]Could not find any episodes on this page.[/bold red]")
        return

    if not is_movie:
        user_choice = console.input("[bold]Enter episode numbers to download ([bright_cyan]e.g., 1, 3-5, all[/bright_cyan]) or '[magenta]b[/magenta]' to go back: [/bold]")
        if user_choice.lower() == 'b': return
        selected_numbers = parse_episode_selection(user_choice, len(episodes_list))
    else:
        selected_numbers = [1]

    if not selected_numbers:
        console.print("[bold red]No valid episodes selected.[/bold red]")
        return

    console.print("\n[yellow]Finding and resolving M3U8 links...[/yellow]")
    final_download_list = []
    with console.status("[bold bright_green]Processing episodes...") as status:
        for i, num in enumerate(selected_numbers):
            status.update(f"[bold bright_green]Processing Episode {num} ({i+1}/{len(selected_numbers)})...[/bold bright_green]")
            ep_data = next((ep for ep in episodes_list if ep['num'] == num), None)
            if ep_data:
                episode_soup = get_soup(ep_data['link']) if not is_movie else soup
                direct_link = get_m3u8_link_from_js(episode_soup)
                if direct_link:
                    final_download_list.append({'num': num, 'link': direct_link, 'page_link': ep_data['link']})
                else:
                    console.print(f"[red]Could not find M3U8 link for Episode {num}.[/red]")

    if not final_download_list:
        console.print("[bold red]Could not resolve any download links for the selected episodes.[/bold red]")
        return

    safe_folder_name = re.sub(r'[\\/*?:"<>|]', "", title)
    os.makedirs(safe_folder_name, exist_ok=True)

    json_export_data = {"title": title, "episodes": {}}
    for item in final_download_list:
        json_export_data["episodes"][str(item['num'])] = item['link']
    json_filepath = os.path.join(safe_folder_name, 'links.json')
    with open(json_filepath, 'w', encoding='utf-8') as f:
        json.dump(json_export_data, f, ensure_ascii=False, indent=4)
    console.print(f"\n[bold bright_green]✓ Link data saved to: [bright_cyan]{json_filepath}[/bright_cyan][/bold bright_green]")

    confirm = console.input("Proceed with download? ([bold bright_green]y[/bold bright_green]/[bold red]n[/bold red]): ")
    if confirm.lower() != 'y':
        console.print("[yellow]Download cancelled.[/yellow]")
        return

    console.print(f"\n[yellow]Downloads will be saved in folder: './{safe_folder_name}/'[/yellow]\n")

    downloaded_eps, failed_eps = [], []
    zfill_width = len(str(len(episodes_list))) if len(episodes_list) > 9 else 2

    for item in final_download_list:
        max_retries = 3
        attempt = 0
        download_successful = False
        current_url = item['link']

        while not download_successful and attempt < max_retries:
            attempt += 1
            try:
                if attempt > 1:
                    console.print(f"\n[yellow]Download failed. Refreshing link and retrying ({attempt}/{max_retries})...[/yellow]")
                    episode_soup = get_soup(item['page_link'])
                    new_final_url = get_m3u8_link_from_js(episode_soup)
                    if new_final_url:
                        current_url = new_final_url
                    else:
                        console.print("[red]Could not refresh link. Aborting retries.[/red]")
                        break
                    time.sleep(3)

                filename = f"{item['num']:0{zfill_width}d}.mp4" if not is_movie else f"{safe_folder_name}.mp4"
                filepath = os.path.join(safe_folder_name, filename)
                download_successful = download_with_custom_progress(current_url, filepath, filename, item['page_link'])

            except Exception as e:
                console.print(f"\n[red]❌ Download error for {filename}: {e}[/red]")

        if download_successful:
            downloaded_eps.append(item['num'])
        else:
            failed_eps.append(item['num'])

    console.print("\n--- [bold]Final Download Summary[/bold] ---")
    if downloaded_eps: console.print(f"[bright_green]✅ Successfully downloaded episodes: {', '.join(map(str, sorted(downloaded_eps)))}[/bright_green]")
    if failed_eps: console.print(f"[red]❌ Failed to download episodes: {', '.join(map(str, sorted(failed_eps)))}[/red]")

def main():
    """Main function to run the interactive scraper."""
    banner_text = """

██████╗  ██████╗ ██╗    ██╗    ██████╗ ██╗███╗   ███╗ █████╗ ██╗  ██╗██╗██████╗ ███████╗
██╔══██╗██╔═══██╗██║    ██║    ██╔══██╗██║████╗ ████║██╔══██╗██║ ██╔╝██║██╔══██╗██╔════╝
██║  ██║██║   ██║██║ █╗ ██║    ██║  ██║██║██╔████╔██║███████║█████╔╝ ██║██║  ██║███████╗
██║  ██║██║   ██║██║███╗██║    ██║  ██║██║██║╚██╔╝██║██╔══██║██╔═██╗ ██║██║  ██║╚════██║
██████╔╝╚██████╔╝╚███╔███╔╝    ██████╔╝██║██║ ╚═╝ ██║██║  ██║██║  ██╗██║██████╔╝███████║
╚═════╝  ╚═════╝  ╚══╝╚══╝     ╚═════╝ ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚═════╝ ╚══════╝                                                                                      
"""
    byline_text = "by Hamza v 1.0.0"

    console.print(banner_text, style="bold cyan")
    console.print(byline_text, style="bold yellow", justify="center")
    console.print()
    console.print()

    while True:
        url_input = console.input("\n[bold]Paste a URL from dimakids.com (or '[magenta]q[/magenta]' to quit): [/bold]")
        if url_input.lower() in ['q', 'quit']:
            break
        if "dimakids.com" in url_input:
            process_page(url_input)
            console.print("\n" + "─"*80 + "\n")
        else:
            console.print("[bold red]Invalid URL. Please paste a valid link from dimakids.com.[/bold red]")

    console.print("\n[bold yellow]Thank you for using the downloader. Goodbye![/bold yellow]")

if __name__ == "__main__":
    main()