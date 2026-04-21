#!/usr/bin/env python3
"""
VRChat Poster Loader Atlas Builder

Reads poster_data.json and individual poster images (512x1024 each).
Groups posters into 2x2 atlases (1024x2048) and generates UV-mapped metadata.
Uses hash caching to skip regenerating unchanged atlases.
"""

import json
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from PIL import Image
import hashlib

# Configuration
REPO_ROOT = Path(__file__).parent
SOURCE_DATA = REPO_ROOT / "poster_data.json"
SOURCE_IMAGES = REPO_ROOT / "images"
BUILT_ASSETS = REPO_ROOT / "built_assets"
BUILT_IMAGES = BUILT_ASSETS / "images"
BUILT_DATA = BUILT_ASSETS / "poster_data.json"
HASH_CACHE_FILE = BUILT_ASSETS / ".hashes.json"

# Image dimensions
SOURCE_SIZE = (512, 1024)   # width, height of each source image
ATLAS_SIZE = (1024, 2048)   # 2x2 grid of 512x1024 tiles
TILE_W = 512                # tile width
TILE_H = 1024               # tile height


def get_github_pages_base():
    """Derive the GitHub Pages base URL from env vars (CI) or git remote (local)."""
    # GitHub Actions provides these automatically
    repo_env  = os.environ.get("GITHUB_REPOSITORY")        # e.g. "Sairenix/tus.github.io"
    owner_env = os.environ.get("GITHUB_REPOSITORY_OWNER")  # e.g. "Sairenix"

    if repo_env and owner_env:
        owner = owner_env.lower()
        repo  = repo_env.split("/")[1]
    else:
        # Fall back to parsing the git remote when running locally
        try:
            remote = subprocess.check_output(
                ["git", "remote", "get-url", "origin"],
                cwd=REPO_ROOT, text=True
            ).strip()
            # Handles both HTTPS and SSH remotes:
            #   https://github.com/Sairenix/tus.github.io.git
            #   git@github.com:Sairenix/tus.github.io.git
            if "github.com/" in remote:
                path = remote.split("github.com/")[1]
            elif "github.com:" in remote:
                path = remote.split("github.com:")[1]
            else:
                raise ValueError(f"Unrecognised remote: {remote}")
            owner, repo = path.rstrip(".git").split("/")
            owner = owner.lower()
        except Exception as e:
            raise RuntimeError(f"Could not determine GitHub repo from git remote: {e}")

    # User/org pages repos (owner.github.io) serve from the root, no repo segment
    if repo.lower() == f"{owner}.github.io":
        return f"https://{owner}.github.io"
    else:
        return f"https://{owner}.github.io/{repo}"


GITHUB_URL_BASE = get_github_pages_base() + "/built_assets/images"

NUM_SLOTS = 29  # Slots 0-28
ATLAS_SLOTS = 4  # 2x2 grid per atlas
NUM_ATLASES = (NUM_SLOTS + ATLAS_SLOTS - 1) // ATLAS_SLOTS  # 8 atlases


def compute_file_hash(file_path):
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            sha256.update(f.read())
        return sha256.hexdigest()
    except FileNotFoundError:
        return None


def load_hash_cache():
    """Load the hash cache from disk."""
    if HASH_CACHE_FILE.exists():
        with open(HASH_CACHE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_hash_cache(cache):
    """Save the hash cache to disk."""
    HASH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HASH_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def get_source_image_hashes(slot_ids):
    """Get hashes of source images for a list of slot IDs."""
    hashes = []
    for slot_id in slot_ids:
        image_path = SOURCE_IMAGES / f"{slot_id}.png"
        h = compute_file_hash(image_path)
        hashes.append(h)
    return hashes


def atlas_needs_rebuild(atlas_index, hash_cache):
    """Check if an atlas needs to be rebuilt based on source image hashes."""
    start_slot = atlas_index * ATLAS_SLOTS
    slot_ids = list(range(start_slot, min(start_slot + ATLAS_SLOTS, NUM_SLOTS)))

    # Get current hashes of source images
    current_hashes = get_source_image_hashes(slot_ids)

    # Get cached hashes
    cache_key = f"atlas_{atlas_index}"
    cached_hashes = hash_cache.get(cache_key, [])

    # Compare
    return current_hashes != cached_hashes


def create_black_tile():
    """Create a solid black RGBA tile (512x1024, fully opaque)."""
    return Image.new("RGBA", SOURCE_SIZE, (0, 0, 0, 255))


def load_image(slot_id):
    """Load an image for a slot, or return a black tile if missing/invisible."""
    image_path = SOURCE_IMAGES / f"{slot_id}.png"

    # Load source data to check visibility
    with open(SOURCE_DATA, "r") as f:
        data = json.load(f)

    slot_data = data.get(str(slot_id), {})
    is_visible = slot_data.get("isVisible", False)

    if not is_visible or not image_path.exists():
        return create_black_tile()

    try:
        img = Image.open(image_path).convert("RGBA")
        if img.size != SOURCE_SIZE:
            img = img.resize(SOURCE_SIZE, Image.Resampling.LANCZOS)
        return img
    except Exception:
        return create_black_tile()


def create_atlas(atlas_index):
    """Create a 2x2 atlas (1024x2048) from 4 source images."""
    start_slot = atlas_index * ATLAS_SLOTS
    slot_ids = list(range(start_slot, min(start_slot + ATLAS_SLOTS, NUM_SLOTS)))

    # Pad with black tiles if needed
    while len(slot_ids) < ATLAS_SLOTS:
        slot_ids.append(-1)  # Placeholder for black tile

    # Create blank atlas
    atlas = Image.new("RGBA", ATLAS_SIZE, (0, 0, 0, 255))

    # Layout (PIL y=0 at top):
    # [0][1]  top half (y 0-1023)
    # [2][3]  bottom half (y 1024-2047)

    positions = [
        (0,     0),      # Slot 0: top-left
        (TILE_W, 0),     # Slot 1: top-right
        (0,     TILE_H), # Slot 2: bottom-left
        (TILE_W, TILE_H),# Slot 3: bottom-right
    ]

    for slot_index, slot_id in enumerate(slot_ids):
        if slot_id == -1:
            img = create_black_tile()
        else:
            img = load_image(slot_id)

        pos = positions[slot_index]
        atlas.paste(img, pos, img)

    return atlas


def compute_uv_offset(poster_id):
    """
    Compute UV offset for a poster within its atlas.
    Slot layout in atlas: 0=TL, 1=TR, 2=BL, 3=BR
    Unity UV: origin at bottom-left, y increases upward
    PNG: origin at top-left, y increases downward
    """
    slot_in_atlas = poster_id % ATLAS_SLOTS
    col = slot_in_atlas % 2
    row = slot_in_atlas // 2
    # Flip row for Unity UV (y=0 at bottom)
    uv_y = (1 - row) * 0.5
    uv_x = col * 0.5
    return [uv_x, uv_y]


def main():
    """Main build process."""
    print("Building poster atlases...")

    # Create output directory
    BUILT_IMAGES.mkdir(parents=True, exist_ok=True)

    # Load source data
    with open(SOURCE_DATA, "r") as f:
        poster_data = json.load(f)

    # Load hash cache
    hash_cache = load_hash_cache()

    # Track which atlases were rebuilt
    rebuilt_atlases = []

    # Generate atlases
    atlas_urls = []
    for atlas_index in range(NUM_ATLASES):
        atlas_path = BUILT_IMAGES / f"atlas_{atlas_index}.png"
        atlas_url = f"{GITHUB_URL_BASE}/atlas_{atlas_index}.png"
        atlas_urls.append(atlas_url)

        # Check if rebuild is needed
        if not atlas_needs_rebuild(atlas_index, hash_cache):
            print(f"  Skipping atlas_{atlas_index}.png (unchanged)")
            continue

        print(f"  Building atlas_{atlas_index}.png...")
        atlas = create_atlas(atlas_index)
        atlas.save(atlas_path, "PNG")

        # Update hash cache
        start_slot = atlas_index * ATLAS_SLOTS
        slot_ids = list(range(start_slot, min(start_slot + ATLAS_SLOTS, NUM_SLOTS)))
        current_hashes = get_source_image_hashes(slot_ids)
        hash_cache[f"atlas_{atlas_index}"] = current_hashes

        rebuilt_atlases.append(atlas_index)

    # Save updated hash cache
    save_hash_cache(hash_cache)

    # Generate poster_data.json
    print("  Generating poster_data.json...")
    build_time = datetime.now(timezone.utc).isoformat()

    output_data = {
        "buildTime": build_time,
        "atlases": atlas_urls,
        "posters": {}
    }

    for poster_id in range(NUM_SLOTS):
        poster_id_str = str(poster_id)
        if poster_id_str not in poster_data:
            continue

        original = poster_data[poster_id_str]
        atlas_index = poster_id // ATLAS_SLOTS
        uv_offset = compute_uv_offset(poster_id)

        output_data["posters"][poster_id_str] = {
            "name": original.get("name", ""),
            "isVisible": original.get("isVisible", False),
            "atlasIndex": atlas_index,
            "uvOffset": uv_offset,
            "uvScale": [0.5, 0.5]
        }

    with open(BUILT_DATA, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"Build complete! Rebuilt {len(rebuilt_atlases)} atlases.")
    if rebuilt_atlases:
        print(f"  Atlases: {rebuilt_atlases}")


if __name__ == "__main__":
    main()
