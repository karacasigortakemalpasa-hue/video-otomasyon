"""
Otomatik video + kapak resmi + YouTube yukleme scripti

scenes.json formati:
{
  "video_meta": {
    "title": "YouTube video basligi",
    "description": "YouTube aciklama metni",
    "tags": ["etiket1", "etiket2"]
  },
  "thumbnail": {
    "background_prompt": "Kapak resmi arka plan gorsel tarifi (Ingilizce)",
    "left_label": "MEN",
    "right_label": "WOMEN",
    "left_color": "#2E86AB",
    "right_color": "#E07A5F"
  },
  "scenes": [
    {"image_prompt": "...", "narration": "...", "reference_image_url": "..."},
    ...
  ]
}

Kullanim:
    python generate_video.py scenes.json
"""

import os
import sys
import json
import base64
import subprocess
import urllib.parse
import urllib.request
import urllib.error

POLLINATIONS_KEY = os.environ.get("POLLINATIONS_API_KEY", "")
GOOGLE_TTS_KEY = os.environ.get("GOOGLE_TTS_API_KEY", "")
YT_CLIENT_ID = os.environ.get("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN", "")

OUTPUT_DIR = "output"
IMG_DIR = os.path.join(OUTPUT_DIR, "images")
AUDIO_DIR = os.path.join(OUTPUT_DIR, "audio")
CLIP_DIR = os.path.join(OUTPUT_DIR, "clips")

for d in (IMG_DIR, AUDIO_DIR, CLIP_DIR):
    os.makedirs(d, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def _download(url: str, out_path: str):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"HATA {e.code}: {body}")
        raise
    with open(out_path, "wb") as f:
        f.write(data)


def generate_image(prompt: str, index: int, seed: int = 42, reference_image: str = None) -> str:
    """Pollinations.ai'dan görsel indirir, dosya yolunu döner."""
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&seed={seed}&model=flux&enhance=true"
    if reference_image:
        url += f"&image={urllib.parse.quote(reference_image)}"

    out_path = os.path.join(IMG_DIR, f"scene_{index:03d}.jpg")
    print(f"[{index}] Görsel isteniyor: {prompt[:60]}...")
    _download(url, out_path)
    return out_path


def generate_thumbnail(thumb_cfg: dict, reference_image: str = None) -> str:
    """Kapak resmi: Pollinations'tan arka plan + ffmpeg ile buyuk baslik yazisi."""
    bg_prompt = thumb_cfg.get("background_prompt", "")
    seed = thumb_cfg.get("seed", 999)
    raw_bg_path = os.path.join(OUTPUT_DIR, "thumbnail_bg.jpg")

    encoded_prompt = urllib.parse.quote(bg_prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&seed={seed}&model=flux&enhance=true"
    if reference_image:
        url += f"&image={urllib.parse.quote(reference_image)}"

    print("Kapak resmi arka planı isteniyor...")
    _download(url, raw_bg_path)

    left_label = thumb_cfg.get("left_label", "MEN")
    right_label = thumb_cfg.get("right_label", "WOMEN")
    left_color = thumb_cfg.get("left_color", "0x2E86AB")
    right_color = thumb_cfg.get("right_color", "0xE07A5F")

    final_thumb = os.path.join(OUTPUT_DIR, "thumbnail.jpg")
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    vf = (
        f"drawbox=x=0:y=0:w=iw/2-2:h=120:color={left_color}@0.92:t=fill,"
        f"drawbox=x=iw/2+2:y=0:w=iw/2-2:h=120:color={right_color}@0.92:t=fill,"
        f"drawtext=fontfile={font}:text='{left_label}':fontcolor=white:fontsize=64:"
        f"x=(iw/4)-(text_w/2):y=30:borderw=3:bordercolor=black,"
        f"drawtext=fontfile={font}:text='VS':fontcolor=yellow:fontsize=54:"
        f"x=(iw/2)-(text_w/2):y=30:borderw=3:bordercolor=black,"
        f"drawtext=fontfile={font}:text='{right_label}':fontcolor=white:fontsize=64:"
        f"x=(3*iw/4)-(text_w/2):y=30:borderw=3:bordercolor=black"
    )

    cmd = ["ffmpeg", "-y", "-i", raw_bg_path, "-vf", vf, final_thumb]
    print("Kapak resmi başlık yazısı ekleniyor...")
    subprocess.run(cmd, check=True, capture_output=True)
    return final_thumb


def generate_audio(text: str, index: int, voice_name: str = "en-US-Neural2-D", language_code: str = "en-US") -> str:
    """Google Cloud Text-to-Speech ile seslendirme üretir, mp3 dosya yolu döner."""
    if not GOOGLE_TTS_KEY:
        raise RuntimeError("GOOGLE_TTS_API_KEY tanımlı değil.")

    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_KEY}"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": language_code, "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.0, "pitch": 0.0},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    print(f"[{index}] Seslendirme üretiliyor ({len(text)} karakter)...")
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    audio_bytes = base64.b64decode(result["audioContent"])
    out_path = os.path.join(AUDIO_DIR, f"scene_{index:03d}.mp3")
    with open(out_path, "wb") as f:
        f.write(audio_bytes)
    return out_path


def get_audio_duration(path: str) -> float:
    """ffprobe ile ses dosyasının süresini (saniye) döner."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def make_scene_clip(image_path: str, audio_path: str, index: int, subtitle_text: str = "") -> str:
    """Sabit görsele Ken Burns efekti + opsiyonel altyazı uygulayıp sesle birleştirir."""
    duration = get_audio_duration(audio_path)
    fps = 30
    total_frames = int(duration * fps)

    out_path = os.path.join(CLIP_DIR, f"clip_{index:03d}.mp4")

    vf_parts = [
        "scale=2560:1440",
        f"zoompan=z='min(zoom+0.0007,1.3)':d={total_frames}:s=1280x720:fps={fps}",
    ]

    if subtitle_text:
        safe_text = subtitle_text.replace("'", "\u2019").replace(":", "\\:").replace(",", "\\,")
        vf_parts.append(
            "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"text='{safe_text}':fontcolor=white:fontsize=38:"
            "borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-140:"
            "line_spacing=8"
        )

    vf = ",".join(vf_parts)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        "-t", str(duration),
        out_path,
    ]
    print(f"[{index}] ffmpeg ile sahne birleştiriliyor ({duration:.1f}sn)...")
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def concat_clips(clip_paths: list, final_name: str = "final_video.mp4") -> str:
    """Tüm sahne kliplerini tek bir videoda birleştirir."""
    list_file = os.path.join(OUTPUT_DIR, "concat_list.txt")
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    final_path = os.path.join(OUTPUT_DIR, final_name)
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy",
        final_path,
    ]
    print("Tüm sahneler birleştiriliyor...")
    subprocess.run(cmd, check=True, capture_output=True)
    return final_path


def upload_to_youtube(video_path: str, thumb_path: str, meta: dict):
    """Videoyu YouTube'a private (taslak gibi) olarak yukler ve kapak resmini ayarlar."""
    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        print("YouTube secret'ları eksik, yükleme atlanıyor. Video sadece dosya olarak üretildi.")
        return

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )

    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": meta.get("title", "Untitled"),
            "description": meta.get("description", ""),
            "tags": meta.get("tags", []),
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
    }

    print("YouTube'a yükleniyor (private/taslak olarak)...")
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    video_id = response["id"]
    print(f"Yüklendi! Video ID: {video_id} (private, izlemek için: https://youtu.be/{video_id})")

    if thumb_path and os.path.exists(thumb_path):
        print("Kapak resmi ayarlanıyor...")
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumb_path)).execute()

    return video_id


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python generate_video.py scenes.json")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        config = json.load(f)

    scenes = config["scenes"]
    meta = config.get("video_meta", {})
    thumb_cfg = config.get("thumbnail", {})

    clip_paths = []
    thumb_reference = None
    for i, scene in enumerate(scenes, start=1):
        ref = scene.get("reference_image_url")
        if ref and thumb_reference is None:
            thumb_reference = ref
        image_path = generate_image(scene["image_prompt"], i, reference_image=ref)
        audio_path = generate_audio(scene["narration"], i)
        clip_path = make_scene_clip(image_path, audio_path, i, subtitle_text=scene["narration"])
        clip_paths.append(clip_path)

    final_video = concat_clips(clip_paths)

    thumb_path = None
    if thumb_cfg.get("background_prompt"):
        thumb_path = generate_thumbnail(thumb_cfg, reference_image=thumb_reference)

    print(f"\nBitti! Video hazır: {final_video}")
    if thumb_path:
        print(f"Kapak resmi hazır: {thumb_path}")

    upload_to_youtube(final_video, thumb_path, meta)


if __name__ == "__main__":
    main()
