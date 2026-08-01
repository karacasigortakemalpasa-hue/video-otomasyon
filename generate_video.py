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
import time
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
GCP_SERVICE_ACCOUNT_JSON = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "rosy-embassy-473607-a3")
GCP_REGION = "global"

_vertex_access_token_cache = {"token": None}


def _get_vertex_access_token() -> str:
    """Servis hesabi JSON'undan Vertex AI icin gecici erisim token'i uretir."""
    if _vertex_access_token_cache["token"]:
        return _vertex_access_token_cache["token"]

    if not GCP_SERVICE_ACCOUNT_JSON:
        raise RuntimeError("GCP_SERVICE_ACCOUNT_JSON tanımlı değil.")

    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    info = json.loads(GCP_SERVICE_ACCOUNT_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    _vertex_access_token_cache["token"] = credentials.token
    return credentials.token


def _gemini_generate_image(prompt: str, out_path: str, aspect_ratio: str = "16:9", reference_image_url=None):
    """Vertex AI uzerinden Nano Banana 2 Lite (gemini-3.1-flash-lite-image) ile gorsel uretir - genel Cloud kredisinden duser.
    reference_image_url: tek bir URL string'i ya da birden fazla URL iceren bir liste olabilir."""
    token = _get_vertex_access_token()

    host = "aiplatform.googleapis.com" if GCP_REGION == "global" else f"{GCP_REGION}-aiplatform.googleapis.com"
    url = (
        f"https://{host}/v1/projects/{GCP_PROJECT_ID}"
        f"/locations/{GCP_REGION}/publishers/google/models/gemini-3.1-flash-lite-image:generateContent"
    )

    ref_urls = []
    if reference_image_url:
        ref_urls = reference_image_url if isinstance(reference_image_url, list) else [reference_image_url]

    parts = []
    for ref_url in ref_urls:
        try:
            req_ref = urllib.request.Request(ref_url)
            req_ref.add_header("User-Agent", USER_AGENT)
            with urllib.request.urlopen(req_ref, timeout=60) as resp:
                ref_bytes = resp.read()
            ext = ref_url.lower().split(".")[-1]
            mime = "image/png" if ext == "png" else "image/jpeg"
            parts.append({
                "inline_data": {
                    "mime_type": mime,
                    "data": base64.b64encode(ref_bytes).decode("utf-8"),
                }
            })
        except Exception as e:
            print(f"UYARI: referans görsel indirilemedi ({ref_url}): {e}")

    parts.append({"text": prompt})

    payload = {
        "contents": [{"role": "user", "parts": parts}],
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
                print(f"429 (rate limit) alındı, {wait} saniye bekleyip tekrar denenecek...")
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


def generate_image(prompt: str, index: int, seed: int = 42, reference_image: str = None) -> str:
    """Gemini (Nano Banana) ile görsel üretir, dosya yolunu döner."""
    out_path = os.path.join(IMG_DIR, f"scene_{index:03d}.jpg")
    print(f"[{index}] Görsel isteniyor: {prompt[:60]}...")
    _gemini_generate_image(prompt, out_path, reference_image_url=reference_image)
    time.sleep(8)
    return out_path


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


def generate_thumbnail(thumb_cfg: dict, meta: dict = None) -> str:
    """Kapak resmi: kadin ve erkek karakter AYRI AYRI (tek referansla, guvenilir) uretilir -
    her birinin pozuna konuyla ilgili, karakterden daha az goze carpan ikincil bir obje/gorsel
    entegre edilebilir. Baslik yazisi bizim gercek fontumuzla, VS amblemi bizim gercek logo
    dosyamizla, ffmpeg ile garantili sekilde eklenir."""
    left_prompt = thumb_cfg.get("left_pose_prompt", "standing confidently, plain white background")
    right_prompt = thumb_cfg.get("right_pose_prompt", "standing confidently, plain white background")
    headline = thumb_cfg.get("headline_text") or (meta or {}).get("title", "")

    style_note = ("Simple flat 2D digital illustration character, clean line-work, plain solid white "
                  "background, no scenery, no shadow, full body or waist-up, facing forward or "
                  "three-quarter angle. The character's face, hair, and clothing must match EXACTLY as "
                  "shown in the reference image — do not restyle them. Any secondary topic-related object "
                  "or visual element must be clearly SMALLER and LESS prominent than the character itself, "
                  "positioned near or interacting with them, never competing with or overshadowing the "
                  "character. ")

    left_img = os.path.join(OUTPUT_DIR, "thumb_left.jpg")
    right_img = os.path.join(OUTPUT_DIR, "thumb_right.jpg")

    print("Kapak için kadın karakter üretiliyor...")
    _gemini_generate_image(style_note + left_prompt, left_img, aspect_ratio="3:4", reference_image_url=WOMAN_REFERENCE_URL)
    print("Kapak için erkek karakter üretiliyor...")
    _gemini_generate_image(style_note + right_prompt, right_img, aspect_ratio="3:4", reference_image_url=MAN_REFERENCE_URL)

    final_thumb = os.path.join(OUTPUT_DIR, "thumbnail.jpg")
    font = "font.ttf"  # repo koklerinden yuklenen Chewy fontu
    logo_path = "thumbnail_logo.png"  # repo koklerinden yuklenen seffaf logo PNG'si
    safe_headline = wrap_text(headline, max_chars=28).replace("'", "\u2019").replace(":", "\\:").replace(",", "\\,")

    has_logo = os.path.exists(logo_path)

    inputs = ["-f", "lavfi", "-i", "color=c=white:s=1280x720", "-i", left_img, "-i", right_img]
    if has_logo:
        inputs += ["-i", logo_path]

    filter_complex = (
        "color=c=white:s=1280x720[bg];"
        "[1:v]scale=620:-1[l];"
        "[2:v]scale=620:-1[r];"
        "[bg][l]overlay=x=10:y=180[bg1];"
        "[bg1][r]overlay=x=650:y=180[bg2];"
        f"[bg2]drawtext=fontfile={font}:text='{safe_headline}':fontcolor=black:fontsize=54:"
        "borderw=0:x=(w-text_w)/2:y=20:line_spacing=6[bg3]"
    )
    if has_logo:
        filter_complex += (
            ";[3:v]scale=-1:220[logo];"
            "[bg3][logo]overlay=x=(W-w)/2:y=(H-h)/2+70[out]"
        )
    else:
        filter_complex += (
            f";[bg3]drawtext=fontfile={font}:text='VS':fontcolor=0xFF2222:fontsize=90:"
            "borderw=6:bordercolor=white:x=(w-text_w)/2:y=(h-text_h)/2+60[out]"
        )

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", "[out]",
        "-frames:v", "1", "-update", "1",
        final_thumb,
    ]
    print("Kapak birleştiriliyor (gerçek font + gerçek logo, garantili şekilde)...")
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


def make_scene_clip(image_path: str, audio_path: str, index: int, subtitle_text: str = "") -> str:
    """Sabit görsele Ken Burns efekti + opsiyonel altyazı uygulayıp sesle birleştirir."""
    duration = get_audio_duration(audio_path)
    fps = 30
    total_frames = int(duration * fps)

    out_path = os.path.join(CLIP_DIR, f"clip_{index:03d}.mp4")

    vf_parts = [
        "scale=2400:1350:force_original_aspect_ratio=increase",
        "crop=2400:1350",
        f"zoompan=z='min(zoom+0.0006,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1920x1080:fps={fps}",
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
        "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
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
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
        "recordingDetails": {
            "locationDescription": "Canada",
        },
    }

    print("YouTube'a yükleniyor (private/taslak olarak)...")
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status,recordingDetails", body=body, media_body=media)
    response = request.execute()
    video_id = response["id"]
    print(f"Yüklendi! Video ID: {video_id} (private, izlemek için: https://youtu.be/{video_id})")

    if thumb_path and os.path.exists(thumb_path):
        print("Kapak resmi ayarlanıyor...")
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumb_path)).execute()

    return video_id


WOMAN_REFERENCE_URL = "https://raw.githubusercontent.com/karacasigortakemalpasa-hue/video-otomasyon/main/woman_mascot.jpg"
MAN_REFERENCE_URL = "https://raw.githubusercontent.com/karacasigortakemalpasa-hue/video-otomasyon/main/man_mascot.jpg"

STYLE_GUIDE = """[Masterpiece, Best Quality] A detailed 2D digital illustration, clean simple line-work, reminiscent of a graphic novel. The entire scene is monochromatic, dominated by shades of dark grey and charcoal. Only selective warm light and one colored item of clothing on a character break the palette. Highly detailed crosshatched background. Minimal noise, sharp lines. The character's face, hairstyle, and body must match EXACTLY as shown in the provided reference image — do not alter, simplify, or restyle their facial features in any way, only change their pose, expression, and clothing accessory as needed for the scene."""


def expand_topic_to_scenes(topic: str, num_scenes: int = 30) -> dict:
    """Bir konu cumlesini Gemini metin modeliyle tam scenes.json yapisina cevirir."""
    system_prompt = f"""You are a scriptwriter and researcher for a fact-based, slightly cinematic YouTube
psychology/behavioral-science explainer channel (Canada-based, English audience).

Given a TOPIC (a real psychology/behavioral-science study or phenomenon), produce a complete video plan as
STRICT JSON (no markdown fences, no commentary, just the JSON object).

Style guide for every image_prompt (always start each image_prompt with this exact style block, then add
scene-specific action after it):
"{STYLE_GUIDE}"

Rules:
- Tone: intriguing, documentary-style, narratively hooks the viewer early, grounded in real accurate
  information. Not mean-spirited, fair to everyone involved.
- Exactly {num_scenes} scenes.
- CRITICAL BALANCE RULE: this is a MEN VS WOMEN comparison video. Across the scenes, you must give
  genuinely balanced screen time and narration to BOTH sides — describe what the research shows about women
  in some scenes AND what it shows about men in other scenes, back and forth, not just one side with the
  other barely mentioned. Roughly half the image_prompts should feature a woman as the visual subject and
  roughly half should feature a man, alternating naturally as the narration discusses each side's data point.
- Every scene object must include a "subject" field: exactly "woman" or "man", matching which one appears in
  that scene's image_prompt. This channel uses a FIXED recurring mascot character for each: a consistent
  woman character and a consistent man character appear in every episode (their exact look is supplied
  separately via a reference image, so your image_prompt does not need to redescribe their face/hair/clothing
  in detail — just describe their pose, expression, action, and the setting for that scene).
- Each "narration" is 1-2 short spoken sentences in English, natural conversational documentary tone,
  building a coherent narrative arc: hook -> the research on one side -> the research on the other side ->
  what the comparison reveals -> the explanation -> whether it holds up today -> a reflective closing question.
- The FIRST scene must open with an intriguing hook about the comparison, no title card needed.
- The LAST scene must end with a reflective question inviting viewers to comment their opinion, followed by
  a brief, natural "Subscribe for more." (not pushy).
- Each "image_prompt" must describe a specific, concrete visual action/scene (following the style guide
  above) featuring either the woman or the man as the subject (alternating per the balance rule), matching
  that scene's narration.
- "video_meta.title" is a catchy, clickable YouTube title (under 70 characters).
- "video_meta.description" MUST follow this exact structure:
  1. A 2-4 sentence hook paragraph describing the study/experiment and what it revealed, ending with an emoji.
  2. A blank line, then "In this video:" followed by 4-6 bullet lines, each starting with a relevant emoji,
     each a short phrase describing a specific beat/moment from the video.
  3. A blank line, then "📚 SOURCES & IMPORTANT NOTE" header, followed by a real citation of the actual
     study if you are confident of the real author/year/journal (format: Author(s) (Year). "Title." Journal,
     volume(issue), pages.) — only include a citation if you are reasonably confident it is accurate; if
     unsure, omit specific citation details and just describe the study generally. If the finding is known to
     be contested, failed replication, or debated, add a clear "IMPORTANT:" line noting this honestly, so
     viewers treat it as an open question rather than settled fact. If the finding is well-replicated and
     uncontroversial, state that instead.
  4. A blank line, then a short reflective question inviting comments (e.g. "So what do you think — ...").
  5. A blank line, then chapter timestamps in the format "00:00 [Chapter Name]" — create 4-6 sensible
     chapter markers spaced through the video's estimated runtime (assume ~{num_scenes*7} seconds total,
     roughly 7 seconds per scene).
  6. A blank line, then 4-6 relevant hashtags starting with #.
- "video_meta.tags" is a list of 5-10 relevant keyword tags.
- "thumbnail.headline_text" is a short, catchy, bold headline (under 45 characters) directly related to this
  specific episode's hook.
- "thumbnail.left_pose_prompt" describes ONLY the woman mascot's pose, expression, and one small topic-relevant
  secondary object/prop she is holding, wearing, or interacting with, relevant to this episode's topic (e.g.
  for a shopping-habits topic: "she is confidently holding a small shopping bag, smiling"). This secondary
  object must stay clearly smaller and less prominent than the character herself. Do not describe her face/
  hair/clothing design, only pose, expression, and the topic-relevant secondary object.
- "thumbnail.right_pose_prompt" is the same but for the man mascot (e.g. "he is looking at an empty wallet
  with a worried expression").

CRITICAL: The output must be STRICTLY VALID, parseable JSON. Any double-quote characters that appear inside
a string value (for example around a study or journal title in the citation) MUST be escaped as \". Do not
use unescaped straight quotes inside string values. Do not include trailing commas.

Output EXACTLY this JSON schema, nothing else:
{{
  "video_meta": {{"title": "...", "description": "...", "tags": ["...", "..."]}},
  "thumbnail": {{"headline_text": "...", "left_pose_prompt": "...", "right_pose_prompt": "..."}},
  "scenes": [
    {{"image_prompt": "...", "narration": "...", "subject": "woman"}}
  ]
}}

TOPIC: {topic}
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
    print(f"Konu senaryoya çevriliyor: {topic[:60]}...")

    max_attempts = 3
    config = None
    for attempt in range(max_attempts):
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

        try:
            config = json.loads(text_out)
            break
        except json.JSONDecodeError as e:
            print(f"Senaryo JSON'u bozuk geldi (deneme {attempt + 1}/{max_attempts}): {e}")
            if attempt == max_attempts - 1:
                raise
            continue

    for scene in config["scenes"]:
        subject = scene.get("subject", "").lower()
        scene["reference_image_url"] = MAN_REFERENCE_URL if subject == "man" else WOMAN_REFERENCE_URL

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
    for i, scene in enumerate(scenes, start=1):
        narration = scene.get("narration", "").strip()
        if not narration:
            print(f"[{i}] UYARI: bu sahnede 'narration' eksik/boş, bir önceki cümle tekrar kullanılıyor.")
            narration = scenes[i - 2].get("narration", "...") if i > 1 else "..."

        ref = scene.get("reference_image_url")

        try:
            image_path = generate_image(scene.get("image_prompt", ""), i, reference_image=ref)
            audio_path = generate_audio(narration, i)
            clip_path = make_scene_clip(image_path, audio_path, i, subtitle_text=narration)
            clip_paths.append(clip_path)
        except Exception as e:
            print(f"[{i}] SAHNE ATLANDI (tekrar denemelere rağmen başarısız oldu): {e}")
            continue

    if not clip_paths:
        raise RuntimeError("Hiçbir sahne üretilemedi, video oluşturulamıyor.")

    final_video = concat_clips(clip_paths)

    thumb_path = None
    if thumb_cfg.get("headline_text") or meta.get("title"):
        thumb_path = generate_thumbnail(thumb_cfg, meta=meta)

    print(f"\nBitti! Video hazır: {final_video}")
    if thumb_path:
        print(f"Kapak resmi hazır: {thumb_path}")

    upload_to_youtube(final_video, thumb_path, meta)


if __name__ == "__main__":
    main()
