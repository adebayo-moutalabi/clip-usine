#!/usr/bin/env python3
"""Clip Usine — transforme un clip/VOD en short vertical 9:16 sous-titré.

Tourne dans GitHub Actions (aucune installation sur ton PC).
Étapes : téléchargement -> (détection auto des temps forts) -> recadrage 9:16
-> sous-titres mot à mot -> titre + mention pub -> MP4 + fiche de publication.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

W, H, FPS = 1080, 1920, 30
MAX_CLIP = 90  # secondes, garde-fou


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def to_seconds(t):
    """'1:02:03', '2:05', '125' -> secondes."""
    if t is None or str(t).strip() == "":
        return None
    parts = [float(p) for p in str(t).strip().split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def hms(s):
    s = max(0.0, float(s))
    return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}"


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True, capture_output=True, text=True).stdout
    info = json.loads(out)
    dur = float(info["format"]["duration"])
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    return {"duration": dur, "width": int(v["width"]) if v else 0,
            "height": int(v["height"]) if v else 0, "has_audio": a is not None}


# ---------------------------------------------------------------- téléchargement
def download(url, dest, start=None, end=None, audio_only=False):
    """Télécharge une source (clip Twitch/Kick, VOD, lien direct, Google Drive)."""
    if os.path.exists(url):  # fichier local (tests)
        return Path(url)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if url == "test":  # vidéo de démonstration générée sur place, sans téléchargement
        out = Path(str(dest) + ".mp4")
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=20",
             "-f", "lavfi", "-i", "sine=f=440:d=20", "-c:v", "libx264", "-preset", "ultrafast",
             "-c:a", "aac", str(out)])
        return out
    cmd = ["yt-dlp", "--no-playlist", "--no-progress", "-o", str(dest) + ".%(ext)s"]
    if audio_only:
        cmd += ["-f", "bestaudio/worst", "-x", "--audio-format", "m4a"]
    else:
        cmd += ["-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4"]
    if start is not None and end is not None:
        cmd += ["--download-sections", f"*{hms(start)}-{hms(end)}", "--force-keyframes-at-cuts"]
    cmd.append(url)
    run(cmd)
    files = sorted(dest.parent.glob(dest.name + ".*"))
    if not files:
        sys.exit(f"Téléchargement impossible : {url}")
    return files[0]


# ---------------------------------------------------------------- temps forts
def loudness_per_second(path):
    """Énergie audio par seconde (mono 8 kHz), sans dépendance lourde."""
    import numpy as np
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        check=True, capture_output=True)
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)
    n = len(pcm) // 8000
    if n == 0:
        return np.zeros(0)
    frames = pcm[: n * 8000].reshape(n, 8000)
    return np.sqrt((frames ** 2).mean(axis=1))


def find_highlights(path, count=5, length=40, lead=12):
    """Repère les `count` moments les plus bruyants (rires, cris, action).

    Chaque fenêtre commence `lead` secondes AVANT le pic : on garde la montée
    et la chute, pas seulement le cri.
    """
    import numpy as np
    rms = loudness_per_second(path)
    n = len(rms)
    if n <= length:
        return [(0.0, float(n))]
    base = np.median(rms) + 1e-6
    # score = pic court (5 s) rapporté au niveau de fond
    kernel = np.ones(5) / 5
    score = np.convolve(rms, kernel, mode="same") / base
    picks = []
    order = np.argsort(score)[::-1]
    for idx in order:
        if len(picks) >= count:
            break
        start = max(0, min(int(idx) - lead, n - length))
        if all(abs(start - p) >= length * 1.5 for p in picks):
            picks.append(start)
    return [(float(s), float(s + length)) for s in sorted(picks)]


# ---------------------------------------------------------------- sous-titres
def transcribe(path, lang="fr", model_size="small"):
    """Retourne (mots [(debut, fin, texte)], texte complet). Vide si pas de voix."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper absent : pas de sous-titres.")
        return [], ""
    import numpy as np
    # on décode l'audio nous-mêmes : évite les incompatibilités entre faster-whisper et PyAV
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         check=True, capture_output=True).stdout
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio, language=lang or None, word_timestamps=True, vad_filter=True)
    words, text = [], []
    for seg in segments:
        text.append(seg.text.strip())
        for w in seg.words or []:
            tok = w.word.strip()
            if tok:
                words.append((float(w.start), float(w.end), tok))
    return words, " ".join(text)


def group_words(words, max_words=3, max_chars=18, max_gap=0.6):
    """Regroupe les mots en petits blocs lisibles (style TikTok)."""
    groups, cur = [], []
    for w in words:
        if cur:
            chars = sum(len(x[2]) for x in cur) + len(cur) + len(w[2])
            if len(cur) >= max_words or chars > max_chars or w[0] - cur[-1][1] > max_gap \
                    or cur[-1][2][-1:] in ".?!":
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    out = []
    for i, g in enumerate(groups):
        start, end = g[0][0], g[-1][1]
        if i + 1 < len(groups):  # pas de chevauchement, pas de trou visible
            end = min(max(end, start + 0.35), groups[i + 1][0][0])
        out.append((start, max(end, start + 0.2), " ".join(x[2] for x in g)))
    return out


def ass_time(s):
    s = max(0.0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def ass_escape(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap(text, width):
    """Coupe un titre en lignes de `width` caractères max."""
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines


def build_ass(path, duration, groups, title="", mention="", font="DejaVu Sans", title_y=170):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,520,1
Style: Titre,{font},68,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,-1,0,0,0,100,100,0,0,3,14,0,8,70,70,{title_y},1
Style: Mention,{font},38,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,-1,0,0,0,100,100,0,0,3,8,0,7,40,40,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if title:
        txt = "\\N".join(ass_escape(l) for l in wrap(title, 24)[:3])
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(duration)},Titre,,0,0,0,,{txt}")
    if mention:
        ev.append(f"Dialogue: 2,{ass_time(0)},{ass_time(min(duration, 4))},Mention,,0,0,0,,{ass_escape(mention)}")
    for start, end, text in groups:
        if start >= duration:
            continue
        # petit effet "pop" à l'apparition
        ev.append(f"Dialogue: 0,{ass_time(start)},{ass_time(min(end, duration))},Sub,,0,0,0,,"
                  f"{{\\fscx88\\fscy88\\t(0,90,\\fscx100\\fscy100)}}{ass_escape(text).upper()}")
    Path(path).write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


def write_srt(path, groups):
    def t(s):
        return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{int(s % 60):02d},{int(s * 1000 % 1000):03d}"
    lines = []
    for i, (a, b, txt) in enumerate(groups, 1):
        lines += [str(i), f"{t(a)} --> {t(b)}", txt, ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- rendu vidéo
CAM_PRESETS = {  # coin de la facecam : x, y (fractions de la source)
    "hg": (0.0, 0.0), "hd": (1.0, 0.0), "bg": (0.0, 1.0), "bd": (1.0, 1.0),
}


def video_filter(layout, cam, cam_size, ass_path):
    """Construit le filter_complex ffmpeg pour une sortie 1080x1920."""
    ass = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    if layout == "zoom":  # plein écran, recadrage central
        chain = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[base]"
    elif layout == "cam" and cam in CAM_PRESETS:  # facecam en haut, jeu en bas
        fx, fy = CAM_PRESETS[cam]
        cs = max(0.1, min(0.6, cam_size))
        top_h = 720
        chain = (
            f"[0:v]split=2[a][b];"
            f"[a]crop=iw*{cs}:ih*{cs}:(iw-iw*{cs})*{fx}:(ih-ih*{cs})*{fy},"
            f"scale={W}:{top_h}:force_original_aspect_ratio=increase,crop={W}:{top_h}[top];"
            f"[b]scale={W}:{H - top_h}:force_original_aspect_ratio=increase,crop={W}:{H - top_h}[bot];"
            f"[top][bot]vstack=inputs=2[base]"
        )
    else:  # "flou" : vidéo entière nette au centre, fond flouté
        chain = (
            f"[0:v]split=2[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=24:4,eq=brightness=-0.12[bg];"
            f"[b]scale={W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
        )
    return chain + f";[base]fps={FPS},ass='{ass}',format=yuv420p[v]"


def render(src, out, start, duration, layout, cam, cam_size, ass_path, has_audio=True):
    cmd = ["ffmpeg", "-y", "-v", "error", "-stats", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-filter_complex", video_filter(layout, cam, cam_size, ass_path), "-map", "[v]",
            "-map", "0:a:0" if has_audio else "1:a:0"]
    if has_audio:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
            "-ar", "48000", "-movflags", "+faststart", "-shortest", str(out)]
    run(cmd)


# ---------------------------------------------------------------- fiche
FICHE = """# Fiche de publication — {name}

- Source : {url}
- Extrait : {start} -> {end} ({dur:.0f} s) · mise en page : {layout}
- Titre incrusté : {title}

## Avant de poster (30 secondes)
- [ ] J'ai le droit d'utiliser cette vidéo (brief de campagne qui la cite, ou accord écrit du streamer)
- [ ] Aucun spoiler : pas de cinématique isolée, pas de fin, pas de grosse révélation
- [ ] Mention de partenariat visible au début si le clip est rémunéré (case « contenu de marque » cochée dans l'appli)
- [ ] Pas de filigrane d'un autre outil, pas la même vidéo envoyée à deux campagnes
- [ ] Lien soumis à la campagne dans les 30 minutes après publication

## Prompt à coller dans Claude (accroche + légende)
```
Tu es un monteur de shorts gaming francophone. Voici la transcription d'un clip
de {dur:.0f} secondes tiré d'un live GTA 6. Donne-moi :
1. 5 titres à incruster (6 mots max, sans spoiler, sans putaclic mensonger)
2. 3 légendes TikTok/Reels (1 phrase + question pour les commentaires)
3. 8 hashtags pertinents en français et en anglais
4. Le meilleur moment pour couper le début si les 2 premières secondes sont molles
Transcription :
{transcript}
```
"""


def process(url, start, end, idx, args, outdir, work):
    name = f"clip_{idx:02d}"
    src = download(url, work / name, start, end)
    info = probe(src)
    # si on a téléchargé une section, la source commence à 0
    local = os.path.exists(url)
    s = (start or 0.0) if local else 0.0
    dur = (end - start) if (start is not None and end is not None) else info["duration"] - s
    dur = max(1.0, min(dur, info["duration"] - s, args.max))

    cut = work / f"{name}_cut.wav"
    words, transcript = [], ""
    if args.subs and info["has_audio"]:
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
             "-vn", "-ac", "1", "-ar", "16000", str(cut)])
        try:
            words, transcript = transcribe(cut, args.lang, args.model)
        except Exception as e:  # un souci de sous-titres ne doit pas faire perdre le clip
            print(f"Sous-titres indisponibles pour ce clip : {e}", file=sys.stderr)
    groups = group_words(words)
    ass = work / f"{name}.ass"
    build_ass(ass, dur, groups, args.title, args.mention,
              title_y=650 if args.layout == "cam" else 170)
    out = outdir / f"{name}.mp4"
    render(src, out, s, dur, args.layout, args.cam, args.cam_size, ass, info["has_audio"])
    if groups:
        write_srt(outdir / f"{name}.srt", groups)
    (outdir / f"{name}_fiche.md").write_text(FICHE.format(
        name=name, url=url, start=hms(start or 0), end=hms((start or 0) + dur), dur=dur,
        layout=args.layout, title=args.title or "(aucun)",
        transcript=transcript or "(pas de voix détectée)"), encoding="utf-8")
    print(f"OK {out} ({dur:.0f} s, {len(groups)} sous-titres)")
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True, help="une ou plusieurs URL (séparées par espace ou retour ligne)")
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--auto", type=int, default=0, help="VOD longue : nombre de temps forts à extraire")
    p.add_argument("--length", type=int, default=40, help="durée des clips en mode auto (s)")
    p.add_argument("--layout", default="flou", choices=["flou", "zoom", "cam"])
    p.add_argument("--cam", default="hg", choices=list(CAM_PRESETS))
    p.add_argument("--cam-size", type=float, default=0.28)
    p.add_argument("--title", default="")
    p.add_argument("--mention", default="")
    p.add_argument("--subs", default="oui")
    p.add_argument("--lang", default="fr")
    p.add_argument("--model", default="small")
    p.add_argument("--max", type=float, default=60)
    p.add_argument("--out", default="out")
    args = p.parse_args()
    args.subs = str(args.subs).lower() in ("oui", "true", "1", "yes")
    args.max = min(args.max, MAX_CLIP)
    args.length = int(min(args.length, args.max))

    outdir, work = Path(args.out), Path("work")
    outdir.mkdir(parents=True, exist_ok=True)
    work.mkdir(exist_ok=True)
    urls = [u for u in re.split(r"[\s,]+", args.url.strip()) if u]
    start, end = to_seconds(args.start), to_seconds(args.end)
    if start is not None and end is None:
        end = start + args.max
    idx, failed = 0, []
    for url in urls:
        try:
            if args.auto > 0:
                audio = download(url, work / f"audio_{idx}", audio_only=True)
                spans = find_highlights(audio, args.auto, args.length)
                print("Temps forts :", [(hms(a), hms(b)) for a, b in spans])
                for a, b in spans:
                    idx += 1
                    process(url, a, b, idx, args, outdir, work)
            else:
                idx += 1
                process(url, start, end, idx, args, outdir, work)
        except subprocess.CalledProcessError as e:
            failed.append(url)
            print(f"ECHEC {url} : {e}", file=sys.stderr)
    shutil.rmtree(work, ignore_errors=True)
    if failed and not list(outdir.glob("*.mp4")):
        sys.exit("Aucun clip produit.")


if __name__ == "__main__":
    main()
