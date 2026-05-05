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

# --- Global objects ---
console = Console()
scraper = cloudscraper.create_scraper()


def get_soup(url):
    """Fetches a URL and returns a BeautifulSoup object."""
    try:
        response = scraper.get(url, timeout=15, allow_redirects=True)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        console.log(f"[red]Error fetching soup from {url}: {e}[/red]")
        return None

def get_final_download_link(intermediate_url):
    """Resolves the final, direct download link."""
    if not intermediate_url.startswith('http'):
        intermediate_url = f"https://anime3rb.com{intermediate_url}"
    try:
        with scraper.head(intermediate_url, timeout=15, allow_redirects=True) as r:
            final_url = r.url
            if final_url != intermediate_url and final_url.split('?')[0].endswith(('.mp4', '.mkv')):
                return final_url
        console.log(f"[dim]Fast method failed. Falling back to parsing HTML...[/dim]")
        soup = get_soup(intermediate_url)
        if not soup: return None
        final_link_tag = soup.find('a', href=True, string=re.compile(r'اضغط هنا للتحميل', re.IGNORECASE))
        if final_link_tag: return final_link_tag['href']
        final_link_tag = soup.find('a', href=True, class_=re.compile(r'download|btn-download|btn-success'))
        if final_link_tag: return final_link_tag['href']
        return None
    except Exception:
        return None

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

def format_size(size_bytes):
    """Formats bytes into a human-readable string (KB, MB, GB)."""
    if not isinstance(size_bytes, (int, float)) or size_bytes <= 0: return "N/A"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def get_download_links_universal(episode_url):
    """Gets the intermediate download quality links from an episode page."""
    initial_soup = get_soup(episode_url)
    if not initial_soup: return {}
    download_container = initial_soup.find(lambda tag: tag.name == 'div' and 'تحميل مباشر' in tag.get_text())
    if not download_container: return {}

    download_links = {}
    for link_tag in download_container.find_all('a', href=re.compile(r'/download/')):
        quality_label = link_tag.find_previous('label')
        if quality_label:
            match = re.search(r'\[(\d+p(?: HEVC)?)\]', quality_label.text.strip())
            if match: download_links[match.group(1)] = link_tag['href']
    return download_links

def download_file(url, filepath, filename_for_display):
    """Downloads a single file using a standard, single connection."""
    try:
        with scraper.get(url, stream=True, timeout=120, allow_redirects=True) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))

            with Progress(
                TextColumn("[bold bright_cyan]{task.fields[filename]}", justify="left"),
                BarColumn(bar_width=None),
                "[progress.percentage]{task.percentage:>3.1f}%", "•",
                DownloadColumn(), "•",
                TransferSpeedColumn(), "•",
                TimeRemainingColumn()
            ) as progress:
                task = progress.add_task("download", total=total_size, filename=filename_for_display)
                with open(filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))

        # Verify download
        downloaded_size = os.path.getsize(filepath)
        if total_size != 0 and downloaded_size < total_size:
            raise Exception("Incomplete download")

        return True

    except Exception as e:
        # Clean up failed download
        if os.path.exists(filepath):
            os.remove(filepath)
        # Re-raise exception to be caught by the retry loop
        raise e

def scrape_anime_page(anime_data):
    title, url = anime_data['title'], anime_data['link']
    console.print(f"\n[bright_cyan]Scraping details for: [bold]{title}[/bold]...[/bright_cyan]")
    soup = get_soup(url)
    if not soup: return

    episodes_list = []
    videos_container = soup.find('div', class_='videos-list')
    if videos_container:
        for ep_link in videos_container.find_all('a', href=True):
            video_data_div = ep_link.find('div', class_='video-data')
            if video_data_div and video_data_div.find('span'):
                try: episodes_list.append({'num': int(re.search(r'\d+', video_data_div.find('span').text).group()),'link': ep_link['href']})
                except (AttributeError, ValueError): continue

    status_td = soup.find('td', string=re.compile(r'الحالة:'))
    status = status_td.find_next_sibling('td').text.strip() if status_td and status_td.find_next_sibling('td') else "N/A"

    # --- VISUAL FIX: Combine content into a single Text object for perfect alignment ---
    panel_text = Text(f"Status: {status}\n", style="bold bright_green", justify="center")
    panel_text.append(f"Total Episodes Found: {len(episodes_list)}", style="yellow")
    console.print(Panel(panel_text, title=f"[bold bright_magenta]{title}[/bold bright_magenta]", border_style="bright_blue", expand=False))

    if not episodes_list: console.print("[bold red]Could not find any episodes to select.[/bold red]"); return

    while True:
        user_choice = console.input("[bold]Enter episode numbers to download ([bright_cyan]e.g., 1, 3-5, all[/bright_cyan]) or '[magenta]b[/magenta]' to go back: [/bold]")
        if user_choice.lower() == 'b': break
        selected_numbers = parse_episode_selection(user_choice, len(episodes_list))
        if not selected_numbers: console.print("[bold red]No valid episodes selected.[/bold red]"); continue

        download_plan = []
        with console.status("[bold bright_green]Finding all available links...") as status:
            for num in selected_numbers:
                ep_data = next((ep for ep in episodes_list if ep['num'] == num), None)
                if ep_data:
                    status.update(f"Fetching links for Episode {num}...")
                    links = get_download_links_universal(ep_data['link'])
                    download_plan.append({'num': num, 'links': links})

        # --- VISUAL FIX: Use a cleaner, more modern box style ---
        summary_table = Table(title="Found Download Links", box=box.ROUNDED, header_style="bold bright_magenta", border_style="bright_blue")
        summary_table.add_column("Ep", style="bright_cyan"); summary_table.add_column("1080p", style="bright_green"); summary_table.add_column("720p", style="bright_blue"); summary_table.add_column("480p", style="dim")
        for item in download_plan: summary_table.add_row(f"{item['num']:02d}", "✓" if '1080p' in item['links'] else "✗", "✓" if '720p' in item['links'] else "✗", "✓" if '480p' in item['links'] else "✗")
        console.print(summary_table)

        all_resolved_links = {"anime_title": title, "episodes": {}}
        quality_priority = ['1080p', '720p', '480p']
        with console.status("[bold bright_green]Resolving all direct links for JSON export...") as status:
            for item in download_plan:
                episode_links = {}
                for quality in quality_priority:
                    if quality in item['links']:
                        status.update(f"Resolving Ep {item['num']} ({quality})...")
                        final_url = get_final_download_link(item['links'][quality])
                        if final_url: episode_links[quality] = final_url
                if episode_links: all_resolved_links["episodes"][str(item['num'])] = episode_links

        safe_folder_name = re.sub(r'[\\/*?:"<>|]', "", title)
        os.makedirs(safe_folder_name, exist_ok=True)
        if all_resolved_links["episodes"]:
            json_filepath = os.path.join(safe_folder_name, 'links.json')
            with open(json_filepath, 'w', encoding='utf-8') as f:
                json.dump(all_resolved_links, f, ensure_ascii=False, indent=4)
            console.print(f"\n[bold bright_green]✓ Link data saved to: [bright_cyan]{json_filepath}[/bright_cyan][/bold bright_green]")

        final_download_list = []
        for ep_num_str, links_dict in all_resolved_links["episodes"].items():
            ep_num = int(ep_num_str)
            best_url, best_quality = None, None
            for quality in quality_priority:
                if quality in links_dict:
                    best_url, best_quality = links_dict[quality], quality
                    break
            if best_url: final_download_list.append({'num': ep_num, 'url': best_url, 'quality': best_quality})

        if not final_download_list:
            console.print("[bold red]Could not resolve any direct download links.[/bold red]"); break

        confirm = console.input("Proceed with download? ([bold bright_green]y[/bold bright_green]/[bold red]n[/bold red]): ")
        if confirm.lower() != 'y':
            console.print("[yellow]Download cancelled.[/yellow]"); break

        console.print(f"\n[yellow]Downloads will be saved in folder: './{safe_folder_name}/'[/yellow]\n")

        downloaded_eps, failed_eps = [], []
        zfill_width = len(str(len(episodes_list))) if len(episodes_list) > 9 else 2

        for item in final_download_list:
            max_retries = 3
            attempt = 0
            download_successful = False
            current_url = item['url']

            while not download_successful and attempt < max_retries:
                attempt += 1
                try:
                    if attempt > 1:
                        console.print(f"[yellow]Download failed. Refreshing link and retrying ({attempt}/{max_retries})...[/yellow]")
                        ep_data = next((ep for ep in episodes_list if ep['num'] == item['num']), None)
                        if ep_data:
                            new_links = get_download_links_universal(ep_data['link'])
                            if item['quality'] in new_links:
                                new_final_url = get_final_download_link(new_links[item['quality']])
                                if new_final_url:
                                    current_url = new_final_url
                                else:
                                    console.print("[red]Could not refresh link. Aborting retries for this episode.[/red]")
                                    break
                        time.sleep(3)

                    filename = f"{item['num']:0{zfill_width}d}-{item['quality']}.mp4"
                    filepath = os.path.join(safe_folder_name, filename)
                    download_successful = download_file(current_url, filepath, filename)

                except Exception as e:
                    console.print(f"[red]❌ Download error for {filename}: {e}[/red]")

            if download_successful:
                downloaded_eps.append(item['num'])
            else:
                failed_eps.append(item['num'])

        console.print("\n--- [bold]Final Download Summary[/bold] ---")
        if downloaded_eps: console.print(f"[bright_green]✅ Successfully downloaded episodes: {', '.join(map(str, sorted(downloaded_eps)))}[/bright_green]")
        if failed_eps: console.print(f"[red]❌ Failed to download episodes: {', '.join(map(str, sorted(failed_eps)))}[/red]")
        break

def search_and_select_anime(query):
    console.print(f"\n[yellow]Searching for '[bold bright_cyan]{query}[/bold bright_cyan]'...[/yellow]")
    base_url = "https://anime3rb.com"
    soup = get_soup(base_url)
    if not soup: return

    anime_cards = []
    try:
        livewire_component = soup.find('form', {'wire:id': True})
        if livewire_component:
            snapshot_str = livewire_component.get('wire:snapshot')
            payload = {"_token": soup.find('meta', {'name': 'csrf-token'})['content'],"components": [{"snapshot": snapshot_str, "updates": {"query": query}, "calls": []}]}
            search_response = scraper.post(f"{base_url}/livewire/update", headers={'Content-Type': 'application/json'}, data=json.dumps(payload))
            search_response.raise_for_status()
            response_data = search_response.json()
            anime_cards = BeautifulSoup(response_data['components'][0]['effects']['html'], 'html.parser').find_all('a', class_='simple-title-card')
    except Exception: pass

    if not anime_cards:
      search_url = f"{base_url}/search?q={query.replace(' ', '+')}"
      search_soup = get_soup(search_url)
      if search_soup:
        anime_cards = search_soup.select('div.videos-list-content a.simple-title-card')

    if not anime_cards: console.print(f"[bold red]No results found for '{query}'.[/bold red]"); return

    results_list = [{'title': card.find('h4').text.strip(), 'link': card['href']} for card in anime_cards]
    table = Table(title=f"Search Results for '{query}'", box=box.DOUBLE_EDGE, header_style="bold bright_magenta", border_style="bright_blue")
    table.add_column("No.", style="dim", justify="center", width=4); table.add_column("Title", style="bold bright_cyan", width=60, overflow="ellipsis", no_wrap=True)
    for i, item in enumerate(results_list, 1): table.add_row(f"{i:02d}", item['title'])
    console.print(table)

    while True:
        try:
            choice = console.input("[bold]Enter the number of the anime to view (or '[magenta]b[/magenta]' to go back): [/bold]")
            if choice.lower() == 'b': return
            selection = int(choice)
            if 1 <= selection <= len(results_list):
                scrape_anime_page(results_list[selection - 1]); return
            else: console.print(f"[bold red]Invalid number. Please enter between 1 and {len(results_list)}.[/bold red]")
        except ValueError: console.print("[bold red]Invalid input. Please enter a number.[/bold red]")

def main():
    """Main function to run the interactive scraper."""
    banner_text = """
██████╗  ██████╗ ██╗    ██╗     █████╗ ███╗   ██╗██╗███╗   ███╗███████╗██████╗ ██████╗ ██████╗
██╔══██╗██╔═══██╗██║    ██║    ██╔══██╗████╗  ██║██║████╗ ████║██╔════╝╚════██╗██╔══██╗██╔══██╗
██║  ██║██║   ██║██║ █╗ ██║    ███████║██╔██╗ ██║██║██╔████╔██║█████╗   █████╔╝██████╔╝██████╔╝
██║  ██║██║   ██║██║███╗██║    ██╔══██║██║╚██╗██║██║██║╚██╔╝██║██╔══╝   ╚═══██╗██╔══██╗██╔══██╗
██████╔╝╚██████╔╝╚███╔███╔╝    ██║  ██║██║ ╚████║██║██║ ╚═╝ ██║███████╗██████╔╝██║  ██║██████╔╝
╚═════╝  ╚═════╝  ╚══╝╚══╝     ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝╚═╝     ╚═╝╚══════╝╚═════╝ ╚═╝  ╚═╝╚═════╝
"""
    byline_text = "by Hamza v 1.0.0"

    console.print(banner_text, style="bold bright_cyan")
    console.print(byline_text, style="bold yellow", justify="center")
    console.print()

    while True:
        search_query = console.input("\n[bold]Enter an anime name to search for (or '[magenta]q[/magenta]' to quit): [/bold]")
        if search_query.lower() in ['q', 'quit']:
            break
        if search_query:
            search_and_select_anime(search_query)
        else:
            console.print("[bold red]Search query cannot be empty.[/bold red]")

    console.print("\n[bold yellow]Thank you for using the scraper. Goodbye![/bold yellow]")

if __name__ == "__main__":
    main()