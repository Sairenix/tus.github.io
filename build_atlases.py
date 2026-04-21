#!/usr/bin/env python3
"""
VRChat Poster Loader Atlas Builder

Reads poster_data.json and individual poster images (512x1024 each).
Groups posters into 2x2 atlases (1024x2048) and generates UV-mapped metadata.
Uses hash caching to skip regenerating unchanged atlases.
"""

import json
import os
import re
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
ATLAS_SIZE = (2048, 2048)   # 4x2 grid of 512x1024 tiles
TILE_W = 512                # tile width
TILE_H = 1024               # tile height
ATLAS_COLS = 4              # columns in atlas grid
ATLAS_ROWS = 2              # rows in atlas grid



NUM_SLOTS = 29                                                # Slots 0-28
ATLAS_SLOTS = ATLAS_COLS * ATLAS_ROWS                         # 8 images per atlas
NUM_ATLASES = (NUM_SLOTS + ATLAS_SLOTS - 1) // ATLAS_SLOTS   # 4 atlases


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
    """Create a 4x2 atlas (2048x2048) from 8 source images."""
    start_slot = atlas_index * ATLAS_SLOTS
    slot_ids = list(range(start_slot, min(start_slot + ATLAS_SLOTS, NUM_SLOTS)))

    # Pad with black tiles if needed
    while len(slot_ids) < ATLAS_SLOTS:
        slot_ids.append(-1)  # Placeholder for black tile

    # Create blank atlas
    atlas = Image.new("RGBA", ATLAS_SIZE, (0, 0, 0, 255))

    # Layout (PIL y=0 at top):
    # [0][1][2][3]  top half    (y 0-1023)
    # [4][5][6][7]  bottom half (y 1024-2047)
    positions = [
        (col * TILE_W, row * TILE_H)
        for row in range(ATLAS_ROWS)
        for col in range(ATLAS_COLS)
    ]

    for slot_index, slot_id in enumerate(slot_ids):
        img = create_black_tile() if slot_id == -1 else load_image(slot_id)
        atlas.paste(img, positions[slot_index], img)

    return atlas


def compute_uv_offset(poster_id):
    """
    Compute UV offset for a poster within its atlas.
    Slot layout (PIL, y=0 at top):
      [0][1][2][3]  row 0 (top)
      [4][5][6][7]  row 1 (bottom)
    Unity UV has y=0 at bottom, so rows are flipped.
    """
    slot_in_atlas = poster_id % ATLAS_SLOTS
    col = slot_in_atlas % ATLAS_COLS
    row = slot_in_atlas // ATLAS_COLS
    uv_x = col / ATLAS_COLS           # 0.0, 0.25, 0.5, 0.75
    uv_y = (1 - row) / ATLAS_ROWS     # row 0 → 0.5, row 1 → 0.0
    return [uv_x, uv_y]


def get_github_pages_base():
    """Derive the GitHub Pages base URL from env vars (CI) or git remote (local)."""
    repo_env  = os.environ.get("GITHUB_REPOSITORY")
    owner_env = os.environ.get("GITHUB_REPOSITORY_OWNER")

    if repo_env and owner_env:
        owner = owner_env.lower()
        repo  = repo_env.split("/")[1]
    else:
        try:
            remote = subprocess.check_output(
                ["git", "remote", "get-url", "origin"],
                cwd=REPO_ROOT, text=True
            ).strip()
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

    if repo.lower() == f"{owner}.github.io":
        return f"https://{owner}.github.io"
    else:
        return f"https://{owner}.github.io/{repo}"


def update_readme(pages_base, atlas_count):
    """Write built asset links into README.md between marker comments."""
    readme_path = REPO_ROOT / "README.md"

    links_block = (
        f"<!-- BUILT_LINKS_START -->\n"
        f"### Built Asset Links\n\n"
        f"| Asset | URL |\n"
        f"|---|---|\n"
        f"| Poster data JSON | `{pages_base}/built_assets/poster_data.json` |\n"
        f"| Atlas images | `{pages_base}/built_assets/images/` |\n"
        f"| Atlases built | {atlas_count} |\n"
        f"<!-- BUILT_LINKS_END -->"
    )

    if readme_path.exists():
        content = readme_path.read_text(encoding="utf-8")
        # Replace existing block if present
        content, replaced = re.subn(
            r"<!-- BUILT_LINKS_START -->.*?<!-- BUILT_LINKS_END -->",
            links_block,
            content,
            flags=re.DOTALL
        )
        if not replaced:
            # Append if markers not found
            content = content.rstrip() + "\n\n" + links_block + "\n"
    else:
        content = links_block + "\n"

    readme_path.write_text(content, encoding="utf-8")
    print("  Updated README.md with built asset links.")


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
    for atlas_index in range(NUM_ATLASES):
        atlas_path = BUILT_IMAGES / f"atlas_{atlas_index}.png"

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

    # Remove any stale atlas files that are no longer needed
    for stale in BUILT_IMAGES.glob("atlas_*.png"):
        try:
            index = int(stale.stem.split("_")[1])
            if index >= NUM_ATLASES:
                stale.unlink()
                print(f"  Removed stale {stale.name}")
        except (ValueError, IndexError):
            pass

    # Generate poster_data.json
    print("  Generating poster_data.json...")
    build_time = datetime.now(timezone.utc).isoformat()

    output_data = {
        "buildTime": build_time,
        "atlasCount": NUM_ATLASES,
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
            "uvScale": [1 / ATLAS_COLS, 1 / ATLAS_ROWS]
        }

    with open(BUILT_DATA, "w") as f:
        json.dump(output_data, f, indent=2)

    # Update README with live links
    pages_base = get_github_pages_base()
    update_readme(pages_base, NUM_ATLASES)

    # Build a descriptive commit message
    if rebuilt_atlases:
        indices = ", ".join(str(i) for i in rebuilt_atlases)
        commit_message = f"chore: rebuild atlas {indices} ({len(rebuilt_atlases)}/{NUM_ATLASES} rebuilt)"
    else:
        commit_message = "chore: update poster metadata (no atlases changed)"

    # Write commit message to GitHub Actions output if running in CI
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"commit_message={commit_message}\n")

    print(f"Build complete! Rebuilt {len(rebuilt_atlases)} atlases.")
    if rebuilt_atlases:
        print(f"  Atlases: {rebuilt_atlases}")


if __name__ == "__main__":
    main()
