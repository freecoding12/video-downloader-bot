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

# Render Environment Variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
INITIAL_SECRET_CODE = os.environ.get("SECRET_CODE", "JAHID2026").strip()

ADMIN_ID = 7454180235
secret_code = INITIAL_SECRET_CODE

API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Runtime-এ অনুমোদিত ইউজার
authorized_users = set()

# একই সময়ে বেশি ডাউনলোড চালু না করার জন্য
download_lock = threading.Semaphore(2)


def telegram(method, data=None, files=None, timeout=120):
    try:
        response = requests.post(
            f"{API}/{method}",
            data=data,
            files=files,
            timeout=timeout
        )

        print(method, response.status_code, response.text[:500])
        return response.json()

    except Exception as error:
        print("Telegram API Error:", error)
        return {}


def send_message(chat_id, text, reply_to=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_to:
        data["reply_to_message_id"] = reply_to

    result = telegram("sendMessage", data)

    return result.get("result", {}).get("message_id")


def delete_message(chat_id, message_id):
    if message_id:
        telegram(
            "deleteMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id
            }
        )


def is_authorized(user_id):
    return user_id == ADMIN_ID or user_id in authorized_users


def extract_url(text):
    if not text:
        return None

    match = re.search(r"https?://[^\s]+", text)

    if not match:
        return None

    return match.group(0).rstrip(".,!?)]}>\"'")


def is_supported_url(url):
    try:
        hostname = urlparse(url).hostname

        if not hostname:
            return False

        hostname = hostname.lower().replace("www.", "")

        supported_domains = [
            "tiktok.com",
            "tiktokcdn.com",
            "facebook.com",
            "fb.watch",
            "instagram.com",
            "twitter.com",
            "x.com",
            "youtube.com",
            "youtu.be",
            "vidmard.com",
            "vidmard.net",
            "google.com"
        ]

        return any(
            hostname == domain or hostname.endswith("." + domain)
            for domain in supported_domains
        )

    except Exception:
        return False


def start_message(name):
    return (
        f"👋 আসসালামু আলাইকুম, {name} স্যার!\n\n"
        "📥 Supported Platforms:\n\n"
        "🎵 TikTok\n"
        "🎵 TikTok Lite\n"
        "📘 Facebook\n"
        "📘 Facebook Lite\n"
        "📹 Vidmard\n"
        "▶️ YouTube\n"
        "🌐 Chrome\n"
        "🔎 Google\n"
        "🔞 18+ Video\n"
        "🐦 Twitter / X\n"
        "📸 Instagram\n"
        "📸 Instagram Lite\n\n"
        "🔗 শুধু ভিডিও লিংক পাঠান\n"
        "🎬 আমি ভিডিও ডাউনলোড করে দিব\n\n"
        "👨‍💻 Admin: @JAHIDVAI12"
    )


def download_video(url):
    temp_dir = tempfile.mkdtemp(prefix="download_")

    output_template = os.path.join(
        temp_dir,
        "%(title).80s.%(ext)s"
    )

    command = [
        "yt-dlp",
        "--no-playlist",
        "--restrict-filenames",
        "--max-filesize",
        "2G",
        "-f",
        "bv*+ba/b",
        "--merge-output-format",
        "mp4",
        "-o",
        output_template,
        url
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1800
        )

        print("Downloader output:", result.stdout[-2000:])
        print("Downloader error:", result.stderr[-2000:])

        if result.returncode != 0:
            return None, temp_dir

        allowed_extensions = [
            ".mp4",
            ".mkv",
            ".webm",
            ".mov"
        ]

        video_files = [
            file for file in Path(temp_dir).glob("*")
            if file.is_file()
            and file.suffix.lower() in allowed_extensions
        ]

        if not video_files:
            return None, temp_dir

        # সবচেয়ে বড় ভিডিও ফাইলটি নির্বাচন
        video_files.sort(
            key=lambda file: file.stat().st_size,
            reverse=True
        )

        return str(video_files[0]), temp_dir

    except Exception as error:
        print("Download error:", error)
        return None, temp_dir


def send_downloaded_video(chat_id, original_message_id, video_path):
    try:
        file_size = os.path.getsize(video_path)
        size_mb = round(file_size / (1024 * 1024), 2)

        with open(video_path, "rb") as video_file:
            result = telegram(
                "sendVideo",
                data={
                    "chat_id": chat_id,
                    "reply_to_message_id": original_message_id,
                    "caption": f"🎬 ভিডিও ডাউনলোড সম্পন্ন হয়েছে!\n📦 সাইজ: {size_mb} MB",
                    "supports_streaming": "true"
                },
                files={
                    "video": video_file
                },
                timeout=1800
            )

        return result.get("ok", False), size_mb

    except Exception as error:
        print("Send video error:", error)
        return False, 0


def process_video(chat_id, original_message_id, url, status_message_id):
    with download_lock:

        # ৫ সেকেন্ড পরে Downloading মেসেজ ডিলিট
        def remove_status():
            time.sleep(5)
            delete_message(chat_id, status_message_id)

        threading.Thread(
            target=remove_status,
            daemon=True
        ).start()

        send_message(
            chat_id,
            "📥 ভিডিও ডাউনলোড শুরু হয়েছে। বড় ভিডিও হলে সময় বেশি লাগতে পারে।",
            original_message_id
        )

        video_path, temp_dir = download_video(url)

        if not video_path:
            send_message(
                chat_id,
                "❌ ভিডিও ডাউনলোড করা যায়নি।\n\n"
                "সম্ভবত ভিডিওটি private, login-required, "
                "unsupported অথবা সাইটটি ডাউনলোড বন্ধ করেছে।",
                original_message_id
            )
            shutil.rmtree(temp_dir, ignore_errors=True)
            return

        try:
            success, size_mb = send_downloaded_video(
                chat_id,
                original_message_id,
                video_path
            )

            if not success:
                send_message(
                    chat_id,
                    f"📥 ভিডিও ডাউনলোড হয়েছে।\n"
                    f"📦 সাইজ: {size_mb} MB\n\n"
                    "⚠️ ভিডিওটি Telegram-এ সরাসরি পাঠানো যায়নি। "
                    "ফাইলটি খুব বড় হতে পারে।",
                    original_message_id
                )

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def handle_message(message):
    global secret_code

    if not message:
        return

    chat_id = message["chat"]["id"]
    user = message.get("from", {})
    user_id = user.get("id")
    first_name = user.get("first_name", "User")
    text = message.get("text", "").strip()
    message_id = message["message_id"]

    if not text:
        return

    # /start
    if text.startswith("/start"):
        if is_authorized(user_id):
            send_message(
                chat_id,
                start_message(first_name)
            )
        else:
            send_message(
                chat_id,
                "🔐 এই বট ব্যবহার করতে Secret Code দিন।"
            )

        return

    # Admin: Secret Code পরিবর্তন
    if text.startswith("/setcode"):
        if user_id != ADMIN_ID:
            send_message(
                chat_id,
                "❌ এই কমান্ড শুধু Admin ব্যবহার করতে পারবেন।"
            )
            return

        parts = text.split(maxsplit=1)

        if len(parts) != 2 or len(parts[1].strip()) < 4:
            send_message(
                chat_id,
                "ব্যবহার করুন:\n/setcode নতুনকোড"
            )
            return

        secret_code = parts[1].strip()

        send_message(
            chat_id,
            "✅ Secret Code পরিবর্তন হয়েছে।"
        )
        return

    # Admin: User অনুমোদন
    if text.startswith("/adduser"):
        if user_id != ADMIN_ID:
            send_message(
                chat_id,
                "❌ শুধু Admin এই কমান্ড ব্যবহার করতে পারবেন।"
            )
            return

        parts = text.split(maxsplit=1)

        if len(parts) != 2 or not parts[1].isdigit():
            send_message(
                chat_id,
                "ব্যবহার করুন:\n/adduser USER_ID"
            )
            return

        new_user_id = int(parts[1])
        authorized_users.add(new_user_id)

        send_message(
            chat_id,
            f"✅ User অনুমোদিত হয়েছে:\n{new_user_id}"
        )
        return

    # Admin: User বাদ দেওয়া
    if text.startswith("/removeuser"):
        if user_id != ADMIN_ID:
            send_message(
                chat_id,
                "❌ শুধু Admin এই কমান্ড ব্যবহার করতে পারবেন।"
            )
            return

        parts = text.split(maxsplit=1)

        if len(parts) != 2 or not parts[1].isdigit():
            send_message(
                chat_id,
                "ব্যবহার করুন:\n/removeuser USER_ID"
            )
            return

        remove_id = int(parts[1])
        authorized_users.discard(remove_id)

        send_message(
            chat_id,
            f"✅ User-এর অনুমতি বাতিল হয়েছে:\n{remove_id}"
        )
        return

    # Secret Code যাচাই
    if not is_authorized(user_id):
        if text == secret_code:
            authorized_users.add(user_id)

            send_message(
                chat_id,
                "✅ Access Granted!\n\n"
                "🔗 এখন ভিডিও লিংক পাঠান।"
            )
        else:
            send_message(
                chat_id,
                "❌ Secret Code ভুল।\n"
                "Admin-এর কাছ থেকে সঠিক কোড নিন।"
            )

        return

    # URL বের করা
    url = extract_url(text)

    if not url:
        send_message(
            chat_id,
            "🔗 অনুগ্রহ করে একটি ভিডিও লিংক পাঠান।"
        )
        return

    if not is_supported_url(url):
        send_message(
            chat_id,
            "❌ এই লিংকটি এখনো সাপোর্ট করা হচ্ছে না।"
        )
        return

    status_message_id = send_message(
        chat_id,
        "⏳ Downloading Video...",
        message_id
    )

    threading.Thread(
        target=process_video,
        args=(
            chat_id,
            message_id,
            url,
            status_message_id
        ),
        daemon=True
    ).start()


def polling_loop():
    print("Polling started...")

    # পুরোনো Webhook মুছে দেবে
    telegram(
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
                print("Polling response:", data)
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
    port = int(os.environ.get("PORT", "10000"))

    # Polling আলাদা থ্রেডে চালু
    threading.Thread(
        target=polling_loop,
        daemon=True
    ).start()

    app.run(
        host="0.0.0.0",
        port=port
    )
