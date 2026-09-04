import os
import re
import time
import tempfile
import threading
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ADMIN_ID = 7454180235

# Render Environment Variable থেকে Secret Code নেওয়া হবে
SECRET_CODE = os.environ.get("SECRET_CODE", "CHANGE_THIS_CODE")

API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# অনুমতি পাওয়া ইউজারদের মেমোরিতে রাখা হবে
authorized_users = set()


def telegram(method, data=None, files=None):
    try:
        response = requests.post(
            f"{API_URL}/{method}",
            data=data,
            files=files,
            timeout=120
        )
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

    result = telegram("sendMessage", data)
    return result.get("result", {}).get("message_id")


def delete_message(chat_id, message_id):
    telegram(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


def is_allowed_user(user_id):
    return user_id == ADMIN_ID or user_id in authorized_users


def extract_url(text):
    if not text:
        return None

    match = re.search(r"https?://[^\s]+", text)

    if not match:
        return None

    url = match.group(0).rstrip(".,!?)]}>\"'")
    return url


def supported_url(url):
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


def download_video(url):
    temp_dir = tempfile.mkdtemp(prefix="video_")
    output_template = os.path.join(temp_dir, "%(title).80s.%(ext)s")

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
            timeout=900
        )

        print("yt-dlp output:", result.stdout)
        print("yt-dlp error:", result.stderr)

        if result.returncode != 0:
            return None, temp_dir

        files = list(Path(temp_dir).glob("*"))

        video_files = [
            file for file in files
            if file.is_file() and file.suffix.lower() in
            [".mp4", ".mkv", ".webm", ".mov"]
        ]

        if not video_files:
            return None, temp_dir

        return str(video_files[0]), temp_dir

    except Exception as error:
        print("Download error:", error)
        return None, temp_dir


def send_video(chat_id, original_message_id, video_path):
    try:
        file_size = os.path.getsize(video_path)

        # Telegram-এ পাঠানোর আগে ফাইল সাইজ যাচাই
        # বড় হলে নিচের API চেষ্টা করা হবে
        with open(video_path, "rb") as video_file:
            result = telegram(
                "sendVideo",
                data={
                    "chat_id": chat_id,
                    "reply_to_message_id": original_message_id,
                    "caption": "🎬 ভিডিও ডাউনলোড সম্পন্ন হয়েছে!",
                    "supports_streaming": "true"
                },
                files={
                    "video": video_file
                }
            )

        return result.get("ok", False), file_size

    except Exception as error:
        print("Send video error:", error)
        return False, 0


def process_video(chat_id, original_message_id, url, status_message_id):
    # ৫ সেকেন্ড পর Downloading মেসেজ ডিলিট
    def remove_status():
        time.sleep(5)
        delete_message(chat_id, status_message_id)

    threading.Thread(target=remove_status, daemon=True).start()

    video_path, temp_dir = download_video(url)

    if not video_path:
        send_message(
            chat_id,
            "❌ ভিডিও ডাউনলোড করা যায়নি।\n\n"
            "সম্ভবত ভিডিওটি private, login-required, unsupported "
            "অথবা সাইটটি ডাউনলোড বন্ধ করেছে।",
            original_message_id
        )
        return

    try:
        success, file_size = send_video(
            chat_id,
            original_message_id,
            video_path
        )

        if not success:
            size_mb = round(file_size / (1024 * 1024), 2)

            send_message(
                chat_id,
                f"📥 ভিডিও ডাউনলোড হয়েছে।\n"
                f"📦 সাইজ: {size_mb} MB\n\n"
                "⚠️ Telegram-এর ফাইল সীমার কারণে ভিডিওটি সরাসরি "
                "পাঠানো যায়নি। পরে বড় ভিডিওর জন্য আলাদা "
                "ডাউনলোড-লিংক ব্যবস্থা যোগ করা যাবে।",
                original_message_id
            )

    finally:
        # কাজ শেষে অস্থায়ী ফাইল মুছে ফেলা
        try:
            for file in Path(temp_dir).glob("*"):
                file.unlink(missing_ok=True)

            Path(temp_dir).rmdir()
        except Exception as error:
            print("Cleanup error:", error)


def handle_update(update):
    message = update.get("message")

    if not message:
        return

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()
    message_id = message["message_id"]

    # /start
    if text.startswith("/start"):
        if is_allowed_user(user_id):
            send_message(
                chat_id,
                "👋 আসসালামু আলাইকুম, স্যার!\n\n"
                "📥 Supported Platforms:\n\n"
                "🎵 TikTok\n"
                "🎵 TikTok Lite\n"
                "📘 Facebook\n"
                "📘 Facebook Lite\n"
                "📹 Vidmard\n"
                "▶️ YouTube\n"
                "🌐 Chrome\n"
                "🔎 Google\n"
                "🐦 Twitter / X\n"
                "📸 Instagram\n"
                "📸 Instagram Lite\n\n"
                "🔗 শুধু ভিডিও লিংক পাঠান\n"
                "🎬 আমি ভিডিও ডাউনলোড করে দিব\n\n"
                "👨‍💻 Admin: @JAHIDVAI12"
            )
        else:
            send_message(
                chat_id,
                "🔐 এই বট ব্যবহার করতে Secret Code দিন।"
            )

        return

    # Admin-এর জন্য কোড পরিবর্তন
    if text.startswith("/setcode"):
        if user_id != ADMIN_ID:
            send_message(chat_id, "❌ এই কমান্ড শুধু Admin ব্যবহার করতে পারবেন।")
            return

        parts = text.split(maxsplit=1)

        if len(parts) != 2:
            send_message(
                chat_id,
                "ব্যবহার করুন:\n/setcode নতুনকোড"
            )
            return

        send_message(
            chat_id,
            "⚠️ কোড পরিবর্তনের জন্য Render-এর Environment Variable "
            "`SECRET_CODE` পরিবর্তন করুন।"
        )
        return

    # Admin সরাসরি ইউজার অনুমোদন করতে পারবেন
    if text.startswith("/adduser"):
        if user_id != ADMIN_ID:
            send_message(chat_id, "❌ শুধু Admin এই কমান্ড ব্যবহার করতে পারবেন।")
            return

        parts = text.split(maxsplit=1)

        if len(parts) != 2 or not parts[1].isdigit():
            send_message(chat_id, "ব্যবহার করুন:\n/adduser USER_ID")
            return

        new_user_id = int(parts[1])
        authorized_users.add(new_user_id)

        send_message(
            chat_id,
            f"✅ User অনুমোদিত হয়েছে:\n{new_user_id}"
        )
        return

    # Secret Code যাচাই
    if not is_allowed_user(user_id):
        if text == SECRET_CODE:
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

    # অনুমোদিত ইউজারের URL যাচাই
    url = extract_url(text)

    if not url:
        send_message(
            chat_id,
            "🔗 অনুগ্রহ করে একটি ভিডিও লিংক পাঠান।"
        )
        return

    if not supported_url(url):
        send_message(
            chat_id,
            "❌ এই প্ল্যাটফর্ম এখনো সাপোর্ট করা হচ্ছে না।"
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


@app.route("/", methods=["GET"])
def home():
    return "Video Downloader Bot is running."


@app.route("/health", methods=["GET"])
def health():
    return "OK"


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True)

    if update:
        threading.Thread(
            target=handle_update,
            args=(update,),
            daemon=True
        ).start()

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(
        host="0.0.0.0",
        port=port
    )
