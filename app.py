import os
import re
import time
import shutil
import tempfile
import threading
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask

app = Flask(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
SECRET_CODE = os.getenv("SECRET_CODE", "JAHID2026").strip()

ADMIN_ID = 7454180235
API = f"https://api.telegram.org/bot{TOKEN}"

authorized_users = {ADMIN_ID}
current_secret = SECRET_CODE

# একই সময়ে সর্বোচ্চ 2টি ডাউনলোড
download_limit = threading.Semaphore(2)


def tg(method, data=None, files=None, timeout=1800):
    try:
        response = requests.post(
            f"{API}/{method}",
            data=data,
            files=files,
            timeout=timeout
        )

        print(method, response.status_code, response.text[:1000])
        return response.json()

    except Exception as error:
        print("Telegram error:", error)
        return {}


def send_message(chat_id, text, reply_to=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_to:
        data["reply_to_message_id"] = reply_to

    result = tg("sendMessage", data)

    return result.get("result", {}).get("message_id")


def delete_message_later(chat_id, message_id, seconds=10):
    def worker():
        time.sleep(seconds)

        if message_id:
            tg(
                "deleteMessage",
                {
                    "chat_id": chat_id,
                    "message_id": message_id
                }
            )

    threading.Thread(target=worker, daemon=True).start()


def temporary_message(chat_id, text, reply_to=None, seconds=10):
    message_id = send_message(chat_id, text, reply_to)
    delete_message_later(chat_id, message_id, seconds)
    return message_id


def get_url(text):
    if not text:
        return None

    match = re.search(r"https?://[^\s]+", text)

    if not match:
        return None

    return match.group(0).rstrip(".,!?)]}>\"'")


def supported_url(url):
    try:
        host = urlparse(url).hostname

        if not host:
            return False

        host = host.lower().replace("www.", "")

        domains = [
            "tiktok.com",
            "tiktokcdn.com",
            "facebook.com",
            "fb.watch",
            "instagram.com",
            "twitter.com",
            "x.com",
            "youtube.com",
            "youtu.be",
            "google.com",
            "vidmard.com",
            "vidmard.net"
        ]

        return any(
            host == domain or host.endswith("." + domain)
            for domain in domains
        )

    except Exception:
        return False


def download_video(url):
    folder = tempfile.mkdtemp(prefix="video_")

    output = os.path.join(
        folder,
        "%(title).80s.%(ext)s"
    )

    command = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--restrict-filenames",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--socket-timeout",
        "30",
        "--max-filesize",
        "2G",
        "-f",
        "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
        "--merge-output-format",
        "mp4",
        "-o",
        output,
        url
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1800
        )

        print("YT-DLP OUTPUT:")
        print(result.stdout[-4000:])

        print("YT-DLP ERROR:")
        print(result.stderr[-4000:])

        if result.returncode != 0:
            return None, folder

        extensions = [
            ".mp4",
            ".mkv",
            ".webm",
            ".mov",
            ".avi"
        ]

        files = [
            file for file in Path(folder).glob("*")
            if file.is_file()
            and file.suffix.lower() in extensions
        ]

        if not files:
            return None, folder

        files.sort(
            key=lambda item: item.stat().st_size,
            reverse=True
        )

        return str(files[0]), folder

    except subprocess.TimeoutExpired:
        print("Download timeout")
        return None, folder

    except Exception as error:
        print("Download exception:", error)
        return None, folder


def send_video_or_document(chat_id, reply_to, file_path):
    size = os.path.getsize(file_path)
    size_mb = round(size / (1024 * 1024), 2)

    # ৫০ MB বা তার বেশি হলে Document হিসেবে পাঠানোর চেষ্টা
    if size_mb >= 50:
        method = "sendDocument"
        file_key = "document"
        caption = (
            "📦 বড় ভিডিও ডকুমেন্ট হিসেবে পাঠানো হয়েছে\n"
            f"📏 সাইজ: {size_mb} MB"
        )
    else:
        method = "sendVideo"
        file_key = "video"
        caption = (
            "🎬 ভিডিও ডাউনলোড সম্পন্ন\n"
            f"📏 সাইজ: {size_mb} MB"
        )

    with open(file_path, "rb") as video_file:
        result = tg(
            method,
            data={
                "chat_id": chat_id,
                "reply_to_message_id": reply_to,
                "caption": caption,
                "supports_streaming": "true"
            },
            files={
                file_key: video_file
            },
            timeout=1800
        )

    return result.get("ok", False), size_mb


def process_download(chat_id, original_message_id, url, status_id):
    with download_limit:

        # Downloading মেসেজ ৫ সেকেন্ড পরে ডিলিট
        delete_message_later(
            chat_id,
            status_id,
            seconds=5
        )

        temporary_message(
            chat_id,
            "📥 ভিডিও ডাউনলোড হচ্ছে। বড় ভিডিও হলে সময় লাগতে পারে।",
            original_message_id,
            seconds=10
        )

        file_path, folder = download_video(url)

        if not file_path:
            temporary_message(
                chat_id,
                "❌ ভিডিও ডাউনলোড করা যায়নি।\n\n"
                "সম্ভাব্য কারণ:\n"
                "• লিংক private বা login-required\n"
                "• ভিডিওটি সাইট থেকে সরানো হয়েছে\n"
                "• সাইটটি ডাউনলোড বন্ধ করেছে\n"
                "• লিংকটি yt-dlp সাপোর্ট করছে না",
                original_message_id,
                seconds=15
            )

            shutil.rmtree(folder, ignore_errors=True)
            return

        try:
            success, size_mb = send_video_or_document(
                chat_id,
                original_message_id,
                file_path
            )

            if not success:
                temporary_message(
                    chat_id,
                    f"❌ Telegram ভিডিওটি পাঠাতে পারেনি।\n"
                    f"📏 ফাইল সাইজ: {size_mb} MB\n\n"
                    "৫০ MB-এর বেশি ফাইল অফিসিয়াল Telegram Bot API-তে "
                    "পাঠানো নাও যেতে পারে।",
                    original_message_id,
                    seconds=15
                )

        finally:
            shutil.rmtree(folder, ignore_errors=True)


def handle_message(message):
    global current_secret

    if not message:
        return

    chat_id = message["chat"]["id"]
    message_id = message["message_id"]

    user = message.get("from", {})
    user_id = user.get("id")
    first_name = user.get("first_name", "User")

    text = message.get("text", "").strip()

    if not text:
        return

    # Start
    if text.startswith("/start"):
        if user_id in authorized_users or user_id == ADMIN_ID:
            send_message(
                chat_id,
                f"👋 আসসালামু আলাইকুম, {first_name} স্যার!\n\n"
                "📥 Supported:\n"
                "🎵 TikTok / TikTok Lite\n"
                "📘 Facebook / Facebook Lite\n"
                "📹 Vidmard\n"
                "▶️ YouTube\n"
                "🐦 Twitter / X\n"
                "📸 Instagram / Instagram Lite\n"
                "🔗 শুধু ভিডিও লিংক পাঠান\n"
                "🎬 আমি ভিডিও ডাউনলোড করে দিব\n\n"
                "👨‍💻 Admin: @JAHIDVAI12"
            )
        else:
            temporary_message(
                chat_id,
                "🔐 বট ব্যবহার করতে Secret Code দিন।",
                seconds=10
            )

        return

    # Secret code পরিবর্তন
    if text.startswith("/setcode"):
        if user_id != ADMIN_ID:
            temporary_message(
                chat_id,
                "❌ শুধু Admin এই কমান্ড ব্যবহার করতে পারবেন।",
                seconds=10
            )
            return

        parts = text.split(maxsplit=1)

        if len(parts) != 2 or len(parts[1].strip()) < 4:
            temporary_message(
                chat_id,
                "ব্যবহার করুন:\n/setcode নতুনকোড",
                seconds=10
            )
            return

        current_secret = parts[1].strip()

        temporary_message(
            chat_id,
            "✅ Secret Code পরিবর্তন হয়েছে।",
            seconds=10
        )
        return

    # User add
    if text.startswith("/adduser"):
        if user_id != ADMIN_ID:
            temporary_message(
                chat_id,
                "❌ শুধু Admin এই কমান্ড ব্যবহার করতে পারবেন।",
                seconds=10
            )
            return

        parts = text.split(maxsplit=1)

        if len(parts) != 2 or not parts[1].isdigit():
            temporary_message(
                chat_id,
                "ব্যবহার করুন:\n/adduser USER_ID",
                seconds=10
            )
            return

        new_id = int(parts[1])
        authorized_users.add(new_id)

        temporary_message(
            chat_id,
            f"✅ User অনুমোদিত হয়েছে: {new_id}",
            seconds=10
        )
        return

    # User remove
    if text.startswith("/removeuser"):
        if user_id != ADMIN_ID:
            temporary_message(
                chat_id,
                "❌ শুধু Admin এই কমান্ড ব্যবহার করতে পারবেন।",
                seconds=10
            )
            return

        parts = text.split(maxsplit=1)

        if len(parts) != 2 or not parts[1].isdigit():
            temporary_message(
                chat_id,
                "ব্যবহার করুন:\n/removeuser USER_ID",
                seconds=10
            )
            return

        remove_id = int(parts[1])
        authorized_users.discard(remove_id)

        temporary_message(
            chat_id,
            f"✅ User-এর অনুমতি বাতিল হয়েছে: {remove_id}",
            seconds=10
        )
        return

    # Authorization
    if user_id not in authorized_users and user_id != ADMIN_ID:
        if text == current_secret:
            authorized_users.add(user_id)

            temporary_message(
                chat_id,
                "✅ Access Granted!\n\nএখন ভিডিও লিংক পাঠান।",
                seconds=10
            )
        else:
            temporary_message(
                chat_id,
                "❌ Secret Code ভুল।",
                seconds=10
            )

        return

    # Link
    url = get_url(text)

    if not url:
        temporary_message(
            chat_id,
            "🔗 অনুগ্রহ করে ভিডিও লিংক পাঠান।",
            seconds=10
        )
        return

    if not supported_url(url):
        temporary_message(
            chat_id,
            "❌ এই লিংকটি সাপোর্ট করা হচ্ছে না।",
            message_id,
            seconds=10
        )
        return

    status_id = send_message(
        chat_id,
        "⏳ Downloading Video...",
        message_id
    )

    threading.Thread(
        target=process_download,
        args=(
            chat_id,
            message_id,
            url,
            status_id
        ),
        daemon=True
    ).start()


def polling():
    print("Polling started...")

    # পুরোনো webhook মুছে ফেলা
    tg(
        "deleteWebhook",
        {
            "drop_pending_updates": "true"
        }
    )

    offset = None

    while True:
        try:
            params = {
                "timeout": 50,
                "allowed_updates": ["message"]
            }

            if offset is not None:
                params["offset"] = offset

            response = requests.get(
                f"{API}/getUpdates",
                params=params,
                timeout=65
            )

            data = response.json()

            if not data.get("ok"):
                print("GetUpdates error:", data)
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                threading.Thread(
                    target=handle_message,
                    args=(update.get("message"),),
                    daemon=True
                ).start()

        except Exception as error:
            print("Polling error:", error)
            time.sleep(5)


@app.route("/")
def home():
    return "Video Downloader Bot is running."


@app.route("/health")
def health():
    return "OK"


if __name__ == "__main__":
    threading.Thread(
        target=polling,
        daemon=True
    ).start()

    port = int(os.getenv("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port
            )
