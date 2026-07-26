"""
Otomatik video üretim scripti
Sahne sahne: Pollinations.ai'dan görsel -> Google TTS'ten ses -> ffmpeg ile Ken Burns efektli video

Kullanım:
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

# --- Ayarlar (ortam değişkenlerinden / GitHub Secrets'tan okunuyor) ---
POLLINATIONS_KEY = os.environ.get("POLLINATIONS_API_KEY", "")
GOOGLE_TTS_KEY = os.environ.get("GOOGLE_TTS_API_KEY", "")

OUTPUT_DIR = "output"
IMG_DIR = os.path.join(OUTPUT_DIR, "images")
AUDIO_DIR = os.path.join(OUTPUT_DIR, "audio")
CLIP_DIR = os.path.join(OUTPUT_DIR, "clips")

for d in (IMG_DIR, AUDIO_DIR, CLIP_DIR):
    os.makedirs(d, exist_ok=True)


def generate_image(prompt: str, index: int, seed: int = 42, reference_image: str = None) -> str:
    """Pollinations.ai'dan görsel indirir, dosya yolunu döner."""
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://gen.pollinations.ai/image/{encoded_prompt}?model=flux&width=1024&height=1024&seed={seed}"
    if reference_image:
        url += f"&image={urllib.parse.quote(reference_image)}"
    if POLLINATIONS_KEY:
        url += f"&key={urllib.parse.quote(POLLINATIONS_KEY)}"

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

    out_path = os.path.join(IMG_DIR, f"scene_{index:03d}.jpg")
    print(f"[{index}] Görsel isteniyor: {prompt[:60]}...")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"[{index}] HATA {e.code}: {body}")
        raise
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def generate_audio(text: str, index: int, voice_name: str = "tr-TR-Wavenet-D") -> str:
    """Google Cloud Text-to-Speech ile seslendirme üretir, mp3 dosya yolu döner."""
    if not GOOGLE_TTS_KEY:
        raise RuntimeError("GOOGLE_TTS_API_KEY tanımlı değil.")

    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_KEY}"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": "tr-TR", "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3"},
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
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def make_scene_clip(image_path: str, audio_path: str, index: int) -> str:
    """Sabit görsele Ken Burns (yavaş yakınlaşma) efekti uygulayıp sesle birleştirir."""
    duration = get_audio_duration(audio_path)
    fps = 30
    total_frames = int(duration * fps)

    out_path = os.path.join(CLIP_DIR, f"clip_{index:03d}.mp4")

    # Ken Burns: yavaşça zoom-in, zoompan filtresi ile
    vf = (
        f"scale=2000:2000,"
        f"zoompan=z='min(zoom+0.0007,1.3)':d={total_frames}:s=1280x1280:fps={fps}"
    )

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


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python generate_video.py scenes.json")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        scenes = json.load(f)

    clip_paths = []
    prev_image = None
    for i, scene in enumerate(scenes, start=1):
        image_path = generate_image(scene["image_prompt"], i, reference_image=prev_image)
        prev_image = None
        audio_path = generate_audio(scene["narration"], i)
        clip_path = make_scene_clip(image_path, audio_path, i)
        clip_paths.append(clip_path)

    final = concat_clips(clip_paths)
    print(f"\nBitti! Video hazır: {final}")


if __name__ == "__main__":
    main()
