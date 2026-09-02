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
import hashlib

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

# Kanalin sabit gorsel kimligi: risograph iki-renk baski tarzi. Karakter/maskot yok -
# tutarli bir illustrasyon DILI var, ama HER BOLUMDE birebir ayni renk cifti kullanilmiyor -
# YouTube'un "sablon klonu" olarak isaretleyebilecegi mekanik tekrari azaltmak icin,
# konuya gore 3 farkli (ama ayni aileden) iki-renk paletinden biri seciliyor.
PALETTES = [
    "deep plum violet ink and antique brass gold ink ONLY",
    "deep prussian blue ink and burnt rust orange ink ONLY",
    "dark forest green ink and faded crimson red ink ONLY",
]



def _stable_hash(text: str) -> int:
    """Python'un yerlesik hash() fonksiyonu SUREC BAZINDA RASTGELE (guvenlik icin) - yani ayni
    metin, farkli calistirmalarda (orn. her GitHub Actions calismasi) FARKLI hash degeri
    verebiliyor. Bu, 'ayni konu hep ayni paleti/sablonu alir' tutarliligini bozuyordu.
    Bunun yerine, calistirmalar arasi GERCEKTEN sabit olan bu fonksiyonu kullaniyoruz."""
    return int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)

def style_guide_for_topic(topic: str) -> str:
    """Konuya gore (tutarli, ayni konu hep ayni paleti alsin diye hash tabanli) bir palet secer."""
    palette = PALETTES[_stable_hash(topic) % len(PALETTES)]
    return (
        f"Two-color risograph print illustration, {palette}. Coarse halftone dot grain. "
        "Deliberate ink misregistration. Heavy paper texture. Imperfect hand-inked outlines "
        "with breaks and bleeds. Sparse, editorial composition. Flat 2D print aesthetic, no "
        "3D, no photorealism, no digital polish. Vintage investigative-journalism poster "
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


def generate_image(prompt: str, out_path: str, topic: str, aspect_ratio: str = "16:9"):
    full_prompt = f"{style_guide_for_topic(topic)} {prompt}"
    _gemini_generate_image(full_prompt, out_path, aspect_ratio=aspect_ratio)
    time.sleep(8)  # dakikalik hiz limitine takilmamak icin


def _try_gemini_tts(text: str, tone_prompt: str, out_path: str) -> bool:
    """DENEYSEL: Google'in yeni Gemini-TTS modeliyle, dogal dilde ton/duygu talimati vererek
    seslendirme uretmeyi dener (orn. 'bunu kuru bir alaycilikla soyle'). Herhangi bir sebeple
    basarisiz olursa False doner - cagiran taraf guvenilir eski TTS'e gecer, hicbir risk yok."""
    if not tone_prompt:
        return False
    try:
        token = _get_vertex_access_token()
        # Google'in kendi ornegi SADECE Bearer token kullaniyor - API key ile karistirmiyoruz.
        url = "https://texttospeech.googleapis.com/v1/text:synthesize"
        payload = {
            "input": {"text": text, "prompt": tone_prompt},
            "voice": {"languageCode": "en-US", "name": "Charon", "modelName": "gemini-2.5-flash-tts"},
            "audioConfig": {"audioEncoding": "MP3"},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "x-goog-user-project": GCP_PROJECT_ID,
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        audio_bytes = base64.b64decode(result["audioContent"])
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"  (Gemini-TTS denemesi başarısız, standart sese dönülüyor: HTTP {e.code}: {body})")
        return False
    except Exception as e:
        print(f"  (Gemini-TTS denemesi başarısız, standart sese dönülüyor: {e})")
        return False


def generate_audio(text: str, index: int, prefix: str = "scene", speaking_rate: float = 0.97,
                    pitch: float = 0.0, volume_gain_db: float = 10.0, emphasize_last_words: int = 0,
                    tone_prompt: str = "") -> str:
    """Google Cloud TTS ile seslendirme uretir. tone_prompt verilirse once deneysel Gemini-TTS
    ile (dogal dilde duygu/ton yonlendirmesi) denenir; basarisiz olursa otomatik olarak
    guvenilir standart sese (Neural2) duser."""
    if not GOOGLE_TTS_KEY:
        raise RuntimeError("GOOGLE_TTS_API_KEY tanımlı değil.")

    out_path = os.path.join(AUDIO_DIR, f"{prefix}_{index:03d}.mp3")
    print(f"[{prefix} {index}] Seslendirme üretiliyor ({len(text)} karakter)...")

    if tone_prompt and _try_gemini_tts(text, tone_prompt, out_path):
        return out_path

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
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    audio_bytes = base64.b64decode(result["audioContent"])
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
                     flash_caption: bool = False, vertical: bool = False, prefix: str = "clip",
                     zoom_rate: float = 0.0006) -> str:
    """Sabit gorsele Ken Burns efekti + altyazi uygulayip sesle birlestirir.
    vertical=True ise Short icin 1080x1920 dikey kadraj kullanir.
    zoom_rate, bolumden bolume hafifce degisen yaklasma hizi (mekanik tekrari azaltmak icin)."""
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
        f"zoompan=z='min(zoom+{zoom_rate},1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s={canvas}:fps={fps}",
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


def generate_thumbnail(key_object: str, reaction: str, headline_text: str, topic: str) -> str:
    """Kapak resmini SABIT 'abartili tepki enerjisi' (MrBeast tarzi) ile uretir - bu, videonun
    geri kalanindaki (risograph) tarzdan BILEREK farkli, yuksek enerji/yuksek CTR kapak stili.
    AYNI KOMPOZISYONUN her seferinde tekrarlanmasi ("sablon klonu" riski) onlenmesi icin, konuya
    gore (hash tabanli, tutarli) 3 farkli kompozisyon SABLONU arasinda donusum var - enerji/renk
    dili sabit kaliyor, sadece yerlesim degisiyor. Kanal stilinden BAGIMSIZ calisir."""
    compositions = [
        (
            f"Extreme close-up of a generic, anonymous person's face showing {reaction}, mouth "
            f"open, eyes wide, intensely exaggerated expression, positioned right next to a large, "
            f"sharply detailed rendering of {key_object}."
        ),
        (
            f"A large, dramatically lit rendering of {key_object} fills most of the frame, with a "
            f"bold thick red circle highlighting one small shocking detail on it, no face in frame, "
            f"intense dramatic side lighting creating deep shadows."
        ),
        (
            f"A single hand reaching into frame to touch {key_object}, with just the edge of a "
            f"generic anonymous face showing {reaction} cropped at the corner of the frame, dynamic "
            f"diagonal composition."
        ),
    ]
    composition = compositions[_stable_hash(topic) % len(compositions)]
    safe_headline = headline_text.replace("'", "\u2019")

    # Yazi TAMAMEN Gemini'ye birakiliyor (ffmpeg ile yazi eklemiyoruz artik - gorunumu kotu
    # buluyoruz). Gemini metin konusunda genel olarak guclu, ama biz FLASH-LITE (hafif/ucuz)
    # surumu kullaniyoruz - PRO surum kadar guvenilir olmayabilir. API cagrisi tamamen
    # basarisiz olursa (network hatasi vs.), en azindan YAZISIZ bir kapak kalsin diye yedek var -
    # hic kapak olmamasindan iyi, ama bu YAZI KALITESINI garanti etmez.
    final_thumb = os.path.join(OUTPUT_DIR, "thumbnail.jpg")
    scene_prompt_with_text = (
        f"High-energy clickbait YouTube thumbnail in the MrBeast style. {composition} Punchy "
        f"oversaturated colors, bold high contrast lighting, dramatic rim light, shallow depth of "
        f"field with a blurred vivid background, professional photography, 8k, sharp focus. "
        f"Include bold impact-style text at the top of the image reading exactly "
        f"\"{safe_headline}\" in large, thick, high-contrast letters with a black outline, "
        f"perfectly spelled, clearly readable."
    )
    try:
        _gemini_generate_image(scene_prompt_with_text, final_thumb, aspect_ratio="16:9")
        return final_thumb
    except Exception as e:
        print(f"  (Kapakta AI yazi denemesi basarisiz, yazisiz kapaga donuluyor: {e})")

    scene_prompt_no_text = (
        f"High-energy clickbait YouTube thumbnail in the MrBeast style. {composition} Punchy "
        f"oversaturated colors, bold high contrast lighting, dramatic rim light, shallow depth of "
        f"field with a blurred vivid background, professional photography, 8k, sharp focus. No "
        f"text, no letters, no numbers anywhere in the image."
    )
    _gemini_generate_image(scene_prompt_no_text, final_thumb, aspect_ratio="16:9")
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

OPENING VARIETY RULE (applies to long_scenes[0]):
Do NOT always use the exact same opening formula. Pick ONE of these three opening styles at random for this
episode, based on whichever fits the story best:
(a) An abstract, thought-provoking QUESTION tied to the deeper theme (e.g. "Does evidence matter more than
    what we want to believe?").
(b) A bold, flat, confident CLAIM stated as fact, that the episode will complicate (e.g. "For forty years,
    the world's smartest scientists were completely wrong about a skull.").
(c) A direct address daring the viewer's own assumption (e.g. "You've probably never questioned this — but
    you should.").
In all three cases: NEVER start with a greeting or "today we're talking about X". The concrete story/name/
event follows a few lines later, not immediately.

EDITORIAL VOICE REQUIREMENT: Somewhere in the long-form script (not the Short), include at least one moment
of genuine first-person-feeling editorial interpretation or dry observation from the narrator — a specific,
episode-tailored aside that reflects a point of view, not just a recitation of facts (e.g. a wry comment on
the irony of the story, a pointed observation about what it says about the people involved, a moment of
"and here's the part most retellings leave out"). This must be tailored to THIS story's specific details, not
a generic template phrase reusable across episodes.

TONE DIRECTION PER SCENE: Every scene needs a "tone_prompt" field — a short (5-10 word) natural-language
delivery direction for a narrator reading that specific line, tailored to what's actually happening in it
(e.g. "gravely, letting the weight of it land", "with dry, knowing sarcasm", "quick and urgent, almost
alarmed", "curious, leaning in", "flat and matter-of-fact, almost deadpan"). Vary these across the script to
match the emotional arc of the story — a hook should feel different from a tragic detail, which should feel
different from a wry aside. Do not reuse the exact same tone_prompt twice in one script.

LONG-FORM SPECIAL EPISODE (long_scenes): This is a LONGER special episode — approximately 75 to 85 scenes,
targeting 10 to 12 minutes of spoken duration (roughly double the channel's normal ~5 minute episode length).
A video this long CANNOT rely on a single opening hook — it needs several hooks stitched together along the
way, or attention drops. Structure it like this:

1. OPENING HOOK (1, per Opening Variety Rule) — the strongest hook in the episode.
2. CONCRETE SETUP (2-4 scenes) — establish the real subject/premise clearly and specifically.
3. ACT ONE (roughly 15-20 scenes) — the first major thread of the story, ending on a genuine mini-cliffhanger
   or surprising mid-point revelation that makes the viewer need to keep watching (not just "and then...").
4. EARLY SUBSCRIBE ASK (1 scene, short and natural, placed here after Act One has landed — e.g. "If this
   kind of deep dive is your thing, this is exactly what we do here every week — subscribe.")
5. ACT TWO (roughly 20-25 scenes) — the next major thread, going deeper, with real specific detail and at
   least one more genuine surprising turn partway through to re-hook attention.
6. MID-VIDEO COMMENT-BAIT LINE (1, natural, e.g. "Comment below what you'd have done in that position") —
   placed roughly around the halfway-to-two-thirds point.
7. ACT THREE (roughly 15-20 scenes) — the core twist/reveal and its full unpacking, with real specificity,
   this is the emotional and informational peak of the episode.
8. THE DEEPER MECHANISM (roughly 8-12 scenes) — the psychological/historical throughline explained with real
   specificity, avoiding vague clichés, connecting the specific story to the timeless human pattern.
9. RESOLUTION (2-3 scenes).
10. REFLECTIVE CLOSING QUESTION + closing subscribe line (1-2 scenes).

Each scene: concrete narration sentence(s) + a matching risograph-style image_prompt (no text/letters in the
image itself). Because this episode is much longer than usual, it is EXTRA important that every scene adds
real, specific, non-repetitive information or a genuine emotional beat — never pad or restate a point already
made. Vary sentence rhythm and pacing across the episode so no long stretch feels monotone.

This is a LONG-FORM-ONLY special episode — do NOT generate a Short script.

The script must be FACTUALLY GROUNDED — use real documented history. If uncertain of an exact number/date,
use a reasonable, clearly-labeled approximation rather than fabricating false precision. Avoid generic
clichés — every scene should add real, specific information.

video_meta.title: curiosity-driven, under 65 characters. It should still clearly signal the real, concrete
topic (the actual name/event), not be pure abstract clickbait — curiosity AND clarity together, not one
instead of the other.
video_meta.description: Write 200-300 words, structured for YouTube's discovery algorithm, in this order:
(1) The very FIRST sentence must clearly and plainly state the real subject of the video in concrete terms
    (the actual name, event, era, and topic category — e.g. "This video explores the true story of [X], a
    [historical hoax/scandal] from [era]...") so both viewers and YouTube's system immediately know what
    this is about, even if only the first sentence is visible before "show more".
(2) Then 2-3 sentences expanding naturally on what the video actually covers and why it matters — use
    several different natural phrasings of the topic and its themes (semantic variation: e.g. don't only say
    "hoax" ten times — also use "scandal", "deception", "forgotten history", "psychology of belief" etc.
    where accurate) so the description reads as genuinely rich, not keyword-stuffed.
(3) A short factual note on sourcing/accuracy if relevant.
(4) End with 3-5 relevant hashtags.
Never repeat the same exact keyword unnaturally — vary phrasing, write like a real, well-informed video
description a human researcher would write, not an SEO template.
video_meta.tags: 8-12 relevant keyword tags, mixing broad category tags (e.g. "history", "true crime",
"psychology") with specific niche tags naming the actual subject.
thumbnail.key_object: Analyze this specific story and identify ONE single physical object that best
symbolizes it (e.g. "an old vaudeville radio microphone", "a wedding ring", "a small glass vial", "a
child's toy robot"). Short phrase, 3-8 words. Pick something concrete and visually strong, not abstract.
thumbnail.reaction: A short (2-5 word) description of an exaggerated human emotional reaction that fits this
story's twist (e.g. "utter shock and disbelief", "gasping in amazement", "wide-eyed fear", "stunned
betrayal"). This will be shown on a generic anonymous face reacting to thumbnail.key_object, MrBeast-style —
pick the emotion that best matches the story's actual twist, vary it across episodes.
thumbnail.headline_text: a short, bold headline for the thumbnail (under 40 characters). Unlike the video
title (which must stay search-friendly and name the concrete topic), the thumbnail headline can take more
risk — consider making it feel like it's about the VIEWER directly rather than the topic (e.g. "YOU'VE
BEEN FOOLED BY THIS" or "YOU'D HAVE BELIEVED IT TOO" instead of naming the story), when that framing fits
naturally.

CRITICAL: Output must be STRICTLY VALID JSON. Escape internal double quotes as \\". No trailing commas.

Output EXACTLY this schema:
{{
  "video_meta": {{"title": "...", "description": "...", "tags": ["...", "..."]}},
  "thumbnail": {{"key_object": "...", "reaction": "...", "headline_text": "..."}},
  "long_scenes": [{{"image_prompt": "...", "narration": "...", "tone_prompt": "..."}}]
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

def build_long_video(scenes: list, meta: dict, thumb_cfg: dict, topic: str) -> tuple:
    zoom_rate = 0.0004 + (_stable_hash(topic) % 5) * 0.0001  # 0.0004-0.0008 arasi, konuya gore sabit ama degisken
    speaking_rate = 0.94 + (hash(topic + "rate") % 7) * 0.01  # 0.94-1.00 arasi
    pitch = -1.5 + (hash(topic + "pitch") % 7) * 0.5  # -1.5 ile +1.5 arasi
    clip_paths = []
    for i, scene in enumerate(scenes, start=1):
        narration = scene.get("narration", "").strip()
        if not narration:
            print(f"[video {i}] UYARI: narration boş, atlanıyor.")
            continue
        img_path = os.path.join(IMG_DIR, f"scene_{i:03d}.jpg")
        try:
            generate_image(scene.get("image_prompt", ""), img_path, topic, aspect_ratio="16:9")
            audio_path = generate_audio(narration, i, prefix="video", speaking_rate=speaking_rate, pitch=pitch,
                                         tone_prompt=scene.get("tone_prompt", ""))
            clip_path, _ = make_scene_clip(img_path, audio_path, i, subtitle_text=narration,
                                            flash_caption=(i == 1), vertical=False, prefix="clip",
                                            zoom_rate=zoom_rate)
            clip_paths.append(clip_path)
        except Exception as e:
            print(f"[video {i}] SAHNE ATLANDI: {e}")
            continue

    if not clip_paths:
        raise RuntimeError("Uzun video için hiçbir sahne üretilemedi.")

    final_video = concat_clips(clip_paths, "final_video.mp4")
    thumb_path = None
    if thumb_cfg.get("key_object"):
        thumb_path = generate_thumbnail(
            thumb_cfg["key_object"],
            thumb_cfg.get("reaction", "utter shock and disbelief"),
            thumb_cfg.get("headline_text", meta.get("title", "")),
            topic,
        )
    return final_video, thumb_path


def build_short_video(scenes: list, meta: dict, topic: str) -> str:
    zoom_rate = 0.0004 + (_stable_hash(topic) % 5) * 0.0001
    speaking_rate = 0.94 + (hash(topic + "rate") % 7) * 0.01
    pitch = -1.5 + (hash(topic + "pitch") % 7) * 0.5
    clip_paths = []
    for i, scene in enumerate(scenes, start=1):
        narration = scene.get("narration", "").strip()
        if not narration:
            continue
        img_path = os.path.join(IMG_DIR, f"short_{i:03d}.jpg")
        try:
            generate_image(scene.get("image_prompt", ""), img_path, topic, aspect_ratio="9:16")
            audio_path = generate_audio(narration, i, prefix="short", speaking_rate=speaking_rate, pitch=pitch,
                                         tone_prompt=scene.get("tone_prompt", ""))
            clip_path, _ = make_scene_clip(img_path, audio_path, i, subtitle_text=narration,
                                            flash_caption=(i == 1), vertical=True, prefix="shortclip",
                                            zoom_rate=zoom_rate)
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
    topic = config["topic"]

    package = expand_topic_to_package(topic)
    with open(os.path.join(OUTPUT_DIR, "expanded_package.json"), "w", encoding="utf-8") as f:
        json.dump(package, f, ensure_ascii=False, indent=2)

    meta = package.get("video_meta", {})
    thumb_cfg = package.get("thumbnail", {})

    print(f"\n=== UZUN OZEL BOLUM ({len(package.get('long_scenes', []))} sahne, hedef 10-12 dk) ===")
    final_video, thumb_path = build_long_video(package.get("long_scenes", []), meta, thumb_cfg, topic)
    print(f"Uzun özel bölüm hazır: {final_video}")
    upload_to_youtube(final_video, thumb_path, meta)

    print("\nYüklendi:", meta.get("title", ""))


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python generate_long_video.py queue/long_topics/XXX_konu.json")
        sys.exit(1)
    process_topic(sys.argv[1])


if __name__ == "__main__":
    main()
