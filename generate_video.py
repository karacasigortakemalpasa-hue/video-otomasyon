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
GIPHY_API_KEY = os.environ.get("GIPHY_API_KEY", "").strip()
FREESOUND_API_KEY = os.environ.get("FREESOUND_API_KEY", "")

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
    """Kapak resmi: kadin ve erkek TEK istekte, IKI referansla birlikte, tek/butunluklu bir sahne
    olarak uretilir (ayri ayri uretip yan yana koymak 'iki vesikalik kutu' hissi veriyordu, bu
    yontem bunu onluyor). Baslik yazisi bizim gercek fontumuzla, logo bizim gercek logo dosyamizla,
    ffmpeg ile garantili sekilde (AI'a birakilmadan) eklenir."""
    left_prompt = thumb_cfg.get("left_pose_prompt", "standing confidently")
    right_prompt = thumb_cfg.get("right_pose_prompt", "standing confidently")
    headline = thumb_cfg.get("headline_text") or (meta or {}).get("title", "")

    scene_prompt = f"""Create a single richly detailed, dramatic comic-book style illustration, 16:9 widescreen, full frame, one continuous plain white background — no panels, no borders, no separate colored zones, both characters share the exact same seamless white background.
On the LEFT side of the frame: the woman, EXACTLY as shown in her reference image (identical face, hair, and clothing — do not restyle or redesign her), {left_prompt}.
On the RIGHT side of the frame: the man, EXACTLY as shown in his reference image (identical face, hair, and clothing — do not restyle or redesign him), {right_prompt}.
Both characters large, filling most of the frame height, three-quarter angle, bold dynamic shading and dramatic expression for visual impact. Leave the center-top area and a small gap between the two characters clear of any characters or objects (this space will have text and a logo added afterward).
Do not include any text, letters, numbers, logos, or badges in the image."""

    final_thumb = os.path.join(OUTPUT_DIR, "thumbnail.jpg")
    scene_img = os.path.join(OUTPUT_DIR, "thumb_scene.jpg")

    print("Kapak sahnesi (kadın+erkek birlikte, tek istek) üretiliyor...")
    _gemini_generate_image(scene_prompt, scene_img, aspect_ratio="16:9", reference_image_url=[WOMAN_REFERENCE_URL, MAN_REFERENCE_URL])

    font = "font.ttf"  # repo koklerinden yuklenen Chewy fontu
    logo_path = "thumbnail_logo.png"  # repo koklerinden yuklenen seffaf logo PNG'si
    safe_headline = headline.replace("'", "\u2019").replace(":", "\\:").replace(",", "\\,")

    has_logo = os.path.exists(logo_path)

    inputs = ["-i", scene_img]
    if has_logo:
        inputs += ["-i", logo_path]

    filter_complex = (
        f"[0:v]drawtext=fontfile={font}:text='{safe_headline}':fontcolor=black:fontsize=64:"
        "borderw=4:bordercolor=white:"
        "x=(w-text_w)/2:y=20[bg3]"
    )
    if has_logo:
        filter_complex += (
            ";[1:v]scale=-1:170[logo];"
            "[bg3][logo]overlay=x=(W-w)/2:y=(H-h)/2+40[out]"
        )
    else:
        filter_complex += (
            f";[bg3]drawtext=fontfile={font}:text='VS':fontcolor=0xFF2222:fontsize=70:"
            "borderw=5:bordercolor=white:x=(w-text_w)/2:y=(h-text_h)/2+40[out]"
        )

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", "[out]",
        "-frames:v", "1", "-update", "1",
        final_thumb,
    ]
    print("Kapak birleştiriliyor (gerçek font + gerçek logo, garantili şekilde)...")
    subprocess.run(cmd, check=True, capture_output=True)
    return final_thumb




def generate_audio(text: str, index: int, voice_name: str = "en-GB-Neural2-F", language_code: str = "en-GB",
                    speaking_rate: float = 1.0, pitch: float = 0.0, emphasize_last_words: int = 0) -> str:
    """Google Cloud Text-to-Speech ile seslendirme üretir, mp3 dosya yolu döner.
    emphasize_last_words > 0 ise, cumlenin son N kelimesi SSML ile vurgulanir (daha carpici teslimat icin)."""
    if not GOOGLE_TTS_KEY:
        raise RuntimeError("GOOGLE_TTS_API_KEY tanımlı değil.")

    if emphasize_last_words > 0:
        words = text.split()
        if len(words) > emphasize_last_words:
            head = " ".join(words[:-emphasize_last_words])
            tail = " ".join(words[-emphasize_last_words:])
            ssml_text = f"<speak>{head} <emphasis level=\"strong\">{tail}</emphasis></speak>"
        else:
            ssml_text = f"<speak><emphasis level=\"strong\">{text}</emphasis></speak>"
        input_field = {"ssml": ssml_text}
    else:
        input_field = {"text": text}

    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_KEY}"
    payload = {
        "input": input_field,
        "voice": {"languageCode": language_code, "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": speaking_rate, "pitch": pitch},
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
- "thumbnail.headline_text" is a short, catchy, bold headline (STRICTLY under 30 characters, must fit on a
  single line) directly related to this
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


def expand_topic_to_short(topic: str) -> dict:
    """Bir konuyu, YouTube Shorts icin hizli/carpici, karsilikli konusma tarzinda kisa bir
    senaryoya cevirir (ayri, bagimsiz bir uretim - uzun videodan kesilmiyor)."""
    system_prompt = f"""You are writing a punchy, fast-paced YouTube SHORTS script (vertical, under 60 seconds)
for a "Men vs Women" comparison channel. This is a SEPARATE, standalone production from the long-form video,
even though it covers the same general topic.

TOPIC: {topic}

Write STRICT JSON (no markdown fences, no commentary, just the JSON object).

FORMAT: 6 to 9 very short, punchy lines, alternating back and forth between the woman and the man like a
rapid back-and-forth exchange or quick-fire fact reveal (each line under 12 words, quotable, surprising, or
funny). Line 1 must be a scroll-stopping hook question or bold claim. The LAST line must be a quick, natural
call to action to follow/subscribe for more (short, not corny).

Each line object needs:
- "subject": "woman" or "man" — whose reaction/face is shown for this line
- "text": the short spoken line

video_meta.title: a short, curiosity-driven, clickable Shorts title (under 55 characters), often phrased as
a question or bold claim.
video_meta.description: 1-2 punchy hook sentences, then a few relevant hashtags including #Shorts as the
first hashtag.
video_meta.tags: 5-8 short relevant keyword tags.

CRITICAL: Output must be STRICTLY VALID JSON. Escape any internal double quotes as \\". No trailing commas.

Output EXACTLY this schema:
{{
  "video_meta": {{"title": "...", "description": "...", "tags": ["...", "..."]}},
  "lines": [
    {{"subject": "woman", "text": "..."}}
  ]
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
    print(f"Short senaryoya çevriliyor: {topic[:60]}...")

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

        text_out = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        if text_out.startswith("```"):
            text_out = text_out.split("```")[1]
            if text_out.startswith("json"):
                text_out = text_out[4:]
        try:
            config = json.loads(text_out)
            break
        except json.JSONDecodeError as e:
            print(f"Short JSON'u bozuk geldi (deneme {attempt + 1}/{max_attempts}): {e}")
            if attempt == max_attempts - 1:
                raise
            continue

    return config


def fetch_gif(query: str, out_path: str) -> bool:
    """GIPHY'den konuya uygun bir GIF indirir, guvenilir dongu icin hemen MP4'e cevirir.
    out_path'in uzantisi .mp4 olmali. Basarili olursa True, olmazsa False doner (cokmez)."""
    if not GIPHY_API_KEY:
        return False
    print(f"(GIPHY_API_KEY uzunluğu: {len(GIPHY_API_KEY)} karakter — normalde ~32 civarı olmalı)")
    # Sadece ilk birkaç anahtar kelimeyi kullan (kisa/basit sorgu, URI limitine takilmamak icin)
    short_query = " ".join(query.split()[:4])
    raw_gif_path = out_path.replace(".mp4", "_raw.gif")
    try:
        search_url = (
            f"https://api.giphy.com/v1/gifs/search?api_key={GIPHY_API_KEY}"
            f"&q={urllib.parse.quote(short_query)}&limit=1&rating=g"
        )
        req = urllib.request.Request(search_url)
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        data = result.get("data", [])
        if not data:
            return False
        gif_url = data[0]["images"]["fixed_height"]["url"]
        req2 = urllib.request.Request(gif_url)
        with urllib.request.urlopen(req2, timeout=30) as resp2:
            gif_bytes = resp2.read()
        with open(raw_gif_path, "wb") as f:
            f.write(gif_bytes)

        # Guvenilir dongu icin GIF'i duzgun bir MP4'e cevir (ses yok, sabit fps)
        cmd = [
            "ffmpeg", "-y", "-ignore_loop", "0", "-i", raw_gif_path,
            "-vf", "fps=20,scale=400:-2:flags=lanczos",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
            out_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        return os.path.exists(out_path)
    except subprocess.TimeoutExpired:
        print(f"UYARI: GIF->MP4 dönüşümü 30 saniyede bitmedi, GIF'siz devam ediliyor ({query}).")
        return False
    except Exception as e:
        print(f"UYARI: GIPHY GIF çekilemedi ({query}): {e}")
        return False


def fetch_freesound_sfx(query: str, out_path: str) -> bool:
    """Freesound'dan CC0 (telifsiz), kisa (0-3sn) bir ses efekti indirir. Basarisiz olursa False doner."""
    if not FREESOUND_API_KEY:
        return False
    try:
        search_url = (
            "https://freesound.org/apiv2/search/text/"
            f"?query={urllib.parse.quote(query)}"
            f"&filter=license:%22Creative+Commons+0%22+duration:%5B0+TO+3%5D"
            f"&sort=score&fields=id,previews,duration&page_size=1&token={FREESOUND_API_KEY}"
        )
        req = urllib.request.Request(search_url)
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        results = result.get("results", [])
        if not results:
            return False
        preview_url = results[0]["previews"].get("preview-hq-mp3") or results[0]["previews"].get("preview-lq-mp3")
        if not preview_url:
            return False
        req2 = urllib.request.Request(preview_url)
        with urllib.request.urlopen(req2, timeout=30) as resp2:
            audio_bytes = resp2.read()
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
        return True
    except Exception as e:
        print(f"UYARI: Freesound sesi çekilemedi ({query}): {e}")
        return False


def build_transition_sfx(out_path: str, kind: str = "ding"):
    """Sahne gecisleri icin kisa, cesitli sentetik sesler uretir (telifsiz)."""
    if kind == "whoosh":
        filt = "sine=frequency=300:duration=0.3,afade=t=in:d=0.02,afade=t=out:st=0.2:d=0.1,volume=0.3"
    elif kind == "pop":
        filt = "sine=frequency=900:duration=0.12,afade=t=out:st=0.03:d=0.09,volume=0.4"
    elif kind == "drum":
        filt = "sine=frequency=150:duration=0.35,tremolo=f=18:d=0.8,afade=t=out:st=0.25:d=0.1,volume=0.3"
    else:  # ding
        filt = "sine=frequency=1200:duration=0.25,afade=t=in:d=0.02,afade=t=out:st=0.17:d=0.08,volume=0.35"

    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", filt, out_path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=20)


def make_short_line_clip(subject: str, text: str, index: int, is_hook: bool = False, gif_path: str = None) -> tuple:
    """Short icin tek bir satiri (karakter gorseli + ses + hareketli altyazi + GIF sticker) uretir.
    gif_path verilirse (mp4 formatinda), kosede kucuk bir loop'lu sticker olarak eklenir. Sahneler
    arasi gecis burada degil, concat_shorts_with_crossfade() asamasinda yumusak xfade ile yapilir."""
    ref = MAN_REFERENCE_URL if subject == "man" else WOMAN_REFERENCE_URL
    img_path = os.path.join(IMG_DIR, f"short_{index:02d}.jpg")

    prompt = (f"Bold Pop-Art comic style illustration, thick black outlines, bright flat primary colors "
              f"(red, yellow, blue), Ben-Day halftone dot shading in the background, plain solid white "
              f"background, no scenery, comic panel aesthetic reminiscent of classic Pop-Art. "
              f"The character keeps the exact same face, hairstyle, and clothing design as shown in the "
              f"reference image (same identity, same silhouette — do not redesign who they are), but "
              f"rendered with this bold Pop-Art linework and coloring treatment. Waist-up, reacting to this "
              f"line with a clear, exaggerated, energetic expression: \"{text}\". Include one small, clearly "
              f"relevant prop or visual element tied directly to the content of this line (for example: a "
              f"shopping bag, a price tag, an empty wallet, a cash register, a pile of money, a receipt — "
              f"whatever concretely matches what the line is about), held by or positioned right next to "
              f"the character, rendered in the same Pop-Art style. This prop must stay clearly smaller and "
              f"less prominent than the character, reinforcing the topic visually without overshadowing "
              f"them. Vertical portrait framing, character centered.")

    _gemini_generate_image(prompt, img_path, aspect_ratio="9:16", reference_image_url=ref)
    time.sleep(8)  # dakikalik hiz limitine takilmamak icin (uzun videodaki gibi)

    voice = "en-GB-Neural2-F" if subject == "woman" else "en-GB-Neural2-D"
    pitch = 2.0 if subject == "woman" else -2.0
    audio_path = generate_audio(
        text, index, voice_name=voice,
        speaking_rate=1.12, pitch=pitch, emphasize_last_words=2,
    )
    duration = get_audio_duration(audio_path)

    font = "font.ttf"
    logo_path = "thumbnail_logo.png"
    fps = 30
    total_frames = max(1, int(duration * fps))

    # Hareket: kanca (ilk) cumlede hizli zoom-punch, digerlerinde yumusak Ken Burns
    zoom_rate = "0.0035" if is_hook else "0.0009"
    zoom_max = "1.18" if is_hook else "1.10"

    # Alt yazi: kelime obekleri halinde, sirayla belirip kaybolan "flash caption" tarzi
    words = text.split()
    chunk_size = 3
    chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
    if not chunks:
        chunks = [""]
    seg_dur = duration / len(chunks)

    caption_filters = []
    for i, chunk in enumerate(chunks):
        safe_chunk = chunk.replace("'", "\u2019").replace(":", "\\:").replace(",", "\\,")
        start = i * seg_dur
        end = (i + 1) * seg_dur
        caption_filters.append(
            f"drawtext=fontfile={font}:text='{safe_chunk}':fontcolor=white:fontsize=64:"
            f"borderw=6:bordercolor=black:x=(w-text_w)/2:y=h-360:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
    captions_chain = ",".join(caption_filters)

    clip_path = os.path.join(CLIP_DIR, f"short_clip_{index:02d}.mp4")
    inputs = ["-loop", "1", "-i", img_path, "-i", audio_path]
    next_input_idx = 2  # 0=img, 1=audio, siradaki input bu index'ten baslar

    filter_parts = [
        "[0:v]scale=1350:2400:force_original_aspect_ratio=increase,crop=1350:2400,"
        f"zoompan=z='min(zoom+{zoom_rate},{zoom_max})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={total_frames}:s=1080x1920:fps={fps}[bg]",
        f"[bg]{captions_chain}[bg2]",
    ]

    current_label = "bg2"
    has_logo = os.path.exists(logo_path)
    has_gif = gif_path and os.path.exists(gif_path)

    if has_gif:
        inputs += ["-stream_loop", "-1", "-i", gif_path]
        filter_parts.append(f"[{next_input_idx}:v]scale=280:-1[gifstk]")
        filter_parts.append(f"[{current_label}][gifstk]overlay=x=W-w-30:y=H-h-350:shortest=1[bg4]")
        current_label = "bg4"
        next_input_idx += 1

    if has_logo:
        inputs += ["-i", logo_path]
        filter_parts.append(f"[{next_input_idx}:v]scale=180:-1[logo]")
        filter_parts.append(f"[{current_label}][logo]overlay=x=(W-w)/2:y=40[out]")
        current_label = "out"
        next_input_idx += 1

    if current_label != "out":
        # Ne gif ne logo eklendi, son etiketi dogrudan [out] yapalim
        filter_parts[-1] = filter_parts[-1].replace(f"[{current_label}]", "[out]")

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        "-shortest", "-t", str(duration),
        clip_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=90)
    return clip_path, duration


def get_sfx_file(sfx_kind: str, out_path: str):
    """Bir gecis sesi dosyasi hazirlar: once gercek (Freesound, CC0) ses denenir, olmazsa sentetige duser."""
    real_query = {
        "whoosh": "whoosh", "pop": "pop",
        "ding": "notification bell", "drum": "drum hit",
    }.get(sfx_kind, "pop")
    got_real = fetch_freesound_sfx(real_query, out_path)
    if not got_real:
        build_transition_sfx(out_path, kind=sfx_kind)


def concat_shorts_with_crossfade(clip_paths: list, durations: list, sfx_kinds: list, transition: float = 0.3) -> str:
    """Short sahnelerini yumusak crossfade (video+ses) ile birlestirir, VE her gecis noktasina
    tam zamaninda, gercekten duyulan bir gecis sesi karistirir."""
    final_path = os.path.join(OUTPUT_DIR, "final_short_raw.mp4")

    if len(clip_paths) == 1:
        subprocess.run(["cp", clip_paths[0], final_path], check=True)
        return final_path

    inputs = []
    for p in clip_paths:
        inputs += ["-i", p]

    filter_parts = []
    prev_v, prev_a = "0:v", "0:a"
    running_offset = durations[0] - transition
    transition_timestamps = []

    for i in range(1, len(clip_paths)):
        vout, aout = f"v{i}", f"a{i}"
        filter_parts.append(f"[{prev_v}][{i}:v]xfade=transition=fade:duration={transition}:offset={running_offset:.3f}[{vout}]")
        filter_parts.append(f"[{prev_a}][{i}:a]acrossfade=d={transition}[{aout}]")
        transition_timestamps.append(running_offset + transition / 2)
        prev_v, prev_a = vout, aout
        if i < len(clip_paths) - 1:
            running_offset += durations[i] - transition

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{prev_v}]", "-map", f"[{prev_a}]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        final_path,
    ]
    print("Short sahneleri yumuşak geçişlerle (crossfade) birleştiriliyor...")
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # Simdi her gecis noktasina, tam zamaninda bir gecis sesi karistir
    sfx_paths = []
    for i, kind in enumerate(sfx_kinds[:len(transition_timestamps)]):
        sfx_path = os.path.join(AUDIO_DIR, f"transition_sfx_{i:02d}.mp3")
        get_sfx_file(kind, sfx_path)
        sfx_paths.append(sfx_path)

    if not sfx_paths:
        return final_path

    mixed_path = os.path.join(OUTPUT_DIR, "final_short.mp4")
    sfx_inputs = []
    for p in sfx_paths:
        sfx_inputs += ["-i", p]

    delay_labels = []
    delay_parts = []
    for i, (ts, sfx_in_idx) in enumerate(zip(transition_timestamps, range(1, len(sfx_paths) + 1))):
        delay_ms = int(ts * 1000)
        delay_parts.append(f"[{sfx_in_idx}:a]adelay={delay_ms}|{delay_ms},volume=1.4[sfxd{i}]")
        delay_labels.append(f"[sfxd{i}]")

    amix_inputs = "[0:a]" + "".join(delay_labels)
    amix_count = 1 + len(delay_labels)
    mix_filter = ";".join(delay_parts) + f";{amix_inputs}amix=inputs={amix_count}:duration=first:dropout_transition=0:normalize=0[aout]"

    cmd2 = [
        "ffmpeg", "-y", "-i", final_path, *sfx_inputs,
        "-filter_complex", mix_filter,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2",
        mixed_path,
    ]
    print("Geçiş sesleri tam zamanında karıştırılıyor...")
    subprocess.run(cmd2, check=True, capture_output=True, timeout=90)
    return mixed_path


def process_short(config: dict):
    """Tamamen bagimsiz bir Shorts uretim + yukleme akisi."""
    lines = config["lines"]
    meta = config.get("video_meta", {})
    sfx_kinds_cycle = ["whoosh", "pop", "ding", "drum"]

    clip_paths = []
    durations = []
    for i, line in enumerate(lines, start=1):
        subject = line.get("subject", "woman")
        text = line.get("text", "")
        print(f"[Short {i}] üretiliyor ({subject})...")

        # Her satir icin, o satirin kendi metnine gore konuya uygun bir GIF cekmeyi dene.
        # Son satir (abone ol cagrisi) icin ozel olarak 'subscribe/follow' temali bir GIF denenir.
        gif_query = "subscribe follow celebrate" if i == len(lines) else text
        gif_path = os.path.join(IMG_DIR, f"line_gif_{i:02d}.mp4")
        got_gif = fetch_gif(gif_query, gif_path)
        if not got_gif:
            gif_path = None

        clip_path = None
        try:
            clip_path, duration = make_short_line_clip(subject, text, i, is_hook=(i == 1), gif_path=gif_path)
        except Exception as e:
            print(f"[Short {i}] SATIR ATLANDI (başarısız oldu): {e}")
            continue
        clip_paths.append(clip_path)
        durations.append(duration)

    if not clip_paths:
        raise RuntimeError("Hiçbir satır üretilemedi, Short oluşturulamıyor.")

    sfx_kinds = [sfx_kinds_cycle[i % len(sfx_kinds_cycle)] for i in range(len(clip_paths) - 1)]
    final_short = concat_shorts_with_crossfade(clip_paths, durations, sfx_kinds)

    print(f"\nShort hazır: {final_short}")
    upload_to_youtube(final_short, None, meta)



def main():
    if len(sys.argv) < 2:
        print("Kullanım: python generate_video.py scenes.json")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        config = json.load(f)

    if config.get("format") == "short" and "lines" not in config:
        config = expand_topic_to_short(config["topic"])
        with open(os.path.join(OUTPUT_DIR, "expanded_short.json"), "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        process_short(config)
        return

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
