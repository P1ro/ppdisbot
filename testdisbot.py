import yt_dlp as ytdl
from yt_dlp.utils import DownloadError, ExtractorError
import time

def extract_info_with_retries(ydl, url, retries=5, delay=3):
    for attempt in range(retries):
        try:
            return ydl.extract_info(url, download=False)
        except (DownloadError, ExtractorError) as e:
            if attempt < retries - 1:
                print(f"Error extracting info (attempt {attempt + 1}/{retries}), retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                raise e

def load_playlist(playlist_url):
    ydl_opts = {'extract_flat': 'in_playlist'}
    with ytdl.YoutubeDL(ydl_opts) as ydl:
        info = extract_info_with_retries(ydl, playlist_url)
        if 'entries' in info:
            return [entry['url'] for entry in info['entries']]
        return []

# Example usage:
playlist_url = "https://www.youtube.com/watch?v=uLIs0j2WnlM&list=RDEMC8fzeVShZE57exsbYjFEsg&start_radio=1"
urls = load_playlist(playlist_url)
print(urls)  # Should print the list of video URLs from the playlist
