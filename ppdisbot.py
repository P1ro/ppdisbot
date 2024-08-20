import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
import json

# Load token from .env
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Helper function to determine if URL is single song or playlist
def is_playlist(url):
    return "playlist" in url  # Simplified, can be expanded for more accuracy

# Config Manager for loading bot configuration
class ConfigManager:
    def __init__(self, config_path):
        with open(config_path, 'r') as config_file:
            self.config = json.load(config_file)

    def get(self, key, default=None):
        return self.config.get(key, default)

# Music Player Class
class Player:
    def __init__(self):
        self.queue = []
        self.current_song = None

    def add_to_queue(self, song):
        self.queue.append(song)

    def next_song(self):
        if self.queue:
            self.current_song = self.queue.pop(0)
            return self.current_song
        return None

    def clear_queue(self):
        self.queue.clear()

# Main Bot Class
class MusicBot(commands.Bot):
    def __init__(self, config_manager, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_manager = config_manager
        self.player = Player()
        self.guild_data = {}  # To store bot messages per guild

    async def on_ready(self):
        print(f'Logged in as {self.user}')
        # Sync app commands (slash commands)
        try:
            await self.tree.sync()
            print("Slash commands have been synchronized.")
        except Exception as e:
            print(f"Error synchronizing commands: {e}")

    async def connect_to_channel(self, interaction):
        if interaction.user.voice and interaction.user.voice.channel:
            voice_channel = interaction.user.voice.channel
            if interaction.guild.voice_client is None:
                await voice_channel.connect()
            return interaction.guild.voice_client
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message("You are not connected to a voice channel.", ephemeral=True)
            return None

    async def play_song(self, interaction, url):
        await interaction.response.defer(thinking=True)

        voice_client = await self.connect_to_channel(interaction)
        if not voice_client:
            return

        ydl_opts = {'format': 'bestaudio'}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            song = {
                'title': info.get('title', 'Unknown'),
                'uploader': info.get('uploader', 'Unknown'),
                'duration': info.get('duration', 0),
                'views': info.get('view_count', 'Unknown'),
                'upload_date': info.get('upload_date', 'Unknown'),
                'thumbnail': info.get('thumbnail', ''),
                'audio_url': info['url']
            }

            self.player.add_to_queue(song)

            if not voice_client.is_playing():
                await self.play_next_song(voice_client)

            await self.send_song_info(interaction, song)

    async def play_next_song(self, voice_client):
        next_song = self.player.next_song()
        if next_song:
            voice_client.play(discord.FFmpegPCMAudio(next_song['audio_url']), after=lambda e: self.loop.create_task(self.check_queue(voice_client)))

    async def check_queue(self, voice_client):
        if not voice_client.is_playing():
            next_song = self.player.next_song()
            if next_song:
                voice_client.play(discord.FFmpegPCMAudio(next_song['audio_url']), after=lambda e: self.loop.create_task(self.check_queue(voice_client)))
            else:
                await voice_client.disconnect()

    async def send_song_info(self, interaction, song):
        embed = discord.Embed(
            title="Currently Playing:",
            description=f"**Title:** {song.get('title', 'Unknown')}\n"
                        f"**Creator:** {song.get('uploader', 'Unknown')}\n"
                        f"**Duration:** {song.get('duration', 0)} seconds\n"
                        f"**Views:** {song.get('views', 'Unknown')}\n"
                        f"**Upload Date:** {song.get('upload_date', 'Unknown')}",
            color=discord.Color.blue()
        )
        embed.set_image(url=song.get('thumbnail', ''))

        # Get or initialize guild data for storing messages
        guild_id = interaction.guild.id
        if guild_id not in self.guild_data:
            self.guild_data[guild_id] = {"bot_message": None}

        # If the bot has already sent a message, edit it instead of creating a new one
        if self.guild_data[guild_id]["bot_message"]:
            await self.guild_data[guild_id]["bot_message"].edit(embed=embed)
        else:
            bot_message = await interaction.followup.send(embed=embed)
            self.guild_data[guild_id]["bot_message"] = bot_message

# Initialize Config and Bot
config_manager = ConfigManager('bot_config.json')
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = MusicBot(command_prefix="/", config_manager=config_manager, intents=intents)

# Play Command
@bot.tree.command(name="play", description="Play a song from YouTube")
async def play(interaction: discord.Interaction, url: str):
    await bot.play_song(interaction, url)

# Stop Command
@bot.tree.command(name="stop", description="Stop the current song and clear the queue")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()

    bot.player.clear_queue()

    if not interaction.response.is_done():
        await interaction.response.send_message("Stopped the music and cleared the queue.")
    else:
        print("Interaction already responded to.")

# Next Command
@bot.tree.command(name="next", description="Skip to the next song in the queue")
async def next_song(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()  # Stops the current song, and the `after` event will handle playing the next song
        await interaction.response.send_message("Skipping to the next song...", ephemeral=True)
    else:
        await interaction.response.send_message("No song is currently playing.", ephemeral=True)

# Queue Command
@bot.tree.command(name="queue", description="Display the current song queue")
async def queue(interaction: discord.Interaction):
    if bot.player.queue:
        queue_list = "\n".join([song['title'] for song in bot.player.queue])
        await interaction.response.send_message(f"Current Queue:\n{queue_list}")
    else:
        await interaction.response.send_message("The queue is empty.")

# Start the bot
bot.run(TOKEN)