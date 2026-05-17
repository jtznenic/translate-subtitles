# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "requests",
# ]
# ///
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CONFIG_PATH = Path("config.json")
GLOSSARY_PATH = Path("glossary.json")
REQUEST_TIMEOUT_SECONDS = 120
MAX_RETRIES = 5

# 全局 Session：启用 HTTP Keep-Alive 和连接池，复用 TCP/SSL 连接
_session = requests.Session()


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubtitleEntry:
    index: int
    timing: str
    text_lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.text_lines).strip()


class RateLimiter:
    """基于滑动窗口的每分钟请求数限制器。"""

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
                    return
                wait_seconds = max(0.05, 60 - (now - self._timestamps[0]))
                self._condition.wait(timeout=wait_seconds)


class PartialTranslationError(ValueError):
    """部分字幕翻译成功，但仍有缺失 id。"""

    def __init__(self, translated_by_id: dict[int, str], missing_ids: list[int]) -> None:
        super().__init__(f"返回结果缺少字幕 id: {missing_ids}")
        self.translated_by_id = translated_by_id
        self.missing_ids = missing_ids


# ---------------------------------------------------------------------------
# 配置加载与校验
# ---------------------------------------------------------------------------

def load_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SystemExit(f"缺少配置文件: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON 文件格式错误: {path} ({exc})") from exc


def _load_config() -> tuple[dict[str, Any], dict[str, str]]:
    """加载 config.json 和可选的 glossary.json，返回 (config, glossary)。"""
    config = load_json_file(CONFIG_PATH)
    glossary = load_json_file(GLOSSARY_PATH) if GLOSSARY_PATH.exists() else {}
    return config, glossary


def _resolve_api_key(config: dict[str, Any]) -> str:
    api_key = os.getenv("SUBTITLE_API_KEY") or config["api"].get("api_key", "")
    if not api_key:
        raise SystemExit("未配置 API 密钥。请设置 SUBTITLE_API_KEY 环境变量或在 config.json 中填写 api.api_key。")
    return api_key


def _resolve_request_options(config: dict[str, Any]) -> tuple[float, int | None]:
    """解析 temperature 和 max_tokens 配置。"""
    api_cfg = config["api"]

    if "temperature" not in api_cfg:
        raise SystemExit("config.json 缺少 api.temperature 配置。")
    try:
        temperature = float(api_cfg["temperature"])
    except (TypeError, ValueError) as exc:
        raise SystemExit("config.json 中的 api.temperature 必须是数字。") from exc

    raw = api_cfg.get("max_tokens")
    if raw is None or str(raw).strip() == "":
        max_tokens = None
    else:
        try:
            max_tokens = int(raw)
        except (TypeError, ValueError) as exc:
            raise SystemExit("config.json 中的 api.max_tokens 必须是整数或留空。") from exc
        if max_tokens < 0:
            raise SystemExit("config.json 中的 api.max_tokens 不能为负数。")

    return temperature, max_tokens


# ---------------------------------------------------------------------------
# SRT 解析与输出
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="translate-subtitles 字幕翻译工具")
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
        text_lines = tuple(line for line in lines[2:] if line)
        entries.append(SubtitleEntry(index=index, timing=timing, text_lines=text_lines))

    if not entries:
        raise SystemExit("未从输入文件中解析出有效的 SRT 字幕条目。")
    return entries


def _resolve_output_path(input_path: Path, output_arg: str | None, translation_cfg: dict[str, Any]) -> Path:
    if output_arg:
        return Path(output_arg)

    target_code = translation_cfg.get("target_lang_code", "translated")
    mode = translation_cfg.get("mode", "translation_only")

    if mode == "bilingual":
        source_code = translation_cfg.get("source_lang_code", "")
        suffix = f"{source_code}_{target_code}" if source_code else target_code
    else:
        suffix = target_code

    return input_path.with_name(f"{input_path.stem}_{suffix}{input_path.suffix}")


def build_output_entries(entries: list[SubtitleEntry], translations: dict[int, str], mode: str) -> list[str]:
    blocks: list[str] = []
    for entry in entries:
        translated = translations.get(entry.index)
        if translated is None:
            raise RuntimeError(f"字幕条目 {entry.index} 没有对应的翻译结果，翻译流程可能存在缺漏。")

        if mode == "bilingual":
            text_lines = list(entry.text_lines) + [f"<i>{translated}</i>"]
        else:
            text_lines = translated.splitlines() or [translated]

        blocks.append("\n".join([str(entry.index), entry.timing, *text_lines]))
    return blocks


def write_srt(path: Path, blocks: list[str]) -> None:
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# API 适配层：构建请求 / 解析响应
# ---------------------------------------------------------------------------

def _build_openai_request(
    api_cfg: dict, api_key: str, system_prompt: str, user_prompt: str,
    temperature: float, max_tokens: int | None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "model": api_cfg["model_id"],
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return api_cfg["api_url"], headers, payload


def _extract_openai_content(data: dict) -> str:
    try:
        message = data["choices"][0]["message"]
        content = message.get("content")
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"API 响应缺少 message 字段: {data}") from exc

    if content is None:
        detail = "且包含 reasoning 字段，说明 max_tokens 太小，导致在推理阶段被截断。" if message.get("reasoning") else ""
        raise ValueError(
            f"API 返回的 content 为 None{detail} 完整响应: {json.dumps(data, ensure_ascii=False)}"
        )

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        merged = "".join(
            str(item.get("text", "")) for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
        if merged:
            return merged

    raise ValueError(f"无法解析 API 返回内容: {content!r}, 完整响应: {json.dumps(data, ensure_ascii=False)}")


# ---------------------------------------------------------------------------
# JSON 解析工具
# ---------------------------------------------------------------------------

def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
    return cleaned.strip()


def _fix_invalid_json_escapes(text: str) -> str:
    """修复 LLM 输出中不合法的 JSON 转义序列（如 \\S、\\e 等）。"""
    return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """从模型输出中提取 JSON 数组，依次尝试: 直接解析 → 修复转义 → 正则提取。"""
    cleaned = _strip_code_fences(text)

    for candidate in (cleaned, _fix_invalid_json_escapes(cleaned)):
        try:
            payload = json.loads(candidate)
            if isinstance(payload, list):
                break
        except json.JSONDecodeError:
            continue
    else:
        # 正则兜底：提取 [...] 部分
        match = re.search(r"\[\s*{.*}\s*]", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError(f"模型输出不是合法 JSON 数组: {cleaned[:300]}")
        payload = json.loads(_fix_invalid_json_escapes(match.group(0)))

    if not isinstance(payload, list):
        raise ValueError(f"模型输出 JSON 不是数组: {payload!r}")

    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"模型输出数组元素不是对象: {item!r}")
    return payload


# ---------------------------------------------------------------------------
# 翻译核心逻辑
# ---------------------------------------------------------------------------

def _format_glossary(glossary: dict[str, str]) -> str:
    if not glossary:
        return "无"
    return "\n".join(f"- {src}: {tgt}" for src, tgt in glossary.items())


def _build_user_prompt(entries: list[SubtitleEntry], config: dict[str, Any], glossary_text: str) -> str:
    translation_cfg = config["translation"]
    template = config["prompts"]["user_prompt"]
    content = "\n\n".join(f"id: {e.index}\ntext:\n{e.text}" for e in entries)
    return template.format(
        target_language=translation_cfg["target_language"],
        glossary=glossary_text,
        content=content,
    )


def _validate_translation(entries: list[SubtitleEntry], items: list[dict[str, Any]]) -> dict[int, str]:
    """校验模型返回的翻译结果，返回 {id: translated_text} 映射。"""
    expected_ids = {e.index for e in entries}
    translated: dict[int, str] = {}

    for item in items:
        try:
            sid = int(item.get("id"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"返回结果中的 id 非法: {item!r}") from exc

        if sid not in expected_ids:
            continue  # 模型偶尔会返回多余 id，静默跳过
        text = item.get("translated")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"返回结果中的 translated 非法: {item!r}")
        translated[sid] = text.strip()

    missing = sorted(expected_ids - set(translated))
    if missing:
        raise PartialTranslationError(translated_by_id=translated, missing_ids=missing)
    return translated


def _request_translation(
    entries: list[SubtitleEntry],
    config: dict[str, Any],
    glossary_text: str,
    api_key: str,
    rate_limiter: RateLimiter,
    temperature: float,
    max_tokens_cfg: int | None,
) -> dict[int, str]:
    """对一个分块发起翻译请求，含自动重试和部分翻译补译。"""
    api_cfg = config["api"]
    translation_cfg = config["translation"]
    system_prompt = config["prompts"]["system_prompt"].format(
        target_language=translation_cfg["target_language"],
    )

    remaining = list(entries)
    translated_all: dict[int, str] = {}
    last_error: Exception | None = None
    attempt = 0
    chunk_label = f"{entries[0].index}-{entries[-1].index}"

    while remaining and attempt < MAX_RETRIES:
        prompt = _build_user_prompt(remaining, config, glossary_text)

        # 动态计算 max_tokens：配置值为 0 时按输入长度自动估算
        if max_tokens_cfg == 0:
            current_max_tokens: int | None = math.ceil((len(system_prompt) + len(prompt)) * 1.5)
        else:
            current_max_tokens = max_tokens_cfg

        url, headers, payload = _build_openai_request(
            api_cfg, api_key, system_prompt, prompt, temperature, current_max_tokens,
        )

        t0 = time.monotonic()
        try:
            rate_limiter.acquire()
            resp = _session.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            content = _extract_openai_content(resp.json())
            items = _parse_json_array(content)
            result = _validate_translation(remaining, items)
            translated_all.update(result)
            return translated_all

        except PartialTranslationError as exc:
            last_error = exc
            elapsed = time.monotonic() - t0
            new_ids = set(exc.translated_by_id) - set(translated_all)
            translated_all.update(exc.translated_by_id)
            remaining = [e for e in remaining if e.index in exc.missing_ids]
            if remaining:
                print(f"分块 {chunk_label} 缺少字幕 {exc.missing_ids}，正在补译。({elapsed:.1f}s)")
            attempt = 0 if new_ids else attempt + 1
            if attempt < MAX_RETRIES and remaining:
                time.sleep(min(2 ** max(attempt, 1), 4))

        except (requests.RequestException, json.JSONDecodeError, ValueError, OSError, TimeoutError) as exc:
            last_error = exc
            attempt += 1
            elapsed = time.monotonic() - t0
            error_msg = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, requests.RequestException) and exc.response is not None:
                try:
                    error_msg += f"\n详细错误信息: {exc.response.text[:1000]}"
                except Exception:
                    pass
            print(f"分块 {chunk_label} 第 {attempt} 次请求失败 ({elapsed:.1f}s): {error_msg}")
            if attempt < MAX_RETRIES:
                time.sleep(min(2 ** attempt, 16) + random.uniform(0, 2))

    failed_ids = [e.index for e in (remaining or entries)]
    raise RuntimeError(f"字幕分块翻译失败: {failed_ids} ({last_error})")


def translate_entries(
    entries: list[SubtitleEntry],
    config: dict[str, Any],
    glossary: dict[str, str],
    api_key: str,
) -> dict[int, str]:
    """并发翻译所有字幕条目，返回 {id: translated_text} 映射。"""
    translation_cfg = config["translation"]
    temperature, max_tokens_cfg = _resolve_request_options(config)
    chunk_size = int(translation_cfg["chunk_size"])
    chunks = [entries[i:i + chunk_size] for i in range(0, len(entries), chunk_size)]
    glossary_text = _format_glossary(glossary)
    rate_limiter = RateLimiter(int(translation_cfg.get("rpm_limit", 60)))

    max_workers = min(max(1, int(translation_cfg.get("max_workers", 5))), max(1, len(chunks)))
    print(f"开始翻译，共 {len(entries)} 条字幕，分为 {len(chunks)} 个分块，线程数 {max_workers}。")

    translations: dict[int, str] = {}
    failed_chunks: list[list[SubtitleEntry]] = []
    completed = 0
    chunk_start_times: dict[int, float] = {}

    def _do_translate(chunk: list[SubtitleEntry]) -> dict[int, str]:
        chunk_start_times[id(chunk)] = time.monotonic()
        return _request_translation(
            chunk, config, glossary_text, api_key,
            rate_limiter, temperature, max_tokens_cfg,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_chunk = {executor.submit(_do_translate, c): c for c in chunks}
        for future in as_completed(future_to_chunk):
            chunk = future_to_chunk[future]
            label = f"{chunk[0].index}-{chunk[-1].index}"
            elapsed = time.monotonic() - chunk_start_times.get(id(chunk), time.monotonic())
            try:
                translations.update(future.result())
                completed += 1
                print(f"已完成分块 {completed}/{len(chunks)}: 字幕 {label} ({elapsed:.1f}s)")
            except RuntimeError as exc:
                failed_chunks.append(chunk)
                print(f"分块 {label} 暂时失败 ({elapsed:.1f}s)，稍后重试: {exc}")

    # 顺序重试失败的分块
    if failed_chunks:
        print(f"正在重试 {len(failed_chunks)} 个失败的分块...")
        for chunk in failed_chunks:
            label = f"{chunk[0].index}-{chunk[-1].index}"
            time.sleep(5 + random.uniform(0, 3))
            try:
                t0 = time.monotonic()
                translations.update(_do_translate(chunk))
                completed += 1
                print(f"已完成分块 {completed}/{len(chunks)}: 字幕 {label}（重试成功, {time.monotonic() - t0:.1f}s）")
            except RuntimeError as exc:
                raise RuntimeError(f"字幕分块重试后仍然失败: {[e.index for e in chunk]} ({exc})") from exc

    return translations


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> int:
    start_time = time.monotonic()
    args = parse_arguments()
    input_path = Path(args.input_file)
    if not input_path.exists():
        raise SystemExit(f"输入文件不存在: {input_path}")

    config, glossary = _load_config()
    api_key = _resolve_api_key(config)

    entries = parse_srt(input_path.read_text(encoding="utf-8-sig"))
    translations = translate_entries(entries, config, glossary, api_key)

    output_path = _resolve_output_path(input_path, args.output_file, config["translation"])
    output_blocks = build_output_entries(entries, translations, config["translation"]["mode"])
    write_srt(output_path, output_blocks)

    elapsed = time.monotonic() - start_time
    mins, secs = divmod(elapsed, 60)
    print(f"翻译完成，输出文件: {output_path}")
    print(f"执行耗时: {int(mins)} 分 {secs:.2f} 秒" if mins > 0 else f"执行耗时: {secs:.2f} 秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
