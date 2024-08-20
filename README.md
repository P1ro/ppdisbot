# ppdisbot

discord bot 


    Track the Last Message: Store the message ID and the channel ID of the last message sent (similar to how you were doing before with current_message_id and current_channel_id).
    Check If a Message Exists: If a message has already been sent, edit it instead of sending a new one.
    Fallback: If the message doesn’t exist or the bot can’t find it, send a new message and start tracking it.

@discord.ui.button(label="Play/Pause", style=discord.ButtonStyle.green)
async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
    # Get the voice client for the guild
    voice_client = discord.utils.get(self.bot.voice_clients, guild=interaction.guild)

    # Check if the bot is connected to a voice channel
    if voice_client is None or not voice_client.is_connected():
        await interaction.response.send_message("Bot is not connected to a voice channel.", ephemeral=True)
        return

    # Message editing logic
    if current_message_id and current_channel_id:
        try:
            # Try to fetch the existing message
            message_channel = self.bot.get_channel(current_channel_id)
            message = await message_channel.fetch_message(current_message_id)
        except discord.errors.NotFound:
            message = None
    else:
        message = None

    # Toggle between play and pause
    if voice_client.is_playing():
        voice_client.pause()  # Pause the current song
        if message:
            await message.edit(content="Music paused.")
        else:
            new_message = await interaction.response.send_message("Music paused.", ephemeral=True)
            current_message_id = new_message.id
            current_channel_id = new_message.channel.id
    elif voice_client.is_paused():
        voice_client.resume()  # Resume the current song
        if message:
            await message.edit(content="Music resumed.")
        else:
            new_message = await interaction.response.send_message("Music resumed.", ephemeral=True)
            current_message_id = new_message.id
            current_channel_id = new_message.channel.id
    else:
        # If nothing is playing or paused, inform the user
        await interaction.response.send_message("No music is currently playing.", ephemeral=True)



@discord.ui.button(label="Stop", style=discord.ButtonStyle.red)
async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
    # Message editing logic
    if current_message_id and current_channel_id:
        try:
            message_channel = self.bot.get_channel(current_channel_id)
            message = await message_channel.fetch_message(current_message_id)
        except discord.errors.NotFound:
            message = None
    else:
        message = None

    # Stopping logic
    stop_command = self.bot.tree.get_command("stop")
    if stop_command:
        await stop_command.callback(interaction)
    
    # Edit the message or send a new one
    if message:
        await message.edit(content="Music stopped.")
    else:
        new_message = await interaction.response.send_message("Music stopped.", ephemeral=True)
        current_message_id = new_message.id
        current_channel_id = new_message.channel.id
