import os
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request
from urllib.request import urlopen

from google import genai
from google.genai import types
from PIL import Image


def _load_repo_env() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _open_image(image_path: str) -> Image.Image:
    if image_path.startswith(("http://", "https://")):
        request = Request(
            image_path,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        try:
            with urlopen(request) as response:
                return Image.open(BytesIO(response.read())).copy()
        except HTTPError as exc:
            if exc.code == 403:
                raise ValueError(
                    "Image URL returned 403 Forbidden. The file is not publicly readable from this script. "
                    "Use a signed/public URL, download the image locally, or pass a local image path."
                ) from exc
            raise
    return Image.open(image_path)


def _api_key() -> str:
    google_api_key = os.getenv("GOOGLE_API_KEY")
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if google_api_key:
        if gemini_api_key:
            print("Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY.")
        return google_api_key
    if gemini_api_key:
        print("Using GEMINI_API_KEY.")
        return gemini_api_key
    raise ValueError("Please set GOOGLE_API_KEY or GEMINI_API_KEY in your environment or .env file.")


def generate_character_profile(image_path: str, output_txt_path: str = "character_details.txt"):
    """
    Analyzes a profile photo using Nano Banana 2 (Gemini 3.1 Flash Image)
    and extracts structured character details.
    """
    _load_repo_env()

    client = genai.Client(api_key=_api_key())

    # 2. Load the reference profile photo using Pillow
    try:
        profile_image = _open_image(image_path)
    except FileNotFoundError:
        print(f"Error: Could not find an image at {image_path}")
        return
    except Exception as e:
        print(f"Error loading image: {e}")
        return

   # 3. Enhanced system prompt optimized for stable child character replication
    prompt_instruction = (
        "Analyze this profile photo of a child and extract explicit, hyper-specific visual details "
        "to build a highly descriptive narrative character profile. The core goal is to extract features "
        "that ensure maximum visual consistency when this profile is used to generate new images. "
        "Provide the output in clean Markdown with the following structured sections:\n\n"
        
        "1. Core Child Identifiers (Perceived exact age between 3-12, distinct ethnic/heritage facial markers, "
        "and overall face shape like round, oval, heart-shaped).\n"
        
        "2. Fixed Facial Features (Detailed eye color and shape, eyebrow thickness/arch, nose shape like button, "
        "upturned, or flat, lip fullness, and specific ear prominence).\n"
        
        "3. Unchangeable Micro-Details (Explicitly note any freckle distribution patterns, dimples when smiling, "
        "moles, birthmarks, tooth gaps, or unique facial symmetry lines that define this child's face).\n"
        
        "4. Hair Typography (Exact hair color shades like sandy blonde, dark chestnut, texture like straight, wavy, "
        "tight curls, hair length, and the precise haircut style like pixie, bob, shaggy bowl cut, or faded sides).\n"
        
        "5. Current Attire & Visual Theme (Clothing type, fabric textures, worn accessories like glasses or hair clips, "
        "and primary color palette, plus any implied setting vibe like fantasy orphan, sci-fi academy cadet, or modern student).\n\n"
        
        "6. Text-to-Image Replication Prompt (Provide a condensed, comma-separated descriptive string optimized for "
        "diffusion pipelines. Start directly with the child's age, gender/archetype, and specific ethnic markers, followed by "
        "the fixed facial details, hair typography, and micro-details. DO NOT use vague or poetic words like 'cute', 'adorable', "
        "or 'beautiful'. Use only concrete, unchanging physical attributes so that pasting this exact string into an image "
        "generator yields a highly visually similar child)."
    )

    print(f"Analyzing {image_path} via gemini-3.1-flash-image...")

    try:
        # 4. Execute the multimodal request
        response = client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=[profile_image, prompt_instruction]
        )

        # 5. Process and save the text output
        character_details = response.text
        
        print("\n=== Generated Character Sheet ===")
        print(character_details)
        
        # Save to local file
        with open(output_txt_path, "w", encoding="utf-8") as f:
            f.write(character_details)
        print(f"\nSuccessfully saved character profile to: {output_txt_path}")

    except Exception as e:
        print(f"API Execution Error: {e}")

if __name__ == "__main__":
    # Replace this with the path to your local profile image (supports png, jpeg, webp)
    target_photo = 'https://pub-c2bbc7933325408a8f2d12ff895599a7.r2.dev/photo/c2395338-0e1e-486c-b4ee-6ca7ca92deaf/5acbbb8c-642e-4b05-889c-b11195910da9/profile.jpg'
    
    # Run the builder
    generate_character_profile(target_photo)
