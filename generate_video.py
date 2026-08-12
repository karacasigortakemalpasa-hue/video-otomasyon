"""
"Unutulmuş Skandallar" kanali - tek anlatici, risograph iki-renk tarzinda,
her konu icin HEM uzun video HEM Short ureten, ortak basligi paylasan sistem.

Onceki (kadin-erkek maskotlu) sistemin tam yerine gecer.

Kullanim:
    python generate_video.py queue/topics/001_konu.json
"""

import os
import sys
import json
import base64
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.error

GOOGLE_TTS_KEY = os.environ.get("GOOGLE_TTS_API_KEY", "")
YT_CLIENT_ID = os.environ.get("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN", "")
GCP_SERVICE_ACCOUNT_JSON = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "rosy-embassy-473607-a3")
GCP_REGION = "global"

OUTPUT_DIR = "output"
IMG_DIR = os.path.join(OUTPUT_DIR, "images")
AUDIO_DIR = os.path.join(OUTPUT_DIR, "audio")
CLIP_DIR = os.path.join(OUTPUT_DIR, "clips")
for d in (IMG_DIR, AUDIO_DIR, CLIP_DIR):
    os.makedirs(d, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Kanalin sabit gorsel kimligi: Gossip Machine'de test ettigimiz risograph iki-renk
# baski tarzi. Karakter/maskot yok - tek, tutarli bir illustrasyon dili var.
STYLE_GUIDE = (
    "Two-color risograph print illustration, deep plum violet ink and antique brass "
    "gold ink ONLY — no other colors. Coarse halftone dot grain. Deliberate ink "
    "misregistration. Heavy paper texture. Imperfect hand-inked outlines with breaks "
    "and bleeds. Sparse, editorial composition. Flat 2D print aesthetic, no 3D, no "
    "photorealism, no digital polish. Vintage investigative-journalism poster "
    "aesthetic. No text, no letters, no numbers anywhere in the image."
)

NARRATOR_VOICE = "en-GB-Neural2-D"

_vertex_token_cache = {"token": None}


def _get_vertex_access_token() -> str:
    if _vertex_token_cache["token"]:
        return _vertex_token_cache["token"]
    if not GCP_SERVICE_ACCOUNT_JSON:
        raise RuntimeError("GCP_SERVICE_ACCOUNT_JSON tanımlı değil.")
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    info = json.loads(GCP_SERVICE_ACCOUNT_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    _vertex_token_cache["token"] = credentials.token
    return credentials.token


def _gemini_generate_image(prompt: str, out_path: str, aspect_ratio: str = "16:9"):
    """Vertex AI (Nano Banana 2 Lite) ile karaktersiz, risograph tarzinda gorsel uretir."""
    token = _get_vertex_access_token()
    host = "aiplatform.googleapis.com" if GCP_REGION == "global" else f"{GCP_REGION}-aiplatform.googleapis.com"
    url = (
        f"https://{host}/v1/projects/{GCP_PROJECT_ID}"
        f"/locations/{GCP_REGION}/publishers/google/models/gemini-3.1-flash-lite-image:generateContent"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"imageConfig": {"aspectRatio": aspect_ratio}},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )

    max_retries = 6
    result = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code in (429, 503) and attempt < max_retries - 1:
                wait = 30 * (attempt + 1)
                print(f"  {e.code} alındı, {wait}sn bekleniyor...")
                time.sleep(wait)
                continue
            print(f"VERTEX AI HATA {e.code}: {body}")
            raise

    parts_out = result["candidates"][0]["content"]["parts"]
    image_b64 = None
    for part in parts_out:
        if "inlineData" in part:
            image_b64 = part["inlineData"]["data"]
            break
    if not image_b64:
        raise RuntimeError(f"Vertex AI yanıtında görsel bulunamadı: {result}")
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(image_b64))


def generate_image(prompt: str, out_path: str, aspect_ratio: str = "16:9"):
    full_prompt = f"{STYLE_GUIDE} {prompt}"
    _gemini_generate_image(full_prompt, out_path, aspect_ratio=aspect_ratio)
    time.sleep(8)  # dakikalik hiz limitine takilmamak icin


def generate_audio(text: str, index: int, prefix: str = "scene", speaking_rate: float = 0.97,
                    pitch: float = 0.0, volume_gain_db: float = 10.0, emphasize_last_words: int = 0) -> str:
    """Google Cloud TTS ile TEK, sabit anlatici sesiyle seslendirme uretir."""
    if not GOOGLE_TTS_KEY:
        raise RuntimeError("GOOGLE_TTS_API_KEY tanımlı değil.")

    if emphasize_last_words > 0:
        words = text.split()
        if len(words) > emphasize_last_words:
            head = " ".join(words[:-emphasize_last_words])
            tail = " ".join(words[-emphasize_last_words:])
            ssml = f"<speak>{head} <emphasis level=\"strong\">{tail}</emphasis></speak>"
        else:
            ssml = f"<speak><emphasis level=\"strong\">{text}</emphasis></speak>"
        input_field = {"ssml": ssml}
    else:
        input_field = {"text": text}

    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_KEY}"
    payload = {
        "input": input_field,
        "voice": {"languageCode": "en-GB", "name": NARRATOR_VOICE},
        "audioConfig": {
            "audioEncoding": "MP3", "speakingRate": speaking_rate, "pitch": pitch,
            "volumeGainDb": volume_gain_db,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    print(f"[{prefix} {index}] Seslendirme üretiliyor ({len(text)} karakter)...")
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    audio_bytes = base64.b64decode(result["audioContent"])
    out_path = os.path.join(AUDIO_DIR, f"{prefix}_{index:03d}.mp3")
    with open(out_path, "wb") as f:
        f.write(audio_bytes)
    return out_path


def get_audio_duration(path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def wrap_text(text: str, max_chars: int = 42) -> str:
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


def make_scene_clip(image_path: str, audio_path: str, index: int, subtitle_text: str = "",
                     flash_caption: bool = False, vertical: bool = False, prefix: str = "clip") -> str:
    """Sabit gorsele Ken Burns efekti + altyazi uygulayip sesle birlestirir.
    vertical=True ise Short icin 1080x1920 dikey kadraj kullanir."""
    duration = get_audio_duration(audio_path)
    fps = 30
    total_frames = int(duration * fps)
    out_path = os.path.join(CLIP_DIR, f"{prefix}_{index:03d}.mp4")

    if vertical:
        scale_w, scale_h, canvas = 1350, 2400, "1080x1920"
        fontsize, flash_fontsize, y_pos = 40, 50, "h-320"
    else:
        scale_w, scale_h, canvas = 2400, 1350, "1920x1080"
        fontsize, flash_fontsize, y_pos = 34, 44, "h-170"

    vf_parts = [
        f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase",
        f"crop={scale_w}:{scale_h}",
        f"zoompan=z='min(zoom+0.0006,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s={canvas}:fps={fps}",
    ]

    if subtitle_text and flash_caption:
        words = subtitle_text.split()
        chunk_size = 2
        chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)] or [""]
        seg_dur = duration / len(chunks)
        for i, chunk in enumerate(chunks):
            safe_chunk = chunk.replace("'", "\u2019").replace(":", "\\:").replace(",", "\\,")
            start, end = i * seg_dur, (i + 1) * seg_dur
            vf_parts.append(
                f"drawtext=fontfile={FONT}:text='{safe_chunk}':fontcolor=white:fontsize={flash_fontsize}:"
                f"borderw=4:bordercolor=black:x=(w-text_w)/2:y={y_pos}:"
                f"enable='between(t,{start:.3f},{end:.3f})'"
            )
    elif subtitle_text:
        wrapped = wrap_text(subtitle_text, max_chars=30 if vertical else 42)
        safe_text = wrapped.replace("'", "\u2019").replace(":", "\\:").replace(",", "\\,")
        vf_parts.append(
            f"drawtext=fontfile={FONT}:text='{safe_text}':fontcolor=white:fontsize={fontsize}:"
            f"borderw=3:bordercolor=black:x=(w-text_w)/2:y={y_pos}:line_spacing=6"
        )

    vf = ",".join(vf_parts)
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", image_path, "-i", audio_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-shortest", "-t", str(duration),
        out_path,
    ]
    print(f"[{prefix} {index}] ffmpeg ile sahne birleştiriliyor ({duration:.1f}sn)...")
    subprocess.run(cmd, check=True, capture_output=True, timeout=90)
    return out_path, duration


def concat_clips(clip_paths: list, final_name: str) -> str:
    list_file = os.path.join(OUTPUT_DIR, f"concat_list_{final_name}.txt")
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    final_path = os.path.join(OUTPUT_DIR, final_name)
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", final_path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    return final_path


def generate_thumbnail(scene_prompt: str, headline_text: str) -> str:
    """Tek, carpici bir risograph sahnesi + ffmpeg ile garantili buyuk baslik yazisi."""
    scene_img = os.path.join(OUTPUT_DIR, "thumb_scene.jpg")
    generate_image(scene_prompt, scene_img, aspect_ratio="16:9")

    final_thumb = os.path.join(OUTPUT_DIR, "thumbnail.jpg")
    safe_headline = headline_text.replace("'", "\u2019").replace(":", "\\:").replace(",", "\\,")
    filter_complex = (
        f"[0:v]drawtext=fontfile={FONT}:text='{safe_headline}':fontcolor=white:fontsize=68:"
        "borderw=6:bordercolor=black:box=1:boxcolor=black@0.55:boxborderw=25:"
        "x=(w-text_w)/2:y=h-220[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", scene_img,
        "-filter_complex", filter_complex, "-map", "[out]",
        "-frames:v", "1", "-update", "1",
        final_thumb,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return final_thumb


def upload_to_youtube(video_path: str, thumb_path: str, meta: dict):
    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        print("YouTube secret'ları eksik, yükleme atlanıyor.")
        return None

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        token=None, refresh_token=YT_REFRESH_TOKEN, token_uri="https://oauth2.googleapis.com/token",
        client_id=YT_CLIENT_ID, client_secret=YT_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": meta.get("title", "Untitled"),
            "description": meta.get("description", ""),
            "tags": meta.get("tags", []),
            "categoryId": "22",
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
        "recordingDetails": {"locationDescription": "Canada"},
    }
    print("YouTube'a yükleniyor (private/taslak olarak)...")
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status,recordingDetails", body=body, media_body=media)
    response = request.execute()
    video_id = response["id"]
    print(f"Yüklendi! Video ID: {video_id} (https://youtu.be/{video_id})")
    if thumb_path and os.path.exists(thumb_path):
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumb_path)).execute()
    return video_id


# ============================================================
# SENARYO YAZIMI - tek Gemini istegiyle HEM uzun video HEM Short uretir
# ============================================================

def expand_topic_to_package(topic: str) -> dict:
    system_prompt = f"""You are the head writer for a documentary-style YouTube channel about forgotten
scandals, hoaxes, and manipulation in media/science/history — each episode reveals a real, well-documented
story with a genuine "twist," and connects it to a timeless truth about human psychology (conformity, herd
behavior, confirmation bias, the power of belief, etc.). Tone: calm, confident, dry-witted narrator, like a
quality long-form documentary — never cartoonish, never a cheap "AI slop" listicle voice.

TOPIC: {topic}

Write STRICT JSON (no markdown fences, no commentary, just the JSON object) containing a FULL production
package: one long-form video script AND one Short script, sharing the exact same title, both telling the
same core story (the Short is a tightly compressed version, not a teaser).

CRITICAL OPENING RULE (applies to both long_scenes[0] and short_scenes[0]):
The very first line must be a short, abstract, thought-provoking QUESTION directly tied to the deeper theme
of the story — NOT a greeting, NOT "today we're talking about X". It should make the viewer want to hear the
answer. Example style: "Does evidence matter more than what we want to believe?" or "How many people does it
take to turn a lie into a fact?" The concrete story/name/event follows a few lines later, not immediately.

LONG-FORM SCRIPT (long_scenes): {{'~38 to 42 scenes'}}, structured as: abstract hook question (1) -> concrete
premise/name/event (2-3) -> how it happened / rose (several) -> the twist/reveal (several) -> a natural
mid-video comment-bait line about two-thirds through (1, e.g. "Comment below what you think happened next")
-> the deeper psychological mechanism explained with real specificity (several, avoid vague clichés, include
concrete reasoning) -> resolution (1-2) -> reflective closing question + natural subscribe line (1-2). Each
scene: concrete narration sentence(s) + a matching risograph-style image_prompt (no text/letters in the
image itself). Target total spoken duration around 5 minutes.

SHORT SCRIPT (short_scenes): 8 to 12 scenes, same story compressed to its sharpest essence, same opening-
question rule, same twist, ending with a short punchy reflective line + subscribe mention. Target total
spoken duration under 55 seconds — write tightly, no filler.

Both scripts must be FACTUALLY GROUNDED — use real documented history. If uncertain of an exact number/date,
use a reasonable, clearly-labeled approximation rather than fabricating false precision. Avoid generic
clichés — every scene should add real, specific information.

video_meta.title: applies to BOTH the long video and the Short (same title), curiosity-driven, under 65
characters.
video_meta.description: 2-4 sentence hook, then a short factual note on sourcing/accuracy if relevant, then
3-5 relevant hashtags.
video_meta.tags: 6-10 relevant keyword tags.
thumbnail.scene_prompt: ONE striking risograph-style illustrated moment from the story (no text in image).
thumbnail.headline_text: a short, bold headline for the thumbnail (under 40 characters).

CRITICAL: Output must be STRICTLY VALID JSON. Escape internal double quotes as \\". No trailing commas.

Output EXACTLY this schema:
{{
  "video_meta": {{"title": "...", "description": "...", "tags": ["...", "..."]}},
  "thumbnail": {{"scene_prompt": "...", "headline_text": "..."}},
  "long_scenes": [{{"image_prompt": "...", "narration": "..."}}],
  "short_scenes": [{{"image_prompt": "...", "narration": "..."}}]
}}
"""

    token = _get_vertex_access_token()
    host = "aiplatform.googleapis.com" if GCP_REGION == "global" else f"{GCP_REGION}-aiplatform.googleapis.com"
    url = (
        f"https://{host}/v1/projects/{GCP_PROJECT_ID}"
        f"/locations/{GCP_REGION}/publishers/google/models/gemini-3.5-flash-lite:generateContent"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": system_prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    print(f"Konu senaryoya çevriliyor: {topic[:70]}...")

    max_attempts = 3
    config = None
    for attempt in range(max_attempts):
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            print(f"GEMINI METIN HATA {e.code}: {body}")
            raise

        text_out = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        if text_out.startswith("```"):
            text_out = text_out.split("```")[1]
            if text_out.startswith("json"):
                text_out = text_out[4:]
        try:
            config = json.loads(text_out)
            break
        except json.JSONDecodeError as e:
            print(f"Senaryo JSON'u bozuk geldi (deneme {attempt + 1}/{max_attempts}): {e}")
            if attempt == max_attempts - 1:
                raise
            continue

    return config


# ============================================================
# URETIM AKISI
# ============================================================

def build_long_video(scenes: list, meta: dict, thumb_cfg: dict) -> tuple:
    clip_paths = []
    for i, scene in enumerate(scenes, start=1):
        narration = scene.get("narration", "").strip()
        if not narration:
            print(f"[video {i}] UYARI: narration boş, atlanıyor.")
            continue
        img_path = os.path.join(IMG_DIR, f"scene_{i:03d}.jpg")
        try:
            generate_image(scene.get("image_prompt", ""), img_path, aspect_ratio="16:9")
            audio_path = generate_audio(narration, i, prefix="video")
            clip_path, _ = make_scene_clip(img_path, audio_path, i, subtitle_text=narration,
                                            flash_caption=(i == 1), vertical=False, prefix="clip")
            clip_paths.append(clip_path)
        except Exception as e:
            print(f"[video {i}] SAHNE ATLANDI: {e}")
            continue

    if not clip_paths:
        raise RuntimeError("Uzun video için hiçbir sahne üretilemedi.")

    final_video = concat_clips(clip_paths, "final_video.mp4")
    thumb_path = None
    if thumb_cfg.get("scene_prompt"):
        thumb_path = generate_thumbnail(thumb_cfg["scene_prompt"], thumb_cfg.get("headline_text", meta.get("title", "")))
    return final_video, thumb_path


def build_short_video(scenes: list, meta: dict) -> str:
    clip_paths = []
    for i, scene in enumerate(scenes, start=1):
        narration = scene.get("narration", "").strip()
        if not narration:
            continue
        img_path = os.path.join(IMG_DIR, f"short_{i:03d}.jpg")
        try:
            generate_image(scene.get("image_prompt", ""), img_path, aspect_ratio="9:16")
            audio_path = generate_audio(narration, i, prefix="short")
            clip_path, _ = make_scene_clip(img_path, audio_path, i, subtitle_text=narration,
                                            flash_caption=(i == 1), vertical=True, prefix="shortclip")
            clip_paths.append(clip_path)
        except Exception as e:
            print(f"[short {i}] SAHNE ATLANDI: {e}")
            continue

    if not clip_paths:
        raise RuntimeError("Short için hiçbir sahne üretilemedi.")

    return concat_clips(clip_paths, "final_short.mp4")


def process_topic(topic_path: str):
    with open(topic_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    package = expand_topic_to_package(config["topic"])
    with open(os.path.join(OUTPUT_DIR, "expanded_package.json"), "w", encoding="utf-8") as f:
        json.dump(package, f, ensure_ascii=False, indent=2)

    meta = package.get("video_meta", {})
    thumb_cfg = package.get("thumbnail", {})

    print(f"\n=== UZUN VIDEO ({len(package.get('long_scenes', []))} sahne) ===")
    final_video, thumb_path = build_long_video(package.get("long_scenes", []), meta, thumb_cfg)
    print(f"Uzun video hazır: {final_video}")
    upload_to_youtube(final_video, thumb_path, meta)

    print(f"\n=== SHORT ({len(package.get('short_scenes', []))} sahne) ===")
    final_short = build_short_video(package.get("short_scenes", []), meta)
    print(f"Short hazır: {final_short}")
    upload_to_youtube(final_short, None, meta)

    print("\nİkisi de aynı başlıkla yüklendi:", meta.get("title", ""))


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python generate_video.py queue/topics/XXX_konu.json")
        sys.exit(1)
    process_topic(sys.argv[1])


if __name__ == "__main__":
    main()
