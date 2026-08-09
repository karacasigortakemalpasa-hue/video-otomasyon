"""
THE GOSSIP MACHINE - bagimsiz, tek seferlik gorsel + montaj script'i.
Bu dosya mevcut otomasyon sistemine (generate_video.py, generate.yml,
generate_shorts.yml) HIC dokunmaz, tamamen ayri calisir.

Yapar: her sahne icin gorsel uretir (Vertex AI, iki farkli karakter referansiyla),
sabit sureli Ken Burns kliplere cevirir, hepsini ffmpeg ile birlestirir.
Yapmaz: seslendirme (TTS), YouTube yukleme - bunlar kullanicinin kendi elleriyle
sonradan (Pictory/ElevenLabs vb.) eklenecek.

Kullanim:
    python generate_oneoff_gossip.py
"""

import os
import sys
import json
import base64
import subprocess
import time
import urllib.request
import urllib.error

GCP_SERVICE_ACCOUNT_JSON = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "rosy-embassy-473607-a3")
GCP_REGION = "global"

MODERN_REFERENCE_URL = "https://raw.githubusercontent.com/karacasigortakemalpasa-hue/video-otomasyon/main/modern_blogger_ref.jpg"
WINCHELL_REFERENCE_URL = "https://raw.githubusercontent.com/karacasigortakemalpasa-hue/video-otomasyon/main/winchell_ref.jpg"

STYLE_GUIDE = (
    "Two-color risograph print illustration, deep plum violet ink and antique brass gold ink ONLY. "
    "Coarse halftone dot grain. Deliberate ink misregistration. Heavy paper texture. Imperfect "
    "hand-inked outlines with breaks and bleeds. Sparse composition. Flat 2D print aesthetic, no 3D, "
    "no photorealism, no digital polish. Vintage poster aesthetic."
)

OUTPUT_DIR = "output_gossip"
IMG_DIR = os.path.join(OUTPUT_DIR, "images")
CLIP_DIR = os.path.join(OUTPUT_DIR, "clips")
for d in (IMG_DIR, CLIP_DIR):
    os.makedirs(d, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

_token_cache = {"token": None}


def get_vertex_access_token() -> str:
    if _token_cache["token"]:
        return _token_cache["token"]
    if not GCP_SERVICE_ACCOUNT_JSON:
        raise RuntimeError("GCP_SERVICE_ACCOUNT_JSON tanımlı değil.")
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    info = json.loads(GCP_SERVICE_ACCOUNT_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    _token_cache["token"] = credentials.token
    return credentials.token


def generate_image(prompt: str, out_path: str, reference_image_url: str = None, aspect_ratio: str = "16:9"):
    """Vertex AI (Nano Banana 2 Lite) ile gorsel uretir, isteğe bağlı tek referans görselle."""
    token = get_vertex_access_token()
    host = "aiplatform.googleapis.com" if GCP_REGION == "global" else f"{GCP_REGION}-aiplatform.googleapis.com"
    url = (
        f"https://{host}/v1/projects/{GCP_PROJECT_ID}"
        f"/locations/{GCP_REGION}/publishers/google/models/gemini-3.1-flash-lite-image:generateContent"
    )

    parts = []
    if reference_image_url:
        req_ref = urllib.request.Request(reference_image_url)
        req_ref.add_header("User-Agent", USER_AGENT)
        with urllib.request.urlopen(req_ref, timeout=60) as resp:
            ref_bytes = resp.read()
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(ref_bytes).decode("utf-8")}})
    parts.append({"text": prompt})

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"imageConfig": {"aspectRatio": aspect_ratio}},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})

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
            print(f"  HATA {e.code}: {body}")
            raise

    parts_out = result["candidates"][0]["content"]["parts"]
    image_b64 = None
    for part in parts_out:
        if "inlineData" in part:
            image_b64 = part["inlineData"]["data"]
            break
    if not image_b64:
        raise RuntimeError(f"Görsel bulunamadı: {result}")
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(image_b64))


def make_silent_clip(image_path: str, out_path: str, duration: float = 4.0):
    """Gorsele yavas Ken Burns (zoom/pan) uygulayip sessiz bir klip yapar."""
    fps = 30
    total_frames = int(duration * fps)
    vf = (
        "scale=2400:1350:force_original_aspect_ratio=increase,"
        "crop=2400:1350,"
        f"zoompan=z='min(zoom+0.0004,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1920x1080:fps={fps}"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", image_path,
        "-vf", vf, "-t", str(duration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=90)


def concat_clips(clip_paths: list, final_name: str = "final_gossip_video.mp4") -> str:
    list_file = os.path.join(OUTPUT_DIR, "concat_list.txt")
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    final_path = os.path.join(OUTPUT_DIR, final_name)
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", final_path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    return final_path


def main():
    scenes_file = sys.argv[1] if len(sys.argv) > 1 else "gossip_scenes_all96.json"
    with open(scenes_file, "r", encoding="utf-8") as f:
        scenes = json.load(f)

    print(f"Toplam {len(scenes)} sahne işlenecek.\n")

    clip_paths = []
    for scene in scenes:
        n = scene["scene_number"]
        character = scene.get("character")
        img_prompt = scene["image_prompt"]
        img_path = os.path.join(IMG_DIR, f"scene_{n:03d}.jpg")
        clip_path = os.path.join(CLIP_DIR, f"clip_{n:03d}.mp4")

        ref_url = None
        if character == "MODERN":
            ref_url = MODERN_REFERENCE_URL
        elif character == "WINCHELL":
            ref_url = WINCHELL_REFERENCE_URL

        full_prompt = f"{STYLE_GUIDE} {img_prompt}"

        print(f"[Sahne {n}] ({character or 'karaktersiz'}) görsel üretiliyor...")
        try:
            generate_image(full_prompt, img_path, reference_image_url=ref_url)
            time.sleep(8)  # dakikalik hiz limitine takilmamak icin
            make_silent_clip(img_path, clip_path, duration=4.0)
            clip_paths.append(clip_path)
        except Exception as e:
            print(f"[Sahne {n}] ATLANDI (başarısız): {e}")
            continue

    if not clip_paths:
        raise RuntimeError("Hiçbir sahne üretilemedi.")

    print(f"\n{len(clip_paths)} sahne birleştiriliyor...")
    final_video = concat_clips(clip_paths)
    print(f"\nBitti! Video (sessiz): {final_video}")
    print("Seslendirme ve YouTube yükleme adımlarını kendin (manuel) tamamlayacaksın.")


if __name__ == "__main__":
    main()
