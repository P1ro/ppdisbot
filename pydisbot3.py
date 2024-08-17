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

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Set up bot intents and commands
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# Queue and history to manage song playback
metadata_queue = asyncio.Queue()  # Queue to store song metadata
previous_songs = []  # Stack to store previously played songs
current_song = None  # Store the current song

# Allowed channels and users
ALLOWED_CHANNELS = [1271957559732862977]
ALLOWED_USER_IDS = [275385318574915585]

# Timeout for auto-disconnection (seconds)
DISCONNECT_TIMEOUT = 300  # 5 minutes

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
        logging.debug(f"Playing audio: {stream_url}")
        voice_client.play(FFmpegPCMAudio(stream_url, **ffmpeg_options), after=lambda e: asyncio.run_coroutine_threadsafe(play_next_song(voice_client), bot.loop))
    except Exception as e:
        logging.error(f"Error playing audio: {str(e)}")

# Fetch metadata and stream URL for the next song only when needed
async def fetch_and_play_next_song(voice_client):
    global current_song
    if not metadata_queue.empty():
        # Save the current song to the previous_songs stack
        if current_song:
            previous_songs.append(current_song)

        # Get the next song's metadata
        current_song = await metadata_queue.get()

        # Extract the stream URL using yt-dlp
        stream_url = await fetch_stream_url(current_song['url'])
        if stream_url:
            await play_audio(voice_client, stream_url)
        else:
            logging.error("Failed to retrieve stream URL.")
            await play_next_song(voice_client)  # Play next song if URL fails

    else:
        logging.debug("Queue is empty, switching presence back to /help.")
        # If queue is empty, set presence back to /help and start idle timeout countdown
        await bot.change_presence(activity=discord.Game(name="/help"))
        await check_and_disconnect(voice_client)

# Fetch the stream URL using yt-dlp for the specific song
async def fetch_stream_url(url):
    ydl_opts = {
        'format': 'bestaudio',
        'quiet': True
    }

    def _extract(url):
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('url')

    try:
        logging.debug(f"Extracting stream URL for: {url}")
        stream_url = await asyncio.to_thread(_extract, url)
        return stream_url
    except Exception as e:
        logging.error(f"Error fetching stream URL: {str(e)}")
        return None

# Fetch stream URL(s) from YouTube using yt-dlp, running in a separate thread
async def fetch_stream_urls(url):
    ydl_opts = {
        'format': 'bestaudio',
        'quiet': True
    }

    def _extract(url):
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info

    try:
        logging.debug(f"Extracting metadata for URL: {url}")
        info = await asyncio.to_thread(_extract, url)

        # Check if the URL is a playlist or a single video
        if 'entries' in info:
            # Playlist case: limit the number of songs to fetch
            songs = []
            for entry in info['entries']:
                song_metadata = {
                    'url': entry.get('url'),
                    'title': entry.get('title', 'Unknown'),
                    'uploader': entry.get('uploader', 'Unknown')
                }
                songs.append(song_metadata)
            return songs, True
        else:
            # Single video case
            song_metadata = {
                'url': info.get('url'),
                'title': info.get('title', 'Unknown'),
                'uploader': info.get('uploader', 'Unknown')
            }
            return [song_metadata], False

    except Exception as e:
        logging.error(f"Error fetching stream URL(s): {str(e)}")
        return None, None

# Play the next song in the queue
async def play_next_song(voice_client):
    await fetch_and_play_next_song(voice_client)

# Disconnect the bot if idle or alone
async def check_and_disconnect(voice_client):
    await asyncio.sleep(5)  # Give it a small delay to handle immediate disconnection
    if not voice_client.is_playing() and len(voice_client.channel.members) == 1:
        # Wait for a defined timeout period before disconnecting
        await asyncio.sleep(DISCONNECT_TIMEOUT)
        if not voice_client.is_playing() and len(voice_client.channel.members) == 1:
            logging.debug("Bot disconnected due to inactivity or being alone.")
            await voice_client.disconnect()

# Play a song or playlist from YouTube using a URL
@bot.tree.command(name="play", description="Play a song or playlist from YouTube")
async def play(interaction: discord.Interaction, url: str):
    await interaction.response.defer(ephemeral=True)

    voice_client = await connect_to_voice(interaction)
    if not voice_client:
        return

    # Add the song or playlist URL directly to the metadata queue
    songs, is_playlist = await fetch_stream_urls(url)
    if not songs:
        await interaction.followup.send("Failed to retrieve the stream URL(s).", ephemeral=True)
        return

    # Add metadata to the metadata queue, but defer fetching stream URL until it's needed
    for song_metadata in songs:
        await metadata_queue.put(song_metadata)
        logging.debug(f"Added song to metadata queue: {song_metadata['title']} by {song_metadata['uploader']}")
    
    # Notify user
    if is_playlist:
        await interaction.followup.send(f"Added {len(songs)} songs from the playlist to the queue.", ephemeral=True)
    else:
        await interaction.followup.send(f"Playing: {songs[0]['title']}", ephemeral=True)

    # Play the first song if the bot is not currently playing
    if not voice_client.is_playing():
        await play_next_song(voice_client)

# Stop the currently playing song and disconnect
@bot.tree.command(name="stop", description="Stop playing music")
async def stop(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
    if voice_client and voice_client.is_playing():
        voice_client.stop()

    # Disconnect after stopping
    if voice_client and voice_client.is_connected():
        logging.debug("Bot stopped and disconnected.")
        await voice_client.disconnect()

    await interaction.followup.send("Stopped the music and disconnected.", ephemeral=True)

# Run the bot using the token from the .env file
bot.run(TOKEN)