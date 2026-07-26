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


GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")


def _gemini_generate_image(prompt: str, out_path: str, aspect_ratio: str = "16:9", reference_image_url: str = None):
    """Gemini (Nano Banana) ile görsel üretir, isteğe bağlı referans görsel ile, out_path'e kaydeder."""
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY tanımlı değil.")

    parts = []

    if reference_image_url:
        req_ref = urllib.request.Request(reference_image_url)
        req_ref.add_header("User-Agent", USER_AGENT)
        with urllib.request.urlopen(req_ref, timeout=60) as resp:
            ref_bytes = resp.read()
        ext = reference_image_url.lower().split(".")[-1]
        mime = "image/png" if ext == "png" else "image/jpeg"
        parts.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(ref_bytes).decode("utf-8"),
            }
        })

    parts.append({"text": prompt})

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"imageConfig": {"aspectRatio": aspect_ratio}},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"GEMINI HATA {e.code}: {body}")
        raise

    parts_out = result["candidates"][0]["content"]["parts"]
    image_b64 = None
    for part in parts_out:
        if "inlineData" in part:
            image_b64 = part["inlineData"]["data"]
            break
    if not image_b64:
        raise RuntimeError(f"Gemini yanıtında görsel bulunamadı: {result}")

    with open(out_path, "wb") as f:
        f.write(base64.b64decode(image_b64))


def generate_image(prompt: str, index: int, seed: int = 42, reference_image: str = None) -> str:
    """Gemini (Nano Banana) ile görsel üretir, dosya yolunu döner."""
    out_path = os.path.join(IMG_DIR, f"scene_{index:03d}.jpg")
    print(f"[{index}] Görsel isteniyor: {prompt[:60]}...")
    _gemini_generate_image(prompt, out_path, reference_image_url=reference_image)
    return out_path


def generate_thumbnail(thumb_cfg: dict, reference_image: str = None) -> str:
    """Kapak resmi: Gemini'den arka plan + ffmpeg ile buyuk baslik yazisi."""
    bg_prompt = thumb_cfg.get("background_prompt", "")
    raw_bg_path = os.path.join(OUTPUT_DIR, "thumbnail_bg.jpg")

    print("Kapak resmi arka planı isteniyor...")
    _gemini_generate_image(bg_prompt, raw_bg_path, reference_image_url=reference_image)

    left_label = thumb_cfg.get("left_label", "MEN")
    right_label = thumb_cfg.get("right_label", "WOMEN")
    left_color = thumb_cfg.get("left_color", "0x2E86AB")
    right_color = thumb_cfg.get("right_color", "0xE07A5F")

    final_thumb = os.path.join(OUTPUT_DIR, "thumbnail.jpg")
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    vf = (
        f"drawbox=x=0:y=0:w=iw/2-2:h=140:color={left_color}@0.94:t=fill,"
        f"drawbox=x=iw/2+2:y=0:w=iw/2-2:h=140:color={right_color}@0.94:t=fill,"
        f"drawtext=fontfile={font}:text='{left_label}':fontcolor=white:fontsize=72:"
        f"x=(w/4)-(text_w/2):y=35:borderw=4:bordercolor=black,"
        f"drawtext=fontfile={font}:text='{right_label}':fontcolor=white:fontsize=72:"
        f"x=(3*w/4)-(text_w/2):y=35:borderw=4:bordercolor=black,"
        # Ortada buyuk kirmizi ok isareti (iki tarafi karsilastiran gorsel vurgu)
        f"drawtext=fontfile={font}:text='>>':fontcolor=0xFF2222:fontsize=140:"
        f"x=(w/2)-(text_w/2):y=(140/2)-(text_h/2)-8:borderw=6:bordercolor=white,"
        f"drawtext=fontfile={font}:text='VS':fontcolor=yellow:fontsize=40:"
        f"x=(w/2)-(text_w/2):y=145:borderw=3:bordercolor=black"
    )

    cmd = ["ffmpeg", "-y", "-i", raw_bg_path, "-vf", vf, "-update", "1", "-frames:v", "1", final_thumb]
    print("Kapak resmi başlık yazısı ekleniyor...")
    subprocess.run(cmd, check=True, capture_output=True)
    return final_thumb


def generate_audio(text: str, index: int, voice_name: str = "en-GB-Neural2-F", language_code: str = "en-GB") -> str:
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


def wrap_text(text: str, max_chars: int = 42) -> str:
    """Uzun cümleyi ekrana sığacak şekilde birden fazla satıra böler."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def make_scene_clip(image_path: str, audio_path: str, index: int, subtitle_text: str = "") -> str:
    """Sabit görsele Ken Burns efekti + opsiyonel altyazı uygulayıp sesle birleştirir."""
    duration = get_audio_duration(audio_path)
    fps = 30
    total_frames = int(duration * fps)

    out_path = os.path.join(CLIP_DIR, f"clip_{index:03d}.mp4")

    vf_parts = [
        "scale=1600:900:force_original_aspect_ratio=increase",
        "crop=1600:900",
        f"zoompan=z='min(zoom+0.0007,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1280x720:fps={fps}",
    ]

    if subtitle_text:
        wrapped = wrap_text(subtitle_text)
        safe_text = wrapped.replace("'", "\u2019").replace(":", "\\:").replace(",", "\\,")
        vf_parts.append(
            "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"text='{safe_text}':fontcolor=white:fontsize=34:"
            "borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-170:"
            "line_spacing=6"
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


REFERENCE_IMAGE_URL = "https://raw.githubusercontent.com/karacasigortakemalpasa-hue/video-otomasyon/main/reference.jpg"

STYLE_GUIDE = """[Masterpiece, Best Quality] A detailed 2D digital illustration, clean simple line-work, reminiscent of a graphic novel. The entire scene is monochromatic, dominated by shades of dark grey and charcoal. Only selective warm light and one colored item of clothing on a character break the palette. Characters are drawn in a simple, minimalist stick-figure-person style with details, round white heads, small dot eyes, expressive exaggerated faces. Highly detailed crosshatched background. Minimal noise, sharp lines."""


def expand_topic_to_scenes(topic: str, num_scenes: int = 30) -> dict:
    """Bir konu cumlesini Gemini metin modeliyle tam scenes.json yapisina cevirir."""
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY tanımlı değil.")

    system_prompt = f"""You are a scriptwriter and art director for a comedic, fact-based YouTube Shorts/explainer channel.
Given a TOPIC, produce a complete video plan as STRICT JSON (no markdown fences, no commentary, just the JSON object).

Style guide for every image_prompt (always start each image_prompt with this exact style block, then add scene-specific action after it):
"{STYLE_GUIDE}"

Rules:
- Tone: humorous, witty, entertaining, but grounded in real, accurate information about the topic. Avoid mean-spirited or offensive stereotypes; keep it lighthearted and fair to all groups mentioned.
- Exactly {num_scenes} scenes.
- Each "narration" is 1-2 short spoken sentences in English, natural conversational tone, building a coherent narrative arc from hook to conclusion.
- The FIRST scene must be an attention-grabbing hook question or statement about the topic.
- The LAST scene must include a friendly call to action asking viewers to subscribe and like the video.
- Each "image_prompt" must describe a specific, concrete visual action/scene (following the style guide above), matching that scene's narration.
- "video_meta.title" is a catchy, clickable YouTube title (under 70 characters).
- "video_meta.description" is 2-4 sentences plus 3-5 relevant hashtags.
- "video_meta.tags" is a list of 5-10 relevant keyword tags.
- "thumbnail.background_prompt" follows the same style guide, depicting a compelling split/comparison scene relevant to the topic.
- "thumbnail.left_label" and "thumbnail.right_label" are short (1-2 word) punchy labels for a comparison thumbnail relevant to the topic (omit or use generic labels like "MYTH"/"FACT" if the topic isn't a two-sided comparison).
- "thumbnail.left_color" and "thumbnail.right_color" are hex-like ffmpeg colors in the form 0xRRGGBB.

Output EXACTLY this JSON schema, nothing else:
{{
  "video_meta": {{"title": "...", "description": "...", "tags": ["...", "..."]}},
  "thumbnail": {{"background_prompt": "...", "left_label": "...", "right_label": "...", "left_color": "0x2E86AB", "right_color": "0xE07A5F"}},
  "scenes": [
    {{"image_prompt": "...", "narration": "..."}}
  ]
}}

TOPIC: {topic}
"""

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
    payload = {
        "contents": [{"parts": [{"text": system_prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY},
    )
    print(f"Konu senaryoya çevriliyor: {topic[:60]}...")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"GEMINI METIN HATA {e.code}: {body}")
        raise

    text_out = result["candidates"][0]["content"]["parts"][0]["text"]
    text_out = text_out.strip()
    if text_out.startswith("```"):
        text_out = text_out.split("```")[1]
        if text_out.startswith("json"):
            text_out = text_out[4:]
    config = json.loads(text_out)

    for scene in config["scenes"]:
        scene["reference_image_url"] = REFERENCE_IMAGE_URL

    return config


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python generate_video.py scenes.json")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        config = json.load(f)

    if "scenes" not in config and "topic" in config:
        config = expand_topic_to_scenes(config["topic"], config.get("num_scenes", 30))
        with open(os.path.join(OUTPUT_DIR, "expanded_scenes.json"), "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

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
