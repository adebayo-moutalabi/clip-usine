#!/usr/bin/env python3
"""Clip Usine — transforme un clip ou une VOD en short vertical 9:16 monté.

Tourne dans GitHub Actions (rien à installer sur ton PC).

Chaîne de fabrication :
  1. téléchargement de la source (ou vidéo de test)
  2. repérage des temps forts au son (mode auto / montage)
  3. passe 1 : recadrage 9:16, zooms sur les pics sonores, fondus entre extraits
  4. sous-titres mot à mot (mot en cours surligné)
  5. passe 2 : titre d'accroche, barre de progression, fondus, niveau sonore
  6. fiche de publication avec prompt Claude
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
MAX_CLIP = 90          # secondes, garde-fou
XFADE = 0.35           # durée des fondus entre extraits d'un montage
ACCENT_ASS = "&H20B0FF&"   # jaune-orangé (ASS s'écrit en BGR)
ACCENT_HEX = "0xFFB020"
TRANSITIONS = ["fade", "smoothleft", "smoothup", "circleopen", "smoothright"]


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd)[:900], flush=True)
    return subprocess.run(cmd, check=True, **kw)


def to_seconds(t):
    """'1:02:03', '2:05', '125' -> secondes."""
    if t is None or str(t).strip() == "":
        return None
    s = 0.0
    for p in str(t).strip().split(":"):
        s = s * 60 + float(p)
    return s


def hms(s):
    s = max(0.0, float(s))
    return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}"


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True, capture_output=True, text=True).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    return {"duration": float(info["format"]["duration"]), "width": int(v["width"]) if v else 0,
            "height": int(v["height"]) if v else 0, "has_audio": a is not None}


# ---------------------------------------------------------------- sources
def make_test_video(out):
    """Vidéo de démonstration de 30 s : mire animée, voix de synthèse, deux pics sonores."""
    out = Path(out)
    if out.exists():
        return out
    speech = out.with_suffix(".speech.wav")
    text = ("Attends, regarde bien ce qui va se passer. Il prend la voiture, il fonce vers le pont. "
            "Non, non, non, il ne va quand même pas sauter ! Incroyable, il l'a fait. "
            "Franchement, c'est le meilleur moment du live. Maintenant il doit semer la police.")
    has_voice = False
    if shutil.which("espeak-ng"):
        try:
            run(["espeak-ng", "-v", "fr", "-s", "165", "-w", str(speech), text])
            has_voice = True
        except subprocess.CalledProcessError:
            pass
    bursts = "volume='if(between(t,9,10.2)+between(t,21,22),0.9,0)':eval=frame"
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=30",
           "-f", "lavfi", "-i", "anoisesrc=d=30:c=pink:a=0.6"]
    if has_voice:
        cmd += ["-stream_loop", "-1", "-i", str(speech),
                "-filter_complex", f"[1:a]{bursts}[n];[2:a]volume=1.0[s];[n][s]amix=inputs=2:normalize=0[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-filter_complex", f"[1:a]{bursts}[a]", "-map", "0:v", "-map", "[a]"]
    cmd += ["-t", "30", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out)]
    run(cmd)
    return out


def is_local(url):
    return url == "test" or os.path.exists(url)


def download(url, dest, start=None, end=None, audio_only=False):
    """Télécharge une source (clip Twitch/Kick, VOD, lien direct, Google Drive)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if url == "test":
        return make_test_video(dest.parent / "test_source.mp4")
    if os.path.exists(url):
        return Path(url)
    cmd = ["yt-dlp", "--no-playlist", "--no-progress", "-o", str(dest) + ".%(ext)s"]
    if audio_only:
        cmd += ["-f", "bestaudio/worst", "-x", "--audio-format", "m4a"]
    else:
        cmd += ["-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4"]
    if start is not None and end is not None:
        cmd += ["--download-sections", f"*{hms(start)}-{hms(end)}", "--force-keyframes-at-cuts"]
    cmd.append(url)
    print("+", " ".join(cmd)[:600], flush=True)
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(res.stdout[-2000:], res.stderr[-3000:], sep="\n", flush=True)
    files = sorted(dest.parent.glob(dest.name + ".*"))
    if res.returncode != 0 or not files:
        raise SourceError(explain(res.stderr, url))
    return files[0]


class SourceError(Exception):
    """Erreur de source, avec un message lisible par un humain."""


def explain(stderr, url):
    e = (stderr or "").lower()
    if "youtube.com" in url or "youtu.be" in url:
        if "not a bot" in e or "sign in" in e:
            return ("YouTube bloque les serveurs de fabrication (contrôle anti-robot). "
                    "Mets la vidéo sur Google Drive en partage « Tous les utilisateurs disposant du lien », "
                    "puis colle le lien Drive.")
    if "private" in e or "privée" in e:
        return "La vidéo est privée : rends-la publique ou partage-la par lien."
    if "unavailable" in e or "not available" in e or "404" in e:
        return "La vidéo est introuvable ou supprimée. Vérifie le lien."
    if "drive.google" in url:
        return ("Google Drive refuse l'accès. Dans Drive : Partager, puis "
                "« Tous les utilisateurs disposant du lien ».")
    if "unsupported url" in e:
        return "Ce site n'est pas pris en charge. Essaie un lien Twitch, Kick, Google Drive ou un lien direct vers un .mp4."
    return "Téléchargement impossible. Vérifie que le lien s'ouvre bien dans un navigateur sans être connecté."


# ---------------------------------------------------------------- analyse du son
def loudness(path, step=1.0, start=None, dur=None):
    """Énergie audio par tranche de `step` secondes (mono 8 kHz)."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    pcm = np.frombuffer(subprocess.run(cmd, check=True, capture_output=True).stdout,
                        dtype=np.int16).astype(np.float32)
    size = int(8000 * step)
    n = len(pcm) // size
    if n == 0:
        return np.zeros(0)
    return np.sqrt((pcm[: n * size].reshape(n, size) ** 2).mean(axis=1))


def find_highlights(path, count=5, length=35, lead=None):
    """Les `count` fenêtres les plus intenses. Chaque fenêtre démarre avant le pic
    pour garder la montée, pas seulement le cri."""
    import numpy as np
    lead = lead if lead is not None else max(2, int(length * 0.35))
    rms = loudness(path)
    n = len(rms)
    if n <= length:
        return [(0.0, float(n))]
    base = np.median(rms) + 1e-6
    score = np.convolve(rms, np.ones(5) / 5, mode="same") / base
    picks = []
    for idx in np.argsort(score)[::-1]:
        if len(picks) >= count:
            break
        start = max(0, min(int(idx) - lead, n - length))
        if all(abs(start - p) >= length * 1.3 for p in picks):
            picks.append(start)
    return [(float(s), float(s + length)) for s in sorted(picks)]


def find_peaks(path, start, dur, maxi=5):
    """Instants (relatifs au début de l'extrait) où le son explose : on y place les zooms."""
    import numpy as np
    step = 0.25
    rms = loudness(path, step, start, dur)
    if len(rms) < 8:
        return []
    base = np.median(rms) + 1e-6
    score = rms / base
    peaks = []
    for idx in np.argsort(score)[::-1]:
        t = float(idx) * step
        if score[idx] < 1.8 or len(peaks) >= maxi:
            break
        if 0.8 < t < dur - 0.8 and all(abs(t - p) >= 3.0 for p in peaks):
            peaks.append(round(t, 2))
    return sorted(peaks)


# ---------------------------------------------------------------- passe 1 : image
CAM_PRESETS = {"hg": (0.0, 0.0), "hd": (1.0, 0.0), "bg": (0.0, 1.0), "bd": (1.0, 1.0)}


def layout_chain(layout, cam_size=0.28):
    """Filtre ffmpeg qui produit [base] en 1080x1920 à partir de [0:v]."""
    if layout == "zoom":
        return f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[base]"
    if layout.startswith("cam-") and layout[4:] in CAM_PRESETS:
        fx, fy = CAM_PRESETS[layout[4:]]
        cs, top = max(0.1, min(0.6, cam_size)), 720
        return (f"[0:v]split=2[a][b];"
                f"[a]crop=iw*{cs}:ih*{cs}:(iw-iw*{cs})*{fx}:(ih-ih*{cs})*{fy},"
                f"scale={W}:{top}:force_original_aspect_ratio=increase,crop={W}:{top}[top];"
                f"[b]scale={W}:{H - top}:force_original_aspect_ratio=increase,crop={W}:{H - top}[bot];"
                f"[top][bot]vstack=inputs=2[base]")
    return (f"[0:v]split=2[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=24:4,eq=brightness=-0.12[bg];"
            f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")


def zoom_expr(peaks, dur):
    """Facteur de zoom en fonction du temps : lente avancée + coup de zoom à chaque pic."""
    parts = [f"1+0.04*t/{max(dur, 1):.2f}"]
    for p in peaks:
        # montée rapide, retombée douce (cloche étroite, sans virgule pour ffmpeg)
        parts.append(f"0.11*exp(-((t-{p})/0.28)*((t-{p})/0.28))")
    return "+".join(parts)


def make_segment(src, start, dur, out, layout, has_audio, peaks=None):
    """Passe 1 : un extrait normalisé (9:16, 30 i/s, stéréo 48 kHz), avec zooms si `peaks`."""
    chain = layout_chain(layout)
    if peaks is not None:
        z = zoom_expr(peaks, dur)
        chain += (f";[base]split=2[k][m];"
                  f"[m]scale=w='2*trunc({W // 2}*({z}))':h='2*trunc({H // 2}*({z}))':eval=frame[zz];"
                  f"[k][zz]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame[zo]")
        last = "zo"
    else:
        last = "base"
    chain += f";[{last}]fps={FPS},format=yuv420p,settb=AVTB[v]"
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-filter_complex", chain, "-map", "[v]", "-map", "0:a:0" if has_audio else "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2", "-shortest", str(out)]
    run(cmd)
    return out


def join_segments(segs, out):
    """Assemble plusieurs extraits avec un fondu différent entre chacun."""
    if len(segs) == 1:
        shutil.copy(segs[0], out)
        return out
    durs = [probe(s)["duration"] for s in segs]
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for s in segs:
        cmd += ["-i", str(s)]
    parts, vprev, aprev, offset = [], "0:v", "0:a", 0.0
    for i in range(1, len(segs)):
        offset += durs[i - 1] - XFADE
        tr = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
        parts.append(f"[{vprev}][{i}:v]xfade=transition={tr}:duration={XFADE}:offset={offset:.3f}[v{i}]")
        parts.append(f"[{aprev}][{i}:a]acrossfade=d={XFADE}[a{i}]")
        vprev, aprev = f"v{i}", f"a{i}"
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{vprev}]", "-map", f"[{aprev}]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(out)]
    run(cmd)
    return out


# ---------------------------------------------------------------- sous-titres
def transcribe(path, lang="fr", model_size="small"):
    """Retourne (mots [(debut, fin, texte)], texte complet)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper absent : pas de sous-titres.")
        return [], ""
    import numpy as np
    # on décode l'audio nous-mêmes : évite les incompatibilités entre faster-whisper et PyAV
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], check=True, capture_output=True).stdout
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
    """Regroupe les mots en petits blocs lisibles. Retourne une liste de listes de mots."""
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
    return groups


def group_spans(groups):
    """(début, fin, texte) par bloc, sans chevauchement."""
    out = []
    for i, g in enumerate(groups):
        start, end = g[0][0], g[-1][1]
        if i + 1 < len(groups):
            end = min(max(end, start + 0.35), groups[i + 1][0][0])
        out.append((start, max(end, start + 0.2), " ".join(x[2] for x in g)))
    return out


def ass_time(s):
    s = max(0.0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def ass_escape(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap(text, width):
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


def build_ass(path, duration, groups, title="", mention="", dynamic=True, title_y=170, font="DejaVu Sans"):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,8,3,2,60,60,520,1
Style: Titre,{font},64,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,-1,0,0,0,100,100,0,0,3,14,0,8,70,70,{title_y},1
Style: Accroche,{font},104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,10,4,5,60,60,0,1
Style: Mention,{font},38,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,-1,0,0,0,100,100,0,0,3,8,0,7,40,40,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if title:
        lines = [ass_escape(l) for l in wrap(title, 24)[:3]]
        hook = min(1.6, duration / 3) if dynamic else 0.0
        if hook:
            big = "\\N".join(ass_escape(l).upper() for l in wrap(title, 14)[:4])
            ev.append(f"Dialogue: 3,{ass_time(0)},{ass_time(hook)},Accroche,,0,0,0,,"
                      f"{{\\fad(0,180)\\fscx70\\fscy70\\t(0,160,\\fscx100\\fscy100)}}{big}")
        ev.append(f"Dialogue: 1,{ass_time(hook)},{ass_time(duration)},Titre,,0,0,0,,"
                  f"{{\\fad(200,0)}}" + "\\N".join(lines))
    if mention:
        ev.append(f"Dialogue: 2,{ass_time(0)},{ass_time(min(duration, 4))},Mention,,0,0,0,,{ass_escape(mention)}")
    spans = group_spans(groups)
    for g, (gs, ge, _) in zip(groups, spans):
        if gs >= duration:
            continue
        ge = min(ge, duration)
        if not dynamic:
            txt = ass_escape(" ".join(w[2] for w in g)).upper()
            ev.append(f"Dialogue: 0,{ass_time(gs)},{ass_time(ge)},Sub,,0,0,0,,{txt}")
            continue
        for i, w in enumerate(g):  # un événement par mot : le mot prononcé passe en couleur
            a = gs if i == 0 else w[0]
            b = ge if i == len(g) - 1 else g[i + 1][0]
            if b <= a:
                continue
            toks = []
            for j, x in enumerate(g):
                t = ass_escape(x[2]).upper()
                toks.append(f"{{\\c{ACCENT_ASS}\\fscx108\\fscy108}}{t}{{\\c&HFFFFFF&\\fscx100\\fscy100}}" if j == i else t)
            pop = "{\\fscx90\\fscy90\\t(0,80,\\fscx100\\fscy100)}" if i == 0 else ""
            ev.append(f"Dialogue: 0,{ass_time(a)},{ass_time(min(b, duration))},Sub,,0,0,0,,{pop}{' '.join(toks)}")
    Path(path).write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


def write_srt(path, spans):
    def t(s):
        return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{int(s % 60):02d},{int(s * 1000 % 1000):03d}"
    lines = []
    for i, (a, b, txt) in enumerate(spans, 1):
        lines += [str(i), f"{t(a)} --> {t(b)}", txt, ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- passe 2 : habillage
def finish(base, out, duration, ass_path, dynamic):
    """Incruste textes et barre de progression, fondus d'entrée/sortie, niveau sonore."""
    ass = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    fo = max(0.0, duration - 0.3)
    v = f"[0:v]ass='{ass}',fade=t=in:st=0:d=0.2,fade=t=out:st={fo:.2f}:d=0.3"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(base)]
    if dynamic:
        cmd += ["-f", "lavfi", "-i", f"color=c={ACCENT_HEX}:s={W}x10:r={FPS}:d={duration:.2f}"]
        v += f"[t];[t][1:v]overlay=x='-W+W*t/{duration:.2f}':y=H-h:shortest=1,format=yuv420p[v]"
    else:
        v += ",format=yuv420p[v]"
    a = f"[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=in:st=0:d=0.15,afade=t=out:st={fo:.2f}:d=0.3[a]"
    cmd += ["-filter_complex", v + ";" + a, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
            "-ar", "48000", "-movflags", "+faststart", str(out)]
    run(cmd)


FICHE = """# Fiche de publication — {name}

- Source : {url}
- Extraits : {spans}
- Durée : {dur:.0f} s · mise en page : {layout} · style : {style}
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
de {dur:.0f} secondes tiré d'un live. Donne-moi :
1. 5 titres à incruster (6 mots max, sans spoiler, sans promesse mensongère)
2. 3 légendes TikTok/Reels/Shorts (1 phrase + une question pour les commentaires)
3. 8 hashtags pertinents en français et en anglais
4. Un commentaire de 2 phrases que je peux dire en voix off pour apporter mon avis
Transcription :
{transcript}
```
"""


def build_clip(url, spans, idx, args, outdir, work):
    """Fabrique UN clip à partir d'un ou plusieurs extraits (start, end) de la source."""
    name = f"clip_{idx:02d}"
    dynamic = args.style == "dynamique"
    segs, used = [], []
    budget = args.max
    for k, (a, b) in enumerate(spans):
        if budget <= 1:
            break
        src = download(url, work / f"{name}_src{k}", a, b)
        info = probe(src)
        s = (a or 0.0) if is_local(url) else 0.0
        dur = (b - a) if (a is not None and b is not None) else info["duration"] - s
        dur = max(1.0, min(dur, info["duration"] - s, budget))
        budget -= dur
        peaks = find_peaks(src, s, dur) if (dynamic and info["has_audio"]) else None
        if dynamic and peaks is None:
            peaks = []
        print(f"Extrait {k + 1} : {hms(a or 0)} +{dur:.1f}s, zooms à {peaks}")
        segs.append(make_segment(src, s, dur, work / f"{name}_seg{k}.mp4", args.layout,
                                 info["has_audio"], peaks if dynamic else None))
        used.append(f"{hms(a or 0)} (+{dur:.0f} s)")
    base = join_segments(segs, work / f"{name}_base.mp4")
    duration = probe(base)["duration"]

    words, transcript = [], ""
    if args.style != "brut":
        try:
            words, transcript = transcribe(base, args.lang, args.model)
        except Exception as e:  # un souci de sous-titres ne doit pas faire perdre le clip
            print(f"Sous-titres indisponibles pour ce clip : {e}", file=sys.stderr)
    groups = group_words(words)
    ass = work / f"{name}.ass"
    build_ass(ass, duration, groups, args.title, args.mention, dynamic,
              title_y=650 if args.layout.startswith("cam-") else 170)
    out = outdir / f"{name}.mp4"
    finish(base, out, duration, ass, dynamic)
    if groups:
        write_srt(outdir / f"{name}.srt", group_spans(groups))
    (outdir / f"{name}_fiche.md").write_text(FICHE.format(
        name=name, url=url, spans=", ".join(used), dur=duration, layout=args.layout, style=args.style,
        title=args.title or "(aucun)", transcript=transcript or "(pas de voix détectée)"), encoding="utf-8")
    (outdir / f"{name}.json").write_text(json.dumps({
        "title": args.title, "source": url, "duration": round(duration, 1), "layout": args.layout,
        "style": args.style, "extraits": len(segs), "sous_titres": len(groups)}, ensure_ascii=False))
    print(f"OK {out} ({duration:.0f} s, {len(segs)} extrait(s), {len(groups)} sous-titres)")
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True, help="un ou plusieurs liens, ou 'test'")
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--auto", type=int, default=0, help="nombre de temps forts à trouver")
    p.add_argument("--montage", default="non", help="oui = réunir les temps forts dans UN clip")
    p.add_argument("--layout", default="flou", choices=["flou", "zoom"] + [f"cam-{c}" for c in CAM_PRESETS])
    p.add_argument("--style", default="dynamique", choices=["dynamique", "simple", "brut"])
    p.add_argument("--title", default="")
    p.add_argument("--mention", default="")
    p.add_argument("--lang", default="fr")
    p.add_argument("--model", default="small")
    p.add_argument("--max", type=float, default=60)
    p.add_argument("--out", default="out")
    args = p.parse_args()
    args.max = min(args.max, MAX_CLIP)
    montage = str(args.montage).lower() in ("oui", "true", "1", "yes")
    args.auto = max(0, min(args.auto, 8))
    if montage and args.auto < 2:
        args.auto = 4

    outdir, work = Path(args.out), Path("work")
    outdir.mkdir(parents=True, exist_ok=True)
    work.mkdir(exist_ok=True)
    urls = [u for u in re.split(r"[\s,]+", args.url.strip()) if u]
    start, end = to_seconds(args.start), to_seconds(args.end)
    if start is not None and end is None:
        end = start + args.max
    idx, failed = 0, []
    for n, url in enumerate(urls):
        try:
            if args.auto > 0:
                audio = download(url, work / f"audio_{n}", audio_only=True)
                length = 9 if montage else 35
                spans = find_highlights(audio, args.auto, length)
                print("Temps forts :", [(hms(a), hms(b)) for a, b in spans])
                if montage:
                    idx += 1
                    build_clip(url, spans, idx, args, outdir, work)
                else:
                    for span in spans:
                        idx += 1
                        build_clip(url, [span], idx, args, outdir, work)
            else:
                idx += 1
                build_clip(url, [(start, end)], idx, args, outdir, work)
        except SourceError as e:
            failed.append(str(e))
            print(f"ECHEC {url} : {e}", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            failed.append("Le montage a échoué sur cette vidéo (format inhabituel ou extrait vide). "
                          "Essaie un autre extrait ou le style « simple ».")
            print(f"ECHEC {url} : {e}", file=sys.stderr)
    shutil.rmtree(work, ignore_errors=True)
    if not list(outdir.glob("*.mp4")):
        msg = failed[0] if failed else "Aucun clip produit."
        (outdir / "erreur.json").write_text(json.dumps({"message": msg}, ensure_ascii=False))
        sys.exit(msg)


if __name__ == "__main__":
    main()
