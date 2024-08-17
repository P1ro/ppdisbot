import discord
from discord import FFmpegPCMAudio
from discord.ext import commands
from yt_dlp import YoutubeDL
import os
import asyncio
from dotenv import load_dotenv
import logging

# Set up logging to a file
logging.basicConfig(filename='/tmp/pyppdisbot.log', level=logging.INFO)
logging.info("###############################")
logging.info("###-------- ppbot ----------###")
logging.info("###############################")

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Set up bot intents and commands
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# Queue and history to manage song playback
queue = asyncio.Queue()
metadata_queue = []  # Store song metadata (title, uploader, duration, etc.)
previous_songs = []  # Stack to store previously played songs
current_song = None  # Store the current song

# Allowed channels and users
ALLOWED_CHANNELS = [1271957559732862977]
ALLOWED_USER_IDS = [275385318574915585]

# Timeout for auto-disconnection (seconds)
DISCONNECT_TIMEOUT = 120  # 2 minutes

# Maximum number of songs to fetch from a playlist
MAX_PLAYLIST_ITEMS = 100

# Restrict the bot to specific channels and users
async def check_channel(interaction):
    return interaction.channel.id in ALLOWED_CHANNELS and interaction.user.id in ALLOWED_USER_IDS

# Event handler for bot readiness
@bot.event
async def on_ready():
    print(f'Bot is ready as {bot.user}')
    await bot.change_presence(activity=discord.Game(name="/help"))
    await bot.tree.sync()

# Connect the bot to a voice channel
async def connect_to_voice(interaction):
    voice_channel = getattr(interaction.user.voice, 'channel', None)
    if not voice_channel:
        await interaction.followup.send("You need to be in a voice channel to play music.", ephemeral=True)
        return None

    voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
    if not voice_client:
        voice_client = await voice_channel.connect()

    return voice_client

# Play a song using FFmpegPCMAudio
async def play_audio(voice_client, stream_url):
    ffmpeg_options = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn -sn -dn -buffer_size 65535 -http_persistent 0'
    }

    try:
        logging.info(f"Playing audio: {stream_url}")
        voice_client.play(FFmpegPCMAudio(stream_url, **ffmpeg_options), after=lambda e: asyncio.run_coroutine_threadsafe(play_next_song(voice_client), bot.loop))
    except Exception as e:
        logging.error(f"Error playing audio: {str(e)}")

# Detect whether the URL is a playlist or a single video
async def detect_playlist(url):
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
    }

    def _extract(url):
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False, process=False)

    try:
        info = await asyncio.to_thread(_extract, url)
        is_playlist = 'entries' in info
        return is_playlist
    except Exception as e:
        logging.error(f"Error detecting playlist: {str(e)}")
        return False

# Fetch the direct stream URL for a single song
async def fetch_single_stream_url(url):
    ydl_opts = {
        'format': 'bestaudio',
        'quiet': True,
    }

    def _extract(url):
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        logging.info(f"Extracting stream URL for song: {url}")
        info = await asyncio.to_thread(_extract, url)
        return {
            'url': info['url'],
            'title': info.get('title', 'Unknown'),
            'uploader': info.get('uploader', 'Unknown'),
            'duration': info.get('duration', 0),
            'views': info.get('view_count', 'Unknown'),
            'upload_date': info.get('upload_date', 'Unknown'),
        }
    except Exception as e:
        logging.error(f"Error fetching stream URL: {str(e)}")
        return None

# Fetch stream URL(s) and metadata using yt_dlp
async def fetch_stream_urls(url):
    is_playlist = await detect_playlist(url)
    ydl_opts = {
        'format': 'bestaudio',
        'quiet': True,
    }

    if is_playlist:
        ydl_opts['extract_flat'] = 'in_playlist'

    def _extract(url):
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        logging.info(f"Extracting metadata for URL: {url}")
        info = await asyncio.to_thread(_extract, url)

        if 'entries' in info:
            # Playlist case: Fetch direct stream URL for each song
            song_data = []
            for entry in info['entries'][:MAX_PLAYLIST_ITEMS]:
                song_metadata = await fetch_single_stream_url(f"https://www.youtube.com/watch?v={entry['id']}")
                if song_metadata:
                    song_data.append(song_metadata)
            return song_data, True
        else:
            # Single video case: Extract metadata directly
            metadata = await fetch_single_stream_url(url)
            return [metadata], False

    except Exception as e:
        logging.error(f"Error fetching stream URL(s): {str(e)}")
        return None, None

# Play the next song in the queue
async def play_next_song(voice_client):
    global current_song
    if not queue.empty():
        # Save the current song to the previous_songs stack
        if current_song:
            previous_songs.append(current_song)

        current_song = await queue.get()
        logging.info(f"Playing next song: {current_song['title']}")
        await play_audio(voice_client, current_song['url'])
    else:
        logging.info("Queue is empty, switching presence back to /help.")
        await bot.change_presence(activity=discord.Game(name="/help"))
        await check_and_disconnect(voice_client)

# Disconnect the bot if idle or alone
async def check_and_disconnect(voice_client):
    await asyncio.sleep(5)
    if not voice_client.is_playing() and len(voice_client.channel.members) == 1:
        await asyncio.sleep(DISCONNECT_TIMEOUT)
        if not voice_client.is_playing() and len(voice_client.channel.members) == 1:
            logging.info("Bot disconnected due to inactivity or being alone.")
            await voice_client.disconnect()

# Play a song or playlist from YouTube using a URL
@bot.tree.command(name="play", description="Play a song or playlist from YouTube")
async def play(interaction: discord.Interaction, url: str):
    await interaction.response.defer(ephemeral=True)

    voice_client = await connect_to_voice(interaction)
    if not voice_client:
        return

    songs, is_playlist = await fetch_stream_urls(url)
    if not songs:
        await interaction.followup.send("Failed to retrieve the stream URL(s).", ephemeral=True)
        return

    # Add songs to the queue
    for song_metadata in songs:
        await queue.put(song_metadata)
        metadata_queue.append(song_metadata)
        logging.info(f"Added song to queue: {song_metadata['title']} by {song_metadata['uploader']}")

    # Notify user
    if is_playlist:
        await interaction.followup.send(f"Added {len(songs)} songs from the playlist to the queue.", ephemeral=True)
    else:
        await interaction.followup.send(f"Playing: {songs[0]['title']}", ephemeral=True)

    # Play the first song if the bot is not currently playing
    if not voice_client.is_playing() and not queue.empty():
        await play_next_song(voice_client)

# Display the current queue
@bot.tree.command(name="queue", description="Display the current queue of songs")
async def display_queue(interaction: discord.Interaction):
    if not metadata_queue:
        await interaction.response.send_message("The queue is currently empty.", ephemeral=True)
    else:
        queue_message = "\n".join([f"{idx+1}. {metadata['title']} by {metadata['uploader']}" for idx, metadata in enumerate(metadata_queue)])
        logging.info(f"Current queue: {queue_message}")
        await interaction.response.send_message(f"Current Queue:\n{queue_message}", ephemeral=True)

# Skip to the next song
@bot.tree.command(name="next", description="Skip to the next song")
async def skip_next(interaction: discord.Interaction):
    voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
    if voice_client and voice_client.is_playing():
        voice_client.stop()  # Stopping the current song will trigger play_next_song
    logging.info("Skipping to next song.")
    await interaction.response.send_message("Skipped to the next song.", ephemeral=True)

# Play the previous song
@bot.tree.command(name="prev", description="Play the previous song")
async def play_previous(interaction: discord.Interaction):
    global current_song
    voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
    
    if previous_songs:
        # Stop the current song and play the previous one
        if voice_client.is_playing():
            voice_client.stop()

        current_song = previous_songs.pop()
        logging.info(f"Playing previous song: {current_song['title']} by {current_song['uploader']}")
        await play_audio(voice_client, current_song['url'])
        await interaction.response.send_message(f"Playing: {current_song['title']} by {current_song['uploader']}", ephemeral=True)
    else:
        await interaction.response.send_message("No previous songs in the history.", ephemeral=True)

# Stop the currently playing song and disconnect
@bot.tree.command(name="stop", description="Stop playing music")
async def stop(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
    if voice_client and voice_client.is_playing():
        voice_client.stop()

    # Disconnect after stopping
    if voice_client and voice_client.is_connected():
        logging.info("Bot stopped and disconnected.")
        await voice_client.disconnect()

    await interaction.followup.send("Stopped the music and disconnected.", ephemeral=True)

# Run the bot using the token from the .env file
bot.run(TOKEN)