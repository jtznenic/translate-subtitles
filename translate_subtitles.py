#!/usr/bin/env python3
"""Japanese subtitle translation tool based on AI."""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx


@dataclass
class Subtitle:
    """Subtitle entry."""

    id: int
    start_time: str
    end_time: str
    text: str


def parse_srt(content: str) -> list[Subtitle]:
    """Parse SRT format subtitle file."""
    pattern = r"(\d+)\n([\d:,]+) --> ([\d:,]+)\n(.*?)(?=\n\n|\Z)"
    matches = re.findall(pattern, content, re.DOTALL)

    subtitles = []
    for match in matches:
        subtitle = Subtitle(
            id=int(match[0]),
            start_time=match[1],
            end_time=match[2],
            text=match[3].strip(),
        )
        subtitles.append(subtitle)

    return subtitles


def format_srt(subtitles: list[Subtitle], mode: str = "chinese_only") -> str:
    """Format subtitles to SRT format."""
    lines = []
    for sub in subtitles:
        lines.append(str(sub.id))
        lines.append(f"{sub.start_time} --> {sub.end_time}")
        if mode == "bilingual" and "\n" in sub.text:
            parts = sub.text.split("\n", 1)
            lines.append(parts[0])
            lines.append(f"<i>{parts[1]}</i>")
        else:
            lines.append(sub.text)
        lines.append("")

    return "\n".join(lines)


def load_config(config_path: Path) -> dict:
    """Load configuration from JSON file."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_glossary(glossary_path: Path) -> dict:
    """Load glossary from JSON file."""
    if not glossary_path.exists():
        return {}
    with open(glossary_path, "r", encoding="utf-8") as f:
        return json.load(f)


def format_glossary(glossary: dict) -> str:
    """Format glossary to string."""
    if not glossary:
        return "无"
    lines = [f"{k}: {v}" for k, v in glossary.items()]
    return "\n".join(lines)


class RateLimiter:
    """Rate limiter for API requests."""

    def __init__(self, rpm_limit: int):
        self.rpm_limit = rpm_limit
        self.interval = 60.0 / rpm_limit
        self.last_request_time = 0.0

    def wait(self):
        """Wait for rate limit."""
        current_time = time.time()
        elapsed = current_time - self.last_request_time
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self.last_request_time = time.time()


def translate_chunk(
    subtitles: list[Subtitle], config: dict, glossary: dict, rate_limiter: RateLimiter
) -> list[Subtitle]:
    """Translate a chunk of subtitles using AI API."""
    api_config = config["api"]
    translation_config = config["translation"]
    prompts_config = config["prompts"]

    mode = translation_config["mode"]
    source_language = translation_config.get("source_language", "日语")
    target_language = translation_config.get("target_language", "中文")

    content_lines = []
    for sub in subtitles:
        content_lines.append(f"[{sub.id}] {sub.text}")
    content = "\n".join(content_lines)

    glossary_str = format_glossary(glossary)

    if mode == "bilingual":
        prompt = prompts_config["bilingual_prompt"].format(
            glossary=glossary_str,
            content=content,
            source_language=source_language,
            target_language=target_language,
        )
    else:
        prompt = prompts_config["translation_prompt"].format(
            glossary=glossary_str,
            content=content,
            source_language=source_language,
            target_language=target_language,
        )

    system_prompt = prompts_config["system_prompt"].format(
        source_language=source_language, target_language=target_language
    )

    rate_limiter.wait()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_config['api_key']}",
    }

    payload = {
        "model": api_config["model_id"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": api_config.get("temperature", 0.3),
        "max_tokens": api_config.get("max_tokens", 4096),
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    api_config["api_url"], headers=headers, json=payload
                )
                response.raise_for_status()

                result = response.json()
                content = result["choices"][0]["message"]["content"]

                json_match = re.search(r"\[.*\]", content, re.DOTALL)
                if json_match:
                    translations = json.loads(json_match.group())
                else:
                    translations = json.loads(content)

                translated_subs = []
                for trans in translations:
                    sub_id = trans["id"]
                    original_sub = next((s for s in subtitles if s.id == sub_id), None)
                    if original_sub:
                        if mode == "bilingual":
                            text = f"{original_sub.text}\n{trans['translated']}"
                        else:
                            text = trans["translated"]

                        translated_subs.append(
                            Subtitle(
                                id=sub_id,
                                start_time=original_sub.start_time,
                                end_time=original_sub.end_time,
                                text=text,
                            )
                        )

                return translated_subs

        except (httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                print(f"Translation attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(2**attempt)
            else:
                print(f"Translation failed after {max_retries} attempts: {e}")
                raise


def split_into_chunks(
    subtitles: list[Subtitle], chunk_size: int
) -> list[list[Subtitle]]:
    """Split subtitles into chunks."""
    chunks = []
    for i in range(0, len(subtitles), chunk_size):
        chunks.append(subtitles[i : i + chunk_size])
    return chunks


def translate_subtitles(
    subtitles: list[Subtitle], config: dict, glossary: dict
) -> list[Subtitle]:
    """Translate all subtitles with parallel processing."""
    translation_config = config["translation"]
    chunk_size = translation_config["chunk_size"]
    max_workers = translation_config["max_workers"]
    rpm_limit = translation_config.get("rpm_limit", 60)

    chunks = split_into_chunks(subtitles, chunk_size)
    rate_limiter = RateLimiter(rpm_limit)

    translated = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(translate_chunk, chunk, config, glossary, rate_limiter): i
            for i, chunk in enumerate(chunks)
        }

        results = [None] * len(chunks)

        for future in as_completed(futures):
            chunk_index = futures[future]
            try:
                result = future.result()
                results[chunk_index] = result
                print(f"Translated chunk {chunk_index + 1}/{len(chunks)}")
            except Exception as e:
                print(f"Error translating chunk {chunk_index + 1}: {e}")
                raise

        for result in results:
            if result:
                translated.extend(result)

    translated.sort(key=lambda s: s.id)
    return translated


def generate_output_filename(input_path: Path) -> Path:
    """Generate output filename based on input filename."""
    stem = input_path.stem
    suffix = input_path.suffix
    new_stem = stem + "_translated"

    return input_path.parent / f"{new_stem}{suffix}"


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: uv run python translate_subtitles.py <input_file> [output_file]")
        sys.exit(1)

    input_file = Path(sys.argv[1])

    if not input_file.exists():
        print(f"Error: Input file '{input_file}' not found")
        sys.exit(1)

    if len(sys.argv) >= 3:
        output_file = Path(sys.argv[2])
    else:
        output_file = generate_output_filename(input_file)

    script_dir = Path(__file__).parent
    config_path = script_dir / "config.json"
    glossary_path = script_dir / "glossary.json"

    if not config_path.exists():
        print(f"Error: Config file '{config_path}' not found")
        sys.exit(1)

    print(f"Loading config from {config_path}")
    config = load_config(config_path)

    print(f"Loading glossary from {glossary_path}")
    glossary = load_glossary(glossary_path)

    print(f"Reading subtitles from {input_file}")
    with open(input_file, "r", encoding="utf-8") as f:
        content = f.read()

    subtitles = parse_srt(content)
    print(f"Found {len(subtitles)} subtitles")

    translation_config = config["translation"]
    mode = translation_config["mode"]
    print(f"Translation mode: {mode}")
    print(f"Chunk size: {translation_config['chunk_size']}")
    print(f"Max workers: {translation_config['max_workers']}")

    print("Starting translation...")
    start_time = time.time()
    translated = translate_subtitles(subtitles, config, glossary)
    elapsed = time.time() - start_time

    print(f"Translation completed in {elapsed:.1f}s")
    print(f"Writing translated subtitles to {output_file}")
    output_content = format_srt(translated, mode)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(output_content)

    print(f"Translation completed! Output saved to {output_file}")


if __name__ == "__main__":
    main()
