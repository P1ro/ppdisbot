import discord
from discord.ext import commands
import yt_dlp as ytdl  # Use yt_dlp as ytdl
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

def get_prefix(bot, message):
    prefixes = ['&', '!']  # List of prefixes the bot should recognize
    return prefixes

bot = commands.Bot(command_prefix=get_prefix, intents=intents)

# YouTube-DL options
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'options': '-vn'
}

ytdl_instance = ytdl.YoutubeDL(ytdl_format_options)  # Correctly create the YoutubeDL instance here

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

@bot.command(name='join')
async def join(ctx):
    # Check if the user is in a voice channel
    if not ctx.message.author.voice:
        await ctx.send("{} is not connected to a voice channel".format(ctx.message.author.name))
        return
    else:
        # Get the voice channel the user is in
        channel = ctx.message.author.voice.channel

    # Connect the bot to the user's voice channel
    await channel.connect()
    await ctx.send(f"Joined {channel.name}")


@bot.command(name='leave')
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send("The bot is not connected to a voice channel.")

@bot.command(name='play')
async def play(ctx, url):
    # Check if the user is in a voice channel
    if not ctx.message.author.voice:
        await ctx.send("You are not connected to a voice channel.")
        return

    channel = ctx.message.author.voice.channel
    voice_client = ctx.message.guild.voice_client

    if voice_client is None:
        voice_client = await channel.connect()

    elif voice_client.channel != channel:
        await voice_client.move_to(channel)

    # Use yt_dlp to extract the direct audio URL
    try:
        info = ytdl_instance.extract_info(url, download=False)
        audio_url = info['url']
        
        # Play the audio using FFmpeg
        voice_client.play(discord.FFmpegPCMAudio(audio_url), after=lambda e: print(f"Error: {e}") if e else None)
        await ctx.send(f"Now playing: {info['title']}")
    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")

@bot.command(name='stop')
async def stop(ctx):
    # Get the bot's current voice client (the voice channel it's connected to)
    voice_client = ctx.message.guild.voice_client

    # If the bot is not connected to any voice channel, send a message
    if not voice_client or not voice_client.is_connected():
        await ctx.send("The bot is not connected to any voice channel.")
        return

    # If the bot is playing something, stop it
    if voice_client.is_playing():
        voice_client.stop()

    # Disconnect the bot from the voice channel
    await voice_client.disconnect()
    await ctx.send("Stopped playing and disconnected from the voice channel.")

bot.run(TOKEN)
