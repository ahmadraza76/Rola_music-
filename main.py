import logging
import asyncio
import json
import os
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pytgcalls import PyTgCalls
from pytgcalls.types import StreamType
from pytgcalls.types.input_stream import AudioPiped
import yt_dlp as youtube_dl
from yt_dlp.utils import DownloadError
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import aiofiles
import aiohttp
from config import API_ID, API_HASH, BOT_TOKEN, OWNER_ID, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

# ✅ Keep Alive Server
from keep_alive import keep_alive
keep_alive()

# ✅ Logging Setup
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("rolavibe.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ✅ Bot Client
app = Client("RolaVibeBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
call_py = PyTgCalls(app)

# ✅ Global Variables
queue = {}
queue_lock = asyncio.Lock()
is_call_active = False
maintenance_mode = False
MAINTENANCE_FILE = "maintenance_mode.json"
FM_CHANNELS = {
    "Radio Mirchi": "http://example.com/radiomirchi",
    "Red FM": "http://example.com/redfm",
    "Big FM": "http://example.com/bigfm"
}

# ✅ AI Status
AI_ENABLED = False  # Default: AI is disabled

# ✅ Cache Files
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# ✅ YouTube-DL Options
ydl_opts = {
    'format': 'bestaudio',
    'quiet': True,
    'noplaylist': True
}

# ✅ Spotify API Initialization
sp = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        ))
    except Exception as e:
        logger.error(f"Spotify API Initialization Error: {e}")

# ✅ Helper Functions
async def load_maintenance_mode():
    global maintenance_mode
    try:
        async with aiofiles.open(MAINTENANCE_FILE, "r") as f:
            data = await f.read()
            maintenance_mode = json.loads(data) if data else False
    except (FileNotFoundError, json.JSONDecodeError):
        maintenance_mode = False

async def save_maintenance_mode():
    try:
        async with aiofiles.open(MAINTENANCE_FILE, "w") as f:
            await f.write(json.dumps(maintenance_mode))
    except Exception as e:
        logger.error(f"❌ Maintenance Mode Save Error: {e}")

async def ensure_files_exist():
    files = ["queue.json", "admin_commands.json", "allowed_groups.json", "fm_channels.json"]
    for file in files:
        if not os.path.exists(file):
            async with aiofiles.open(file, "w") as f:
                await f.write(json.dumps({}))

async def load_queue():
    global queue
    try:
        async with aiofiles.open("queue.json", "r") as f:
            data = await f.read()
            queue = json.loads(data) if data else {}
    except (FileNotFoundError, json.JSONDecodeError):
        queue = {}

async def save_queue():
    try:
        async with aiofiles.open("queue.json", "w") as f:
            await f.write(json.dumps(queue))
    except Exception as e:
        logger.error(f"❌ Queue Save Error: {e}")

async def auto_save():
    while True:
        await asyncio.sleep(120)
        try:
            await save_queue()
            await save_fm_channels()
            await save_maintenance_mode()
        except Exception as e:
            logger.error(f"❌ Auto-Save Error: {e}")

async def is_admin_and_allowed(chat_id, user_id, command):
    try:
        member = await app.get_chat_member(chat_id, user_id)
        if member.status not in ["administrator", "creator"]:
            return False
        async with aiofiles.open("admin_commands.json", "r") as f:
            data = json.loads(await f.read())
            return command in data.get("allowed_admin_commands", [])
    except Exception as e:
        logger.error(f"Admin Check Error: {e}")
        return False

# ✅ Cache Function
async def get_cached_data(cache_key):
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.json")
    if os.path.exists(cache_file):
        async with aiofiles.open(cache_file, "r") as f:
            return json.loads(await f.read())
    return None

async def save_cached_data(cache_key, data):
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.json")
    async with aiofiles.open(cache_file, "w") as f:
        await f.write(json.dumps(data))

# ✅ Async YouTube Search with Caching
async def get_youtube_video(query):
    cache_key = f"youtube_{query}"
    cached_data = await get_cached_data(cache_key)
    if cached_data:
        return cached_data

    loop = asyncio.get_event_loop()
    try:
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(f"ytsearch:{query}", download=False))
            if "entries" in info and len(info["entries"]) > 0:
                video = info["entries"][0]
                await save_cached_data(cache_key, video)
                return video
            return None
    except Exception as e:
        logger.error(f"❌ YouTube Search Error: {e}")
        return None

# ✅ Async Spotify API Call with Caching
async def get_spotify_song_details(query):
    cache_key = f"spotify_{query}"
    cached_data = await get_cached_data(cache_key)
    if cached_data:
        return cached_data

    try:
        if not sp:
            return None
        results = sp.search(q=query, limit=1)
        if results["tracks"]["items"]:
            track = results["tracks"]["items"][0]
            song_details = {
                "title": track["name"],
                "artist": track["artists"][0]["name"],
                "url": track["external_urls"]["spotify"]
            }
            await save_cached_data(cache_key, song_details)
            return song_details
        return None
    except Exception as e:
        logger.error(f"❌ Spotify API Error: {e}")
        return None

def get_thumbnail(video_id):
    return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"

async def is_group_allowed(chat_id):
    try:
        async with aiofiles.open("allowed_groups.json", "r") as f:
            data = await f.read()
            allowed_groups = json.loads(data) if data else {}
            return str(chat_id) in allowed_groups
    except (FileNotFoundError, json.JSONDecodeError):
        return False

# ✅ Commands
@app.on_message(filters.command("start"))
async def start(client, message: Message):
    if message.chat.type == "supergroup" and not await is_group_allowed(message.chat.id):
        return await message.reply_text("⚠️ यह ग्रुप बॉट का उपयोग करने के लिए अधिकृत नहीं है। कृपया बॉट ओनर से संपर्क करें।")

    if maintenance_mode and message.from_user.id != OWNER_ID:
        return await message.reply_text("⚠️ बॉट वर्तमान में मेन्टेनेंस मोड में है। कृपया बाद में पुनः प्रयास करें।")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎶 गाना चलाएं", callback_data="play_music"),
         InlineKeyboardButton("🔊 वॉल्यूम", callback_data="volume_control")],
        [InlineKeyboardButton("📌 मेरी प्लेलिस्ट", callback_data="my_playlist"),
         InlineKeyboardButton("🎵 अभी चल रहा है", callback_data="now_playing")],
        [InlineKeyboardButton("⚙️ सेटिंग्स", callback_data="settings"),
         InlineKeyboardButton("📢 अपडेट्स", url="https://t.me/RolaVibeUpdates")],
        [InlineKeyboardButton("📻 रेडियो", callback_data="radio")]
    ])

    if message.from_user.id == OWNER_ID:
        keyboard.inline_keyboard.append([InlineKeyboardButton("👑 ओनर पैनल", callback_data="owner_panel")])

    await message.reply_text(
        "**✨ Rola Vibe में आपका स्वागत है! 🎶**\n\n"
        "🎧 *अपने ग्रुप में हाई-क्वालिटी म्यूजिक स्ट्रीमिंग का आनंद लें।*\n"
        "🎶 *बस एक कमांड से अपने पसंदीदा गाने चलाएं!*\n\n"
        "📌 *नवीनतम अपडेट्स के लिए जुड़ें* [@RolaVibeUpdates](https://t.me/RolaVibeUpdates)\n\n"
        "👨‍💻 *डेवलपर:* [Mr Nick](https://t.me/5620922625)",
        reply_markup=keyboard,
        disable_web_page_preview=True
    )

# ✅ Help Command (Admin and Owner Commands Info)
@app.on_message(filters.command("help"))
async def help_command(client, message: Message):
    help_text = (
        "✨ **Rola Vibe Bot Help Menu** ✨\n\n"
        "🎵 **सभी के लिए:**\n"
        "▫️ .start - बॉट को स्टार्ट करें और वेलकम मैसेज देखें।\n"
        "▫️ .help - यह हेल्प मेनू देखें।\n\n"
        "🔧 **एडमिन कमांड्स:**\n"
        "▫️ .play <song_name> - गाना चलाएं (केवल एडमिन)।\n"
        "▫️ .stop - प्लेबैक रोकें (केवल एडमिन)।\n"
        "▫️ .pause - प्लेबैक पॉज़ करें (केवल एडमिन)।\n"
        "▫️ .resume - प्लेबैक रिज्यूम करें (केवल एडमिन)।\n"
        "▫️ .skip - अगला गाना चलाएं (केवल एडमिन)।\n\n"
        "👑 **ओनर कमांड्स:**\n"
        "▫️ .enableadmin <command> - एडमिन कमांड को सक्षम करें।\n"
        "▫️ .disableadmin <command> - एडमिन कमांड को अक्षम करें।\n"
        "▫️ .playvideo <video_url> - वीडियो चलाएं (केवल ओनर)।\n"
        "▫️ .addgroup - ग्रुप को बॉट में जोड़ें (केवल ओनर)।\n\n"
        "📌 *नोट:* एडमिन कमांड्स केवल ग्रुप एडमिन और बॉट ओनर ही उपयोग कर सकते हैं।\n"
        "🎧 *Rola Vibe का आनंद लें!* 🎶"
    )

    await message.reply_text(
        help_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 ROLA CHAT", url="https://t.me/RolaVibeChat"),
             InlineKeyboardButton("👨‍💻 DEVELOPER", url="https://t.me/5620922625)]
        ])
    )

# 🎵 Play/Rola Command (Admin Check)
@app.on_message(filters.command(["play", "rola"], prefixes=".") & filters.group)
async def play_rola_command(client, message: Message):
    if not await is_group_allowed(message.chat.id):
        return await message.reply_text("⚠️ यह ग्रुप बॉट का उपयोग करने के लिए अधिकृत नहीं है। कृपया बॉट ओनर से संपर्क करें।")

    global is_call_active
    chat_id = message.chat.id
    user = message.from_user

    if maintenance_mode and user.id != OWNER_ID:
        return await message.reply_text("⚠️ बॉट वर्तमान में मेन्टेनेंस मोड में है। कृपया बाद में पुनः प्रयास करें।")

    if not await is_admin_and_allowed(chat_id, user.id, "play"):
        return await message.reply_text("⚠️ *केवल एडमिन इस कमांड का उपयोग कर सकते हैं!*")

    query = " ".join(message.command[1:]) if len(message.command) > 1 else None
    if not query:
        return await message.reply_text("⚠️ *कृपया गाने का नाम दर्ज करें!*")

    await message.delete()
    searching_msg = await message.reply_text("🔍 *खोज रहा हूँ...*")

    try:
        # Fetch song details from Spotify
        spotify_song = await get_spotify_song_details(query)
        if not spotify_song:
            return await searching_msg.edit("⚠️ *स्पॉटिफाई पर कोई परिणाम नहीं मिला। कृपया कोई अन्य नाम आज़माएं।*")

        # Search YouTube for the song
        info = await get_youtube_video(f"{spotify_song['title']} {spotify_song['artist']}")
        if "entries" in info and len(info["entries"]) > 0:
            video = info["entries"][0]
        else:
            raise DownloadError("No results found.")
            
        video_url = video["url"]
        title = video["title"]
        video_id = video["id"]
        duration = video["duration"]

        # Check song duration
        if duration > 600:  # 10 minutes
            return await searching_msg.edit("⚠️ *गाना बहुत लंबा है। अधिकतम अनुमत अवधि 10 मिनट है।*")
    except DownloadError:
        return await searching_msg.edit("⚠️ *कोई परिणाम नहीं मिला। कृपया कोई अन्य नाम आज़माएं।*")
    except Exception as e:
        logger.error(f"Play Command Error: {e}")
        return await searching_msg.edit("⚠️ *एक त्रुटि हुई। कृपया बाद में पुनः प्रयास करें।*")

    await searching_msg.delete()

    # Add song to queue
    async with queue_lock:
        queue.setdefault(chat_id, []).append((video_url, title, video_id))
        await save_queue()

    # Join voice call if not already joined
    if not is_call_active:
        await call_py.join_group_call(
            chat_id,
            AudioPiped(video_url, stream_type=StreamType().pulse_stream)
        )
        is_call_active = True

    # Send now playing message with Expand option
    await message.reply_photo(
        photo=get_thumbnail(video_id),
        caption=f"🎵 **अभी चल रहा है:** `{title}`\n"
                f"🔗 [YouTube पर देखें](https://youtu.be/{video_id})\n\n"
                "🎧 *Rola Vibe का आनंद लें!*",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏸️ पॉज़", callback_data="pause"),
             InlineKeyboardButton("▶️ रिज्यूम", callback_data="resume"),
             InlineKeyboardButton("⏭️ स्किप", callback_data="skip"),
             InlineKeyboardButton("⏹️ रोकें", callback_data="stop")],
            [InlineKeyboardButton("🔍 विस्तार करें", callback_data="expand")]
        ])
    )

# ✅ Expand Callback
@app.on_callback_query(filters.regex("^expand$"))
async def expand_callback(client, callback_query):
    chat_id = callback_query.message.chat.id
    if "content" in queue.get(chat_id, {}):
        content = queue[chat_id]["content"]
        await callback_query.edit_message_text(
            f"🔍 **विस्तृत सामग्री:**\n\n{content}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏏️ संक्षिप्त करें", callback_data="collapse")]
            ])
        )
    else:
        await callback_query.answer("⚠️ विस्तार करने के लिए कोई सामग्री उपलब्ध नहीं है।", show_alert=True)

# ✅ Collapse Callback
@app.on_callback_query(filters.regex("^collapse$"))
async def collapse_callback(client, callback_query):
    await callback_query.edit_message_text("सामग्री संक्षिप्त की गई।")

# 🎵 Stop Command (Admin Check)
@app.on_message(filters.command("stop", prefixes=".") & filters.group)
async def stop(client, message: Message):
    global is_call_active
    chat_id = message.chat.id
    user = message.from_user

    if not await is_admin_and_allowed(chat_id, user.id, "stop"):
        return await message.reply_text("⚠️ *केवल एडमिन इस कमांड का उपयोग कर सकते हैं!*")

    async with queue_lock:
        queue.pop(chat_id, None)
        await save_queue()

    if is_call_active:
        await call_py.leave_group_call(chat_id)
        is_call_active = False
    await message.reply_text("🛑 *प्लेबैक रोक दिया गया है।*")

# ✅ Owner Commands: Enable/Disable Admin Commands
@app.on_message(filters.command("enableadmin", prefixes=".") & filters.user(OWNER_ID))
async def enable_admin_command(client, message: Message):
    cmd = message.text.split(" ", 1)[1].strip()
    async with aiofiles.open("admin_commands.json", "r+") as f:
        data = json.loads(await f.read())
        if cmd not in data["allowed_admin_commands"]:
            data["allowed_admin_commands"].append(cmd)
            await f.seek(0)
            await f.write(json.dumps(data))
            return await message.reply_text(f"✅ *एडमिन कमांड `{cmd}` सक्षम की गई!*")

@app.on_message(filters.command("disableadmin", prefixes=".") & filters.user(OWNER_ID))
async def disable_admin_command(client, message: Message):
    cmd = message.text.split(" ", 1)[1].strip()
    async with aiofiles.open("admin_commands.json", "r+") as f:
        data = json.loads(await f.read())
        if cmd in data["allowed_admin_commands"]:
            data["allowed_admin_commands"].remove(cmd)
            await f.seek(0)
            await f.write(json.dumps(data))
            return await message.reply_text(f"✅ *एडमिन कमांड `{cmd}` अक्षम की गई!*")

# 🎥 Play Video Command (Owner Only)
@app.on_message(filters.command("playvideo", prefixes=".") & filters.user(OWNER_ID))
async def play_video_command(client, message: Message):
    global is_call_active
    chat_id = message.chat.id
    user = message.from_user

    # Check if user is the bot owner
    if user.id != OWNER_ID:
        return await message.reply_text("⚠️ *केवल बॉट ओनर इस कमांड का उपयोग कर सकते हैं!*")

    # Get video URL from command
    video_url = " ".join(message.command[1:]) if len(message.command) > 1 else None
    if not video_url:
        return await message.reply_text("⚠️ *कृपया वीडियो URL दर्ज करें!*")

    await message.delete()
    searching_msg = await message.reply_text("🔍 *वीडियो प्रोसेस किया जा रहा है...*")

    try:
        # Use yt-dlp to extract video info
        loop = asyncio.get_event_loop()
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(video_url, download=False))
            if not info:
                return await searching_msg.edit("⚠️ *दिए गए URL पर कोई वीडियो नहीं मिला।*")

            video_title = info.get("title", "Unknown Title")
            video_url = info.get("url")  # Direct video stream URL
            video_duration = info.get("duration", 0)

            # Check video duration (max 3 hours = 180 minutes = 10800 seconds)
            if video_duration > 10800:
                return await searching_msg.edit("⚠️ *वीडियो बहुत लंबा है। अधिकतम अनुमत अवधि 3 घंटे है।*")

    except DownloadError:
        return await searching_msg.edit("⚠️ *अमान्य URL या असमर्थित वेबसाइट।*")
    except Exception as e:
        logger.error(f"Video Play Error: {e}")
        return await searching_msg.edit("⚠️ *एक त्रुटि हुई। कृपया बाद में पुनः प्रयास करें।*")

    await searching_msg.delete()

    # Add video to queue
    async with queue_lock:
        queue.setdefault(chat_id, []).append((video_url, video_title, "video"))
        await save_queue()

    # Join voice call if not already joined
    if not is_call_active:
        await call_py.join_group_call(
            chat_id,
            AudioPiped(video_url, stream_type=StreamType().pulse_stream)
        )
        is_call_active = True

    # Send now playing message
    await message.reply_text(
        f"🎥 **अभी चल रहा वीडियो:** `{video_title}`\n"
        f"🔗 [वीडियो देखें]({video_url})\n\n"
        "🎧 *Rola Vibe का आनंद लें!*",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏸️ पॉज़", callback_data="pause"),
             InlineKeyboardButton("▶️ रिज्यूम", callback_data="resume"),
             InlineKeyboardButton("⏭️ स्किप", callback_data="skip"),
             InlineKeyboardButton("⏹️ रोकें", callback_data="stop")],
            [InlineKeyboardButton("🔍 विस्तार करें", callback_data="expand")]
        ])
    )

# ✅ Owner Panel Callback
@app.on_callback_query(filters.regex("^owner_panel$"))
async def owner_panel_callback(client, callback_query):
    user = callback_query.from_user

    # ✅ Check if user is the bot owner
    if user.id != OWNER_ID:
        await callback_query.answer("⚠️ केवल बॉट ओनर इस पैनल तक पहुंच सकते हैं!", show_alert=True)
        return

    # ✅ Owner Panel Options
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 बॉट स्टैट्स", callback_data="bot_stats"),
         InlineKeyboardButton("📢 ब्रॉडकास्ट", callback_data="broadcast")],
        [InlineKeyboardButton("🔧 मेन्टेनेंस", callback_data="maintenance"),
         InlineKeyboardButton("🔒 एडमिन कमांड्स", callback_data="admin_commands")],
        [InlineKeyboardButton("📝 लॉग्स चेक करें", callback_data="check_logs"),
         InlineKeyboardButton("🔙 वापस", callback_data="back_to_start")]
    ])

    await callback_query.edit_message_text(
        "👑 **ओनर पैनल**\n\n"
        "बॉट ओनर के कंट्रोल पैनल में आपका स्वागत है। नीचे दिए गए विकल्पों में से एक चुनें:",
        reply_markup=keyboard
    )

# ✅ Bot Stats Callback
@app.on_callback_query(filters.regex("^bot_stats$"))
async def bot_stats_callback(client, callback_query):
    user = callback_query.from_user

    if user.id != OWNER_ID:
        await callback_query.answer("⚠️ केवल बॉट ओनर इस पैनल तक पहुंच सकते हैं!", show_alert=True)
        return

    # ✅ Fetch Bot Stats (Example)
    total_users = 1000  # Replace with actual logic to fetch stats
    total_groups = 50   # Replace with actual logic to fetch stats

    await callback_query.edit_message_text(
        f"📊 **बॉट स्टैटिस्टिक्स**\n\n"
        f"👤 कुल यूजर्स: `{total_users}`\n"
        f"👥 कुल ग्रुप्स: `{total_groups}`\n\n"
        "🔙 वापस जाने के लिए नीचे दिए गए बटन पर क्लिक करें।",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 वापस", callback_data="owner_panel")]
        ])
    )

# ✅ Broadcast Message Callback
@app.on_callback_query(filters.regex("^broadcast$"))
async def broadcast_callback(client, callback_query):
    user = callback_query.from_user

    if user.id != OWNER_ID:
        await callback_query.answer("⚠️ केवल बॉट ओनर इस पैनल तक पहुंच सकते हैं!", show_alert=True)
        return

    await callback_query.edit_message_text(
        "📢 **ब्रॉडकास्ट मैसेज**\n\n"
        "वह मैसेज भेजें जिसे आप सभी यूजर्स/ग्रुप्स को ब्रॉडकास्ट करना चाहते हैं।\n\n"
        "🔙 वापस जाने के लिए नीचे दिए गए बटन पर क्लिक करें।",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 वापस", callback_data="owner_panel")]
        ])
    )

# ✅ Maintenance Mode Callback
@app.on_callback_query(filters.regex("^maintenance$"))
async def maintenance_callback(client, callback_query):
    global maintenance_mode
    user = callback_query.from_user

    if user.id != OWNER_ID:
        await callback_query.answer("⚠️ केवल बॉट ओनर इस पैनल तक पहुंच सकते हैं!", show_alert=True)
        return

    # ✅ Toggle Maintenance Mode
    maintenance_mode = not maintenance_mode

    await callback_query.edit_message_text(
        f"🔧 **मेन्टेनेंस मोड**\n\n"
        f"मेन्टेनेंस मोड वर्तमान में `{'ON' if maintenance_mode else 'OFF'}` है।\n\n"
        "🔙 वापस जाने के लिए नीचे दिए गए बटन पर क्लिक करें।",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 वापस", callback_data="owner_panel")]
        ])
    )

# ✅ Admin Commands Management Callback
@app.on_callback_query(filters.regex("^admin_commands$"))
async def admin_commands_callback(client, callback_query):
    user = callback_query.from_user

    if user.id != OWNER_ID:
        await callback_query.answer("⚠️ केवल बॉट ओनर इस पैनल तक पहुंच सकते हैं!", show_alert=True)
        return

    # ✅ Fetch Admin Commands (Example)
    admin_commands = [".play", ".stop", ".pause", ".resume", ".skip"]

    await callback_query.edit_message_text(
        f"🔒 **एडमिन कमांड्स मैनेजमेंट**\n\n"
        f"वर्तमान में अनुमत एडमिन कमांड्स:\n"
        f"{', '.join(admin_commands)}\n\n"
        "🔙 वापस जाने के लिए नीचे दिए गए बटन पर क्लिक करें।",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 वापस", callback_data="owner_panel")]
        ])
    )

# ✅ Check Logs Callback
@app.on_callback_query(filters.regex("^check_logs$"))
async def check_logs_callback(client, callback_query):
    user = callback_query.from_user

    if user.id != OWNER_ID:
        await callback_query.answer("⚠️ केवल बॉट ओनर इस पैनल तक पहुंच सकते हैं!", show_alert=True)
        return

    # ✅ Send Logs File (Example)
    try:
        await client.send_document(
            chat_id=user.id,
            document="rolavibe.log",
            caption="📝 **बॉट लॉग्स**\n\nयहां नवीनतम लॉग्स हैं।"
        )
    except Exception as e:
        logger.error(f"Logs Send Error: {e}")
        await callback_query.answer("⚠️ लॉग्स भेजने में विफल। कृपया लॉग फ़ाइल मैन्युअल रूप से जांचें।", show_alert=True)

    await callback_query.answer("लॉग्स आपके प्राइवेट चैट में भेजे गए हैं।", show_alert=True)

# ✅ Back to Start Callback
@app.on_callback_query(filters.regex("^back_to_start$"))
async def back_to_start_callback(client, callback_query):
    user = callback_query.from_user

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎶 गाना चलाएं", callback_data="play_music"),
         InlineKeyboardButton("🔊 वॉल्यूम", callback_data="volume_control")],
        [InlineKeyboardButton("📌 मेरी प्लेलिस्ट", callback_data="my_playlist"),
         InlineKeyboardButton("🎵 अभी चल रहा है", callback_data="now_playing")],
        [InlineKeyboardButton("⚙️ सेटिंग्स", callback_data="settings"),
         InlineKeyboardButton("📢 अपडेट्स", url="https://t.me/RolaVibeUpdates")],
        [InlineKeyboardButton("📻 रेडियो", callback_data="radio")]
    ])

    # ✅ Add Owner-Specific Option
    if user.id == OWNER_ID:
        keyboard.inline_keyboard.append([InlineKeyboardButton("👑 ओनर पैनल", callback_data="owner_panel")])

    await callback_query.edit_message_text(
        "**✨ Rola Vibe में आपका स्वागत है! 🎶**\n\n"
        "🎧 *अपने ग्रुप में हाई-क्वालिटी म्यूजिक स्ट्रीमिंग का आनंद लें।*\n"
        "🎶 *बस एक कमांड से अपने पसंदीदा गाने चलाएं!*\n\n"
        "📌 *नवीनतम अपडेट्स के लिए जुड़ें* [@RolaVibeUpdates](https://t.me/RolaVibeUpdates)\n\n"
        "👨‍💻 *डेवलपर:* [Mr Nick](https://t.me/5620922625)",
        reply_markup=keyboard
    )

# ✅ Owner Commands: AI On/Off
@app.on_message(filters.command("ai on") & filters.user(OWNER_ID))
async def enable_ai(client, message: Message):
    global AI_ENABLED
    AI_ENABLED = True
    await message.reply_text("✅ AI enabled. Bot will now use AI for all functions.")

@app.on_message(filters.command("ai off") & filters.user(OWNER_ID))
async def disable_ai(client, message: Message):
    global AI_ENABLED
    AI_ENABLED = False
    await message.reply_text("✅ AI disabled. Bot will now work normally.")

# ✅ AI-Powered Functionality
async def handle_ai_functionality():
    if AI_ENABLED:
        # AI logic here (e.g., recommendations, automatic handling)
        pass
    else:
        # Normal bot functionality
        pass

# 🔥 Run Bot
async def main():
    try:
        await ensure_files_exist()
        await load_queue()
        await load_fm_channels()
        await load_maintenance_mode()
        await app.start()
        await call_py.start()
        await idle()
    except Exception as e:
        logger.error(f"❌ Bot Startup Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
