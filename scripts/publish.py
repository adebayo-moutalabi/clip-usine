#!/usr/bin/env python3
"""Publie le site et les clips sur la branche gh-pages (lue par GitHub Pages).

La branche est reconstruite à chaque fois en un seul commit : le dépôt ne grossit pas.
On y garde le site (dossier site/), les KEEP derniers clips et les données du radar.
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

KEEP = int(os.environ.get("CLIPS_KEEP", "30"))


def sh(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", default="", help="dossier des clips fabriqués (out/)")
    ap.add_argument("--radar", default="", help="fichier radar.json à publier")
    ap.add_argument("--run", default=os.environ.get("GITHUB_RUN_NUMBER", "0"))
    args = ap.parse_args()

    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ.get("GITHUB_TOKEN", "")
    public = f"https://github.com/{repo}.git"
    push = f"https://x-access-token:{token}@github.com/{repo}.git" if token else public

    tmp = Path(tempfile.mkdtemp())
    prev, site = tmp / "prev", tmp / "site"
    if sh(["git", "clone", "--depth", "1", "--branch", "gh-pages", public, str(prev)], check=False).returncode != 0:
        prev.mkdir()
    shutil.copytree("site", site)
    (site / ".nojekyll").write_text("")
    (site / "clips").mkdir(exist_ok=True)
    (site / "data").mkdir(exist_ok=True)

    # anciens clips et données
    index = []
    if (prev / "clips" / "index.json").exists():
        try:
            index = json.loads((prev / "clips" / "index.json").read_text())
        except ValueError:
            index = []
    if (prev / "data" / "radar.json").exists():
        shutil.copy(prev / "data" / "radar.json", site / "data" / "radar.json")
    if args.radar and Path(args.radar).exists():
        shutil.copy(args.radar, site / "data" / "radar.json")

    # nouveaux clips
    new = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.clips:
        Path(args.clips).mkdir(parents=True, exist_ok=True)
        for mp4 in sorted(Path(args.clips).glob("*.mp4")):
            cid = f"{int(args.run):04d}-{mp4.stem}"
            meta = {}
            if mp4.with_suffix(".json").exists():
                meta = json.loads(mp4.with_suffix(".json").read_text())
            shutil.copy(mp4, site / "clips" / f"{cid}.mp4")
            sh(["ffmpeg", "-y", "-v", "error", "-ss", "1.8", "-i", str(mp4), "-frames:v", "1",
                "-vf", "scale=360:-2", str(site / "clips" / f"{cid}.jpg")], check=False)
            fiche = mp4.with_name(mp4.stem + "_fiche.md")
            if fiche.exists():
                shutil.copy(fiche, site / "clips" / f"{cid}.md")
            new.append({"id": cid, "run": int(args.run), "created": now, **meta})
    index = (new[::-1] + index)[:KEEP]
    for entry in index:  # reprendre les fichiers des clips conservés
        for ext in ("mp4", "jpg", "md"):
            src, dst = prev / "clips" / f"{entry['id']}.{ext}", site / "clips" / f"{entry['id']}.{ext}"
            if src.exists() and not dst.exists():
                shutil.copy(src, dst)
    (site / "clips" / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))

    # état de chaque fabrication, lu par le studio pour afficher un message clair
    (site / "status").mkdir(exist_ok=True)
    if (prev / "status").exists():
        for f in sorted((prev / "status").glob("*.json"))[-50:]:
            shutil.copy(f, site / "status" / f.name)
    if args.clips:
        err = Path(args.clips) / "erreur.json"
        state = {"run": int(args.run), "ok": bool(new), "clips": [c["id"] for c in new]}
        if err.exists():
            state["message"] = json.loads(err.read_text()).get("message", "")
        (site / "status" / f"{int(args.run):04d}.json").write_text(json.dumps(state, ensure_ascii=False))

    sh(["git", "init", "-q", "-b", "gh-pages"], cwd=site)
    sh(["git", "config", "user.name", "clip-usine-bot"], cwd=site)
    sh(["git", "config", "user.email", "clip-usine-bot@users.noreply.github.com"], cwd=site)
    sh(["git", "add", "-A"], cwd=site)
    sh(["git", "commit", "-q", "-m", f"site : {len(new)} nouveau(x) clip(s), {len(index)} en ligne"], cwd=site)
    r = sh(["git", "push", "-q", "--force", push, "gh-pages"], cwd=site, check=False)
    if r.returncode != 0:
        raise SystemExit("Publication impossible : " + r.stderr.replace(token, "***")[-400:])
    print(f"Publié : {len(new)} nouveau(x) clip(s), {len(index)} en ligne.")


if __name__ == "__main__":
    main()
