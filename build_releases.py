#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build PCM release ZIPs for all plugins in this repository.

For every subfolder containing a metadata.json it creates
dist/<plugin-folder>-<version>.zip. The ZIP root mirrors the plugin
folder (metadata.json + plugins/ + optional resources/), exactly the
archive layout the KiCad PCM expects. It then regenerates packages.json
with the real archive SHA-256 hashes and sizes.

Usage:
    python build_releases.py
    # optionally point at a different GitHub owner/repo for the URLs:
    python build_releases.py --owner mrred2k --repo Kicad-Tools
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def find_package_dirs():
    for name in sorted(os.listdir(HERE)):
        d = os.path.join(HERE, name)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "metadata.json")):
            yield name, d


def zip_dir(src, dst_zip):
    """Zip the *contents* of src into dst_zip (no top-level folder)."""
    with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, src)
                zf.write(full, rel.replace(os.sep, "/"))


def dir_size_bytes(path):
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            full = os.path.join(root, f)
            if full.endswith((".pyc", ".pyo")):
                continue
            total += os.path.getsize(full)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", default="mrred2k")
    ap.add_argument("--repo", default="Kicad-Tools")
    ap.add_argument("--tag-prefix", default="v")
    args = ap.parse_args()

    dist = os.path.join(HERE, "dist")
    os.makedirs(dist, exist_ok=True)

    packages = []
    for folder, d in find_package_dirs():
        meta_path = os.path.join(d, "metadata.json")
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        version = meta["versions"][0]["version"]
        zip_name = f"{folder}-{version}.zip"
        zip_path = os.path.join(dist, zip_name)

        print(f"Building {zip_name} ...")
        zip_dir(d, zip_path)

        sha = hashlib.sha256(open(zip_path, "rb").read()).hexdigest()
        dl_size = os.path.getsize(zip_path)
        install_size = dir_size_bytes(d)

        download_url = (
            f"https://github.com/{args.owner}/{args.repo}/releases/"
            f"download/{args.tag_prefix}{version}/{zip_name}"
        )

        entry = json.loads(json.dumps(meta))
        entry["versions"][0].update(
            {
                "download_sha256": sha,
                "download_size": dl_size,
                "download_url": download_url,
                "install_size": install_size,
            }
        )
        packages.append(entry)
        print(f"  {zip_name}: {dl_size} bytes, sha256 {sha[:16]}...")

    packages_path = os.path.join(HERE, "packages.json")
    with open(packages_path, "w", encoding="utf-8") as fh:
        json.dump({"packages": packages}, fh, indent=2)
        fh.write("\n")
    print(f"Wrote {packages_path} with {len(packages)} package(s).")

    # resources.zip: <identifier>/icon.png per package (icons for the PCM
    # repository view), plus repository.json sha256 update.
    res_zip = os.path.join(dist, "resources.zip")
    with zipfile.ZipFile(res_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, d in find_package_dirs():
            meta_path = os.path.join(d, "metadata.json")
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            ident = meta["identifier"]
            icon = os.path.join(d, "resources", "icon.png")
            if os.path.isfile(icon):
                zf.write(icon, f"{ident}/icon.png")
                print(f"  resources: {ident}/icon.png")
    res_sha = hashlib.sha256(open(res_zip, "rb").read()).hexdigest()
    print(f"Wrote {res_zip} ({os.path.getsize(res_zip)} bytes, "
          f"sha256 {res_sha[:16]}...)")

    # Patch repository.json: resource url + sha256 + timestamps
    repo_path = os.path.join(HERE, "repository.json")
    with open(repo_path, encoding="utf-8") as fh:
        repo = json.load(fh)
    now = datetime.now(timezone.utc)
    repo["resources"] = {
        "sha256": res_sha,
        "update_time_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
        "update_timestamp": int(now.timestamp()),
        "url": (f"https://github.com/{args.owner}/{args.repo}/releases/"
                f"download/{args.tag_prefix}{version}/resources.zip"),
    }
    with open(repo_path, "w", encoding="utf-8") as fh:
        json.dump(repo, fh, indent=4)
        fh.write("\n")
    print(f"Updated {repo_path} resources -> {res_zip}")


if __name__ == "__main__":
    main()
