import json
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

intents = discord.Intents.default()
intents.message_content = True

# Configuration file for allowed and excluded channels
CONFIG_FILE = "bot_config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as file:
            return json.load(file)
    else:
        return {"allowed_channels": [], "excluded_channels": []}

def save_config(config):
    with open(CONFIG_FILE, 'w') as file:
        json.dump(config, file, indent=4)

bot_config = load_config()

def get_allowed_channels():
    return bot_config.get("allowed_channels", [])

def get_excluded_channels():
    return bot_config.get("excluded_channels", [])

def is_channel_excluded(channel_id):
    return channel_id in get_excluded_channels()

async def progress_bar(voice_client, total_duration):
    length = 30  # Length of the progress bar
    while voice_client.is_playing():
        current_time = voice_client.timestamp.total_seconds()
        progress = int((current_time / total_duration) * length)
        bar = "█" * progress + "-" * (length - progress)
        progress_message = f"Progress: [{bar}] {int(current_time)}s / {int(total_duration)}s"
        
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
    if queue:
        next_track = queue.popleft()
        await play(ctx, next_track)
    else:
        await ctx.send("The queue is empty.")

async def update_status(title):
    game = discord.Game(f"Now playing: {title}")
    await bot.change_presence(status=discord.Status.online, activity=game)

# Task to disconnect bot after inactivity
@tasks.loop(minutes=1.0)
async def disconnect_after_inactivity():
    for vc in bot.voice_clients:
        if not vc.is_playing():
            await vc.disconnect()
            logging.info(f"Bot disconnected from {vc.channel} due to inactivity")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(status=discord.Status.idle, activity=discord.Game("Idle"))

@bot.event
async def on_command_completion(ctx):
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        await bot.change_presence(status=discord.Status.idle, activity=discord.Game("Idle"))

@bot.event
async def on_message(message):
    if is_channel_excluded(message.channel.id):
        return  # Ignore messages in excluded channels
    await bot.process_commands(message)

@bot.command(name='join')
async def join(ctx):
    if is_channel_excluded(ctx.channel.id):
        await ctx.send("This channel is excluded from bot interaction.")
        return

    if not ctx.message.author.voice:
        await ctx.send("{} is not connected to a voice channel".format(ctx.message.author.name))
        return
    channel = ctx.message.author.voice.channel
    await channel.connect()
    await ctx.send(f"Joined {channel.name}")

@bot.command(name='leave')
async def leave(ctx):
    if is_channel_excluded(ctx.channel.id):
        await ctx.send("This channel is excluded from bot interaction.")
        return

    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send("The bot is not connected to a voice channel.")

# Remove the default help command to avoid conflicts
bot.remove_command('help')

@bot.command(name='help', help="Shows this message.")
async def custom_help_command(ctx):
    if is_channel_excluded(ctx.channel.id):
        await ctx.send("This channel is excluded from bot interaction.")
        return

    help_message = """Your custom help text here..."""
    await ctx.send(help_message)
    await bot.change_presence(status=discord.Status.online, activity=discord.Game("Assisting users with commands"))

@bot.command(name='play', help="Play a song or a playlist from a YouTube URL.")
async def play(ctx, url=None):
    if ctx.channel.id not in get_allowed_channels():
        await ctx.send("This command cannot be used in this channel.")
        return
    
    if is_channel_excluded(ctx.channel.id):
        await ctx.send("This channel is excluded from bot interaction.")
        return

    global current_track, playback_message

    if url:
        try:
            playlist_urls = await load_playlist(url)
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
    
    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
            # Start the auto-disconnect task
            bot.loop.create_task(auto_disconnect(ctx))
        else:
            await ctx.send("You are not connected to a voice channel.")
            return

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
        total_duration = info['duration']  # Total duration of the song in seconds

        ctx.voice_client.play(discord.FFmpegPCMAudio(audio_url), after=lambda e: bot.loop.create_task(check_queue(ctx)))

        buttons = [
            Button(label="⏮️ Previous", custom_id="prev", style=discord.ButtonStyle.secondary),
            Button(label="⏯️ Play/Pause", custom_id="pause_resume", style=discord.ButtonStyle.primary),
            Button(label="⏭️ Next", custom_id="next", style=discord.ButtonStyle.secondary)
        ]
        view = View()
        for button in buttons:
            view.add_item(button)

        # Send the playback controls message and save its reference for updating
        playback_message = await ctx.send(f"Now playing: {info['title']}", view=view)
        
        # Start the progress bar update
        bot.loop.create_task(progress_bar(ctx.voice_client, total_duration))
    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")
        logging.error(f"Playback error: {str(e)}")

@bot.command(name='configurebot', help="Configure the bot's allowed/excluded channels. Use: &configurebot add/remove/exclude/unexclude #channel")
@commands.has_permissions(administrator=True)
async def configure_bot(ctx, action: str = None, channel: discord.TextChannel = None):
    if not action or not channel:
        await ctx.send("Missing arguments. Use `&configurebot add/remove/exclude/unexclude #channel`.")
        return

    channel_id = channel.id

    if action.lower() == "add":
        if channel_id not in bot_config["allowed_channels"]:
            bot_config["allowed_channels"].append(channel_id)
            save_config(bot_config)
            await ctx.send(f"Channel {channel.mention} has been added to the allowed list.")
        else:
            await ctx.send(f"Channel {channel.mention} is already in the allowed list.")
    
    elif action.lower() == "remove":
        if channel_id in bot_config["allowed_channels"]:
            bot_config["allowed_channels"].remove(channel_id)
            save_config(bot_config)
            await ctx.send(f"Channel {channel.mention} has been removed from the allowed list.")
        else:
            await ctx.send(f"Channel {channel.mention} is not in the allowed list.")
    
    elif action.lower() == "exclude":
        if channel_id not in bot_config["excluded_channels"]:
            bot_config["excluded_channels"].append(channel_id)
            save_config(bot_config)
            await ctx.send(f"Channel {channel.mention} has been excluded from bot interaction.")
        else:
            await ctx.send(f"Channel {channel.mention} is already excluded.")
    
    elif action.lower() == "unexclude":
        if channel_id in bot_config["excluded_channels"]:
            bot_config["excluded_channels"].remove(channel_id)
            save_config(bot_config)
            await ctx.send(f"Channel {channel.mention} has been unexcluded from bot interaction.")
        else:
            await ctx.send(f"Channel {channel.mention} is not in the excluded list.")
    
    else:
        await ctx.send("Invalid action. Use `add`, `remove`, `exclude`, or `unexclude`.")

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