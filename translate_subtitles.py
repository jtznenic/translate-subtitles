from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_PATH = Path("config.json")
GLOSSARY_PATH = Path("glossary.json")
REQUEST_TIMEOUT_SECONDS = 120
MAX_RETRIES = 3


@dataclass(frozen=True)
class SubtitleEntry:
    index: int
    timing: str
    text_lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.text_lines).strip()


class RateLimiter:
    def __init__(self, rpm_limit: int) -> None:
        self.rpm_limit = max(1, rpm_limit)
        self._timestamps: deque[float] = deque()
        self._condition = threading.Condition()

    def acquire(self) -> None:
        with self._condition:
            while True:
                now = time.monotonic()
                window_start = now - 60
                while self._timestamps and self._timestamps[0] <= window_start:
                    self._timestamps.popleft()

                if len(self._timestamps) < self.rpm_limit:
                    self._timestamps.append(now)
                    self._condition.notify_all()
                    return

                wait_seconds = max(0.05, 60 - (now - self._timestamps[0]))
                self._condition.wait(timeout=wait_seconds)


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SystemExit(f"缺少配置文件: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON 文件格式错误: {path} ({exc})") from exc


def resolve_api_key(config: dict[str, Any]) -> str:
    api_key = os.getenv("SUBTITLE_API_KEY") or config["api"].get("api_key", "")
    if not api_key:
        raise SystemExit("未配置 API 密钥。请设置 SUBTITLE_API_KEY 环境变量或在 config.json 中填写 api.api_key。")
    return api_key


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多线程字幕翻译工具")
    parser.add_argument("input_file", help="输入的 SRT 字幕文件路径")
    parser.add_argument("output_file", nargs="?", help="输出的 SRT 文件路径，可选")
    return parser.parse_args()


def parse_srt(content: str) -> list[SubtitleEntry]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = re.split(r"\n\s*\n", normalized.strip())
    entries: list[SubtitleEntry] = []

    for block in blocks:
        lines = [line.rstrip() for line in block.split("\n")]
        if len(lines) < 3:
            continue

        try:
            index = int(lines[0].strip())
        except ValueError:
            continue

        timing = lines[1].strip()
        text_lines = [line for line in lines[2:] if line != ""]
        entries.append(SubtitleEntry(index=index, timing=timing, text_lines=text_lines))

    if not entries:
        raise SystemExit("未从输入文件中解析出有效的 SRT 字幕条目。")

    return entries


def chunk_entries(entries: list[SubtitleEntry], chunk_size: int) -> list[list[SubtitleEntry]]:
    if chunk_size <= 0:
        raise SystemExit("translation.chunk_size 必须是正整数。")
    return [entries[i : i + chunk_size] for i in range(0, len(entries), chunk_size)]


def format_glossary(glossary: dict[str, str]) -> str:
    if not glossary:
        return "无"
    return "\n".join(f"- {source}: {target}" for source, target in glossary.items())


def build_prompt(
    entries: list[SubtitleEntry],
    config: dict[str, Any],
    glossary_text: str,
) -> str:
    translation_cfg = config["translation"]
    prompts_cfg = config["prompts"]
    mode = translation_cfg["mode"]
    prompt_template = prompts_cfg["bilingual_prompt"] if mode == "bilingual" else prompts_cfg["translation_prompt"]
    content = "\n\n".join(f"id: {entry.index}\ntext:\n{entry.text}" for entry in entries)
    return prompt_template.format(
        target_language=translation_cfg["target_language"],
        glossary=glossary_text,
        content=content,
    )


def extract_message_content(response_payload: dict[str, Any]) -> str:
    try:
        content = response_payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"API 响应缺少 message content: {response_payload}") from exc

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        merged = "".join(parts).strip()
        if merged:
            return merged

    raise ValueError(f"无法解析 API 返回内容: {content!r}")


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
    return cleaned.strip()


def extract_json_array(text: str) -> list[dict[str, Any]]:
    cleaned = strip_code_fences(text)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[\s*{.*}\s*]", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError(f"模型输出不是合法 JSON 数组: {cleaned[:300]}")
        payload = json.loads(match.group(0))

    if not isinstance(payload, list):
        raise ValueError(f"模型输出 JSON 不是数组: {payload!r}")

    result: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"模型输出数组元素不是对象: {item!r}")
        result.append(item)
    return result


class PartialTranslationError(ValueError):
    def __init__(
        self,
        translated_by_id: dict[int, str],
        missing_ids: list[int],
    ) -> None:
        super().__init__(f"返回结果缺少字幕 id: {missing_ids}")
        self.translated_by_id = translated_by_id
        self.missing_ids = missing_ids


def request_translation(
    entries: list[SubtitleEntry],
    config: dict[str, Any],
    glossary_text: str,
    api_key: str,
    rate_limiter: RateLimiter,
) -> dict[int, str]:
    api_cfg = config["api"]
    translation_cfg = config["translation"]
    remaining_entries = list(entries)
    translated_by_id: dict[int, str] = {}
    last_error: Exception | None = None
    attempt = 0

    while remaining_entries and attempt < MAX_RETRIES:
        prompt = build_prompt(remaining_entries, config, glossary_text)
        payload = {
            "model": api_cfg["model_id"],
            "temperature": api_cfg.get("temperature", 0.3),
            "max_tokens": api_cfg.get("max_tokens", 8192),
            "messages": [
                {
                    "role": "system",
                    "content": config["prompts"]["system_prompt"].format(
                        target_language=translation_cfg["target_language"]
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }

        request_data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            api_cfg["api_url"],
            data=request_data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            rate_limiter.acquire()
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw_response = response.read().decode("utf-8")
            response_payload = json.loads(raw_response)
            message_content = extract_message_content(response_payload)
            items = extract_json_array(message_content)
            translated = validate_translation_items(remaining_entries, items)
            translated_by_id.update(translated)
            remaining_entries = []
            return translated_by_id
        except PartialTranslationError as exc:
            last_error = exc
            new_ids = set(exc.translated_by_id).difference(translated_by_id)
            translated_by_id.update(exc.translated_by_id)
            remaining_entries = [entry for entry in remaining_entries if entry.index in exc.missing_ids]
            if remaining_entries:
                print(
                    f"分块 {entries[0].index}-{entries[-1].index} 缺少字幕 {exc.missing_ids}，正在补译。"
                )
            if new_ids:
                attempt = 0
            else:
                attempt += 1
            if attempt < MAX_RETRIES and remaining_entries:
                time.sleep(min(2 ** max(attempt, 1), 8))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            attempt += 1
            if attempt < MAX_RETRIES:
                time.sleep(min(2**attempt, 8))

    failed_ids = [entry.index for entry in remaining_entries] if remaining_entries else [entry.index for entry in entries]
    raise RuntimeError(f"字幕分块翻译失败: {failed_ids} ({last_error})")


def validate_translation_items(entries: list[SubtitleEntry], items: list[dict[str, Any]]) -> dict[int, str]:
    expected_ids = {entry.index for entry in entries}
    translated_by_id: dict[int, str] = {}

    for item in items:
        raw_id = item.get("id")
        translated = item.get("translated")
        try:
            subtitle_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"返回结果中的 id 非法: {item!r}") from exc

        if subtitle_id not in expected_ids:
            raise ValueError(f"返回结果中包含未知字幕 id: {subtitle_id}")
        if not isinstance(translated, str) or not translated.strip():
            raise ValueError(f"返回结果中的 translated 非法: {item!r}")
        translated_by_id[subtitle_id] = translated.strip()

    missing_ids = expected_ids.difference(translated_by_id)
    if missing_ids:
        raise PartialTranslationError(
            translated_by_id=translated_by_id,
            missing_ids=sorted(missing_ids),
        )

    return translated_by_id


def translate_entries(
    entries: list[SubtitleEntry],
    config: dict[str, Any],
    glossary: dict[str, str],
    api_key: str,
) -> dict[int, str]:
    translation_cfg = config["translation"]
    chunks = chunk_entries(entries, int(translation_cfg["chunk_size"]))
    glossary_text = format_glossary(glossary)
    rate_limiter = RateLimiter(int(translation_cfg.get("rpm_limit", 60)))
    translations: dict[int, str] = {}

    max_workers = max(1, int(translation_cfg.get("max_workers", 5)))
    print(f"开始翻译，共 {len(entries)} 条字幕，分为 {len(chunks)} 个分块，线程数 {max_workers}。")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(request_translation, chunk, config, glossary_text, api_key, rate_limiter): chunk
            for chunk in chunks
        }

        completed = 0
        for future in as_completed(future_map):
            chunk = future_map[future]
            chunk_result = future.result()
            translations.update(chunk_result)
            completed += 1
            chunk_start = chunk[0].index
            chunk_end = chunk[-1].index
            print(f"已完成分块 {completed}/{len(chunks)}: 字幕 {chunk_start}-{chunk_end}")

    return translations


def build_output_entries(entries: list[SubtitleEntry], translations: dict[int, str], mode: str) -> list[str]:
    blocks: list[str] = []
    for entry in entries:
        translated = translations[entry.index]
        if mode == "bilingual":
            text_lines = entry.text_lines + [f"<i>{translated}</i>"]
        else:
            text_lines = translated.splitlines() or [translated]

        block = "\n".join(
            [
                str(entry.index),
                entry.timing,
                *text_lines,
            ]
        )
        blocks.append(block)
    return blocks


def language_suffix(language: str) -> str:
    normalized = language.strip().lower()
    known = {
        "中文": "cn",
        "简体中文": "cn",
        "繁体中文": "tw",
        "中文（简体）": "cn",
        "中文（繁體）": "tw",
        "english": "en",
        "英语": "en",
        "日语": "ja",
        "日本语": "ja",
        "japanese": "ja",
        "韩语": "ko",
        "korean": "ko",
        "法语": "fr",
        "french": "fr",
        "德语": "de",
        "german": "de",
        "西班牙语": "es",
        "spanish": "es",
    }
    if normalized in known:
        return known[normalized]

    ascii_text = (
        normalized.encode("ascii", errors="ignore").decode("ascii").strip().replace(" ", "_")
    )
    ascii_text = re.sub(r"[^a-z0-9_]+", "", ascii_text)
    return ascii_text or "translated"


def resolve_output_path(input_path: Path, output_arg: str | None, target_language: str) -> Path:
    if output_arg:
        return Path(output_arg)
    suffix = language_suffix(target_language)
    return input_path.with_name(f"{input_path.stem}_{suffix}{input_path.suffix}")


def write_srt(path: Path, blocks: list[str]) -> None:
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_arguments()
    input_path = Path(args.input_file)
    if not input_path.exists():
        raise SystemExit(f"输入文件不存在: {input_path}")

    config = load_json_file(CONFIG_PATH)
    glossary = load_json_file(GLOSSARY_PATH) if GLOSSARY_PATH.exists() else {}
    api_key = resolve_api_key(config)

    subtitle_text = input_path.read_text(encoding="utf-8-sig")
    entries = parse_srt(subtitle_text)
    translations = translate_entries(entries, config, glossary, api_key)

    output_path = resolve_output_path(
        input_path=input_path,
        output_arg=args.output_file,
        target_language=config["translation"]["target_language"],
    )
    output_blocks = build_output_entries(entries, translations, config["translation"]["mode"])
    write_srt(output_path, output_blocks)

    print(f"翻译完成，输出文件: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
