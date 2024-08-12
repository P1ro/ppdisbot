from docopt import docopt
import os
import daemon
import daemon.pidfile
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks
import yt_dlp as ytdl
import logging
import asyncio
from collections import deque
import time
from yt_dlp.utils import DownloadError

from discord.ui import Button, View

# Setup logging to a file
logging.basicConfig(filename='/tmp/pyppdisbot.log', level=logging.INFO)

# Define the usage pattern for the command-line arguments
doc = """
My Discord Bot.

Usage:
  pyppdisbot.py [--daemon]
  pyppdisbot.py (-h | --help)
  pyppdisbot.py --version

Options:
  -h --help     Show this screen.
  --version     Show version.
  --daemon      Run the bot in the background as a daemon.
"""

# Parse the command-line arguments
args = docopt(doc, version='PP Discord Bot 1.0')

PID_FILE = '/tmp/pyppdisbot.pid'

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Global variable to store the playback message
playback_message = None

intents = discord.Intents.default()
intents.message_content = True

async def progress_bar(voice_client, total_duration):
    length = 30  # Length of the progress bar
    while voice_client.is_playing():
        current_time = voice_client.timestamp.total_seconds()
        progress = int((current_time / total_duration) * length)
        bar = "█" * progress + "-" * (length - progress)
        progress_message = f"Progress: [{bar}] {int(current_time)}s / {int(total_duration)}s"
        
        # Assuming you store the message ID of the playback message
        await playback_message.edit(content=progress_message)
        
        await asyncio.sleep(5)  # Update every 5 seconds

async def load_playlist(playlist_url):
    ydl_opts = {'extract_flat': 'in_playlist'}  # Use the flat extraction for speed
    with ytdl.YoutubeDL(ydl_opts) as ydl:
        info = await extract_info_with_retries(ydl, playlist_url)  # Await the coroutine
        if info and 'entries' in info:
            return [entry['url'] for entry in info['entries']]
        return []


def get_prefix(bot, message):
    prefixes = ['&', '!']  # List of prefixes the bot should recognize
    return prefixes

bot = commands.Bot(command_prefix=get_prefix, intents=intents)

# YouTube-DL options
ytdl_format_options = {
    'format': 'worstaudio',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,  # Allow playlists
    'nocheckcertificate': True,
    'ignoreerrors': True,  # Continue on errors
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'options': '-vn -buffer_size 65535 -http_persistent 0'
}

ytdl_instance = ytdl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl_instance.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl_instance.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

queue = deque()
current_track = None

async def extract_info_with_retries(ydl, url, retries=3, delay=5):
    loop = asyncio.get_event_loop()
    for attempt in range(retries):
        try:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
            logging.info(f"Successfully extracted info: {info}")  # Log the extracted info
            return info
        except DownloadError as e:
            logging.error(f"DownloadError: {e}")
            if attempt < retries - 1:
                logging.error(f"Error extracting info (attempt {attempt + 1}/{retries}), retrying in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                raise e
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            raise e
    return None

async def auto_disconnect(ctx):
    await bot.wait_until_ready()
    voice_client = ctx.voice_client

    while voice_client and voice_client.is_connected():
        # Check if the only member in the channel is the bot itself
        if len(voice_client.channel.members) == 1:
            await ctx.send("Voice channel is empty, stopping playback and leaving the channel.")
            await voice_client.disconnect()
            break
        
        await asyncio.sleep(30)  # Check every 30 seconds


async def check_queue(ctx):
    global playback_message  # Access the global playback_message

    if queue:
        next_track = queue.popleft()
        try:
            info = await extract_info_with_retries(ytdl_instance, next_track)
            audio_url = info['url']
            ctx.voice_client.play(discord.FFmpegPCMAudio(audio_url), after=lambda e: bot.loop.create_task(check_queue(ctx)))
            await update_status(info['title'])  # Update the bot's status with the next song title
            
            # Edit the existing playback message
            if playback_message:
                await playback_message.edit(content=f"Now playing: {info['title']}")
            else:
                playback_message = await ctx.send(f"Now playing: {info['title']}")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
            logging.error(f"Playback error: {str(e)}")
            await bot.change_presence(status=discord.Status.idle, activity=discord.Game("Idle"))
    else:
        await bot.change_presence(status=discord.Status.idle, activity=discord.Game("Queue is empty"))
        if playback_message:
            await playback_message.edit(content="The queue is empty. Playback has ended.")
        else:
            await ctx.send("The queue is empty. Playback has ended.")


async def update_status(title):
    game = discord.Game(f"Now playing: {title}")
    await bot.change_presence(status=discord.Status.online, activity=game)

# Task to disconnect bot after inactivity
@tasks.loop(minutes=0.5)
async def disconnect_after_inactivity():
    for vc in bot.voice_clients:
        if not vc.is_playing():
            await vc.disconnect()
            logging.info(f"Bot disconnected from {vc.channel} due to inactivity")

async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(status=discord.Status.idle, activity=discord.Game("&help"))

@bot.event
async def on_command_completion(ctx):
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        await bot.change_presence(status=discord.Status.idle, activity=discord.Game("&help"))

@bot.command(name='join')
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send("{} is not connected to a voice channel".format(ctx.message.author.name))
        return
    channel = ctx.message.author.voice.channel
    await channel.connect()
    await ctx.send(f"Joined {channel.name}")

@bot.command(name='leave')
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send("The bot is not connected to a voice channel.")

## Remove the default help command to avoid conflicts
bot.remove_command('help')

@bot.command(name='help', help="Shows this message.")
async def custom_help_command(ctx):
    # Retrieve the prefix using the get_prefix function
    prefix = get_prefix(bot, ctx.message)
    help_message = f"""Command prefix: {' , '.join(prefix)} 
    
Commands:
&help - Shows this message
&play <url> - Play a song or a playlist from a URL
&queue - Display the current queue
&stop - Stop the current playback
&next - Skip to the next track
&prev - Play the previous track
&join - Make the bot join your voice channel
&leave - Make the bot leave the voice channel
"""

    await ctx.send(help_message)
    await bot.change_presence(status=discord.Status.online, activity=discord.Game("Assisting users with commands"))


@bot.command(name='play', help="Play a song or a playlist from a YouTube URL.")
async def play(ctx, url=None):
    global current_track, playback_message

    if url:
        try:
            playlist_urls = await load_playlist(url)  # Load the playlist URLs
            if playlist_urls:
                queue.extend(playlist_urls)
                await ctx.send(f"Added {len(playlist_urls)} tracks from the playlist to the queue.")
            else:
                await ctx.send("No information could be retrieved from the URL.")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
            logging.error(f"Error adding track to queue: {str(e)}")
            return

    if not queue:
        await ctx.send("The queue is empty.")
        return
    
    # Ensure the bot is connected to the voice channel
    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("You are not connected to a voice channel.")
            return
    
    # If the bot is already playing, do not start a new track
    if ctx.voice_client.is_playing():
        await ctx.send("Already playing.")
        return
    
    current_track = queue.popleft()

    try:
        info = await extract_info_with_retries(ytdl_instance, current_track)
        if not info:
            await ctx.send("No information could be retrieved from the URL.")
            return

        audio_url = info['url']
        ctx.voice_client.play(discord.FFmpegPCMAudio(audio_url), after=lambda e: bot.loop.create_task(check_queue(ctx)))

        buttons = [
            discord.ui.Button(label="⏮️ Previous", custom_id="prev", style=discord.ButtonStyle.secondary),
            discord.ui.Button(label="⏯️ Play/Pause", custom_id="pause_resume", style=discord.ButtonStyle.primary),
            discord.ui.Button(label="⏭️ Next", custom_id="next", style=discord.ButtonStyle.secondary)
        ]
        view = discord.ui.View()
        for button in buttons:
            view.add_item(button)

        if playback_message:
            await playback_message.edit(content=f"Now playing: {info['title']}", view=view)
        else:
            playback_message = await ctx.send(f"Now playing: {info['title']}", view=view)
    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")
        logging.error(f"Playback error: {str(e)}")


@bot.command(name='stop')
async def stop(ctx):
    voice_client = ctx.message.guild.voice_client
    if not voice_client or not voice_client.is_connected():
        await ctx.send("The bot is not connected to any voice channel.")
        return

    if voice_client.is_playing():
        voice_client.stop()
    
    await ctx.send("Stopped playing.")

@bot.command(name='next')
async def next(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await play(ctx)
    else:
        await ctx.send("Not currently playing anything.")

@bot.command(name='prev')
async def prev(ctx):
    global current_track

    if current_track:
        queue.appendleft(current_track)
        voice_client = ctx.message.guild.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.stop()
        await play(ctx)
    else:
        await ctx.send("No previous track.")

# Interaction callback for buttons
@bot.event
async def on_interaction(interaction):
    voice_client = interaction.guild.voice_client
    
    if interaction.data['custom_id'] == 'pause_resume':
        if voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message('Resumed playback', ephemeral=True)
        elif voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message('Paused playback', ephemeral=True)
    
    elif interaction.data['custom_id'] == 'next':
        if voice_client is not None and voice_client.is_playing():
            voice_client.stop()
            # Call the play command but handle it as an interaction
            await handle_play_interaction(interaction)
    
    elif interaction.data['custom_id'] == 'prev':
        await handle_play_interaction(interaction)

async def handle_play_interaction(interaction):
    global current_track, playback_message

    if not queue:
        await interaction.response.send_message("The queue is empty.", ephemeral=True)
        return

    current_track = queue.popleft()
    voice_client = interaction.guild.voice_client

    if voice_client is None:
        channel = interaction.user.voice.channel
        voice_client = await channel.connect()
    elif voice_client.channel != interaction.user.voice.channel:
        await voice_client.move_to(interaction.user.voice.channel)

    try:
        # Acknowledge the interaction immediately to avoid timeout
        await interaction.response.defer()

        info = await extract_info_with_retries(ytdl_instance, current_track)
        if not info:
            await interaction.followup.send("No information could be retrieved from the URL.", ephemeral=True)
            return

        audio_url = info['url']
        voice_client.play(discord.FFmpegPCMAudio(audio_url), after=lambda e: bot.loop.create_task(check_queue(interaction)))

        buttons = [
            discord.ui.Button(label="⏮️ Previous", custom_id="prev", style=discord.ButtonStyle.secondary),
            discord.ui.Button(label="⏯️ Play/Pause", custom_id="pause_resume", style=discord.ButtonStyle.primary),
            discord.ui.Button(label="⏭️ Next", custom_id="next", style=discord.ButtonStyle.secondary)
        ]
        view = discord.ui.View()
        for button in buttons:
            view.add_item(button)

        # Edit the existing playback message instead of sending a new one
        if playback_message:
            await playback_message.edit(content=f"Now playing: {info['title']}", view=view)
        else:
            playback_message = await interaction.followup.send(f"Now playing: {info['title']}", view=view)

    except Exception as e:
        await interaction.followup.send(f"An error occurred: {str(e)}", ephemeral=True)
        logging.error(f"Playback error: {str(e)}")


@bot.command(name='queue', help="Display the current queue.")
async def show_queue(ctx):
    if not queue:
        await ctx.send("The queue is empty.")
        return
    
    queue_list = list(queue)  # Convert deque to list

    # Iterate through each item in the queue and send it
    for index, url in enumerate(queue_list, start=1):
        await ctx.send(f"{index}. {url}")

    # If there are more than 10 items, inform the user
    if len(queue_list) > 10:
        await ctx.send(f"...and {len(queue_list) - 10} more items in the queue.")



# Function to run the bot
def run_bot():
    bot.run(TOKEN)

# Run in daemon mode if the --daemon option is specified
if args['--daemon']:
    pidfile = daemon.pidfile.PIDLockFile(PID_FILE)
    with daemon.DaemonContext(pidfile=pidfile):
        pid = os.getpid()  # Get the current process PID
        logging.info(f"Bot running in daemon mode with PID: {pid}")
        run_bot()
else:
    run_bot()