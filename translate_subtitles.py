#!/usr/bin/env python3
"""
字幕翻译脚本
支持日语音轨翻译成中文，支持双语模式和纯中文模式
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import requests


@dataclass
class SubtitleEntry:
    """字幕条目"""
    index: int
    timestamp: str
    text: str
    original_lines: List[str]


@dataclass
class Config:
    """配置类"""
    api_key: str
    api_url: str
    model_id: str
    temperature: float
    max_tokens: int
    mode: str  # 'bilingual' 或 'chinese_only'
    chunk_size: int
    max_workers: int
    source_language: str
    target_language: str
    system_prompt: str
    translation_prompt: str
    bilingual_prompt: str

    @classmethod
    def from_file(cls, config_path: str) -> "Config":
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        api = data["api"]
        translation = data["translation"]
        prompts = data["prompts"]

        return cls(
            api_key=api["api_key"],
            api_url=api["api_url"],
            model_id=api["model_id"],
            temperature=api["temperature"],
            max_tokens=api["max_tokens"],
            mode=translation["mode"],
            chunk_size=translation["chunk_size"],
            max_workers=translation["max_workers"],
            source_language=translation["source_language"],
            target_language=translation["target_language"],
            system_prompt=prompts["system_prompt"],
            translation_prompt=prompts["translation_prompt"],
            bilingual_prompt=prompts["bilingual_prompt"],
        )


class Glossary:
    """固定词汇表"""

    def __init__(self, glossary_path: str):
        self.terms = {}
        if Path(glossary_path).exists():
            with open(glossary_path, "r", encoding="utf-8") as f:
                self.terms = json.load(f)

    def format_for_prompt(self) -> str:
        """格式化词汇表供提示词使用"""
        if not self.terms:
            return "无固定词汇表"
        lines = [f"{jp} -> {cn}" for jp, cn in self.terms.items()]
        return "\n".join(lines)

    def apply_post_replacements(self, text: str) -> str:
        """在翻译后应用词汇表替换（可选，用于校正）"""
        return text


class SubtitleParser:
    """字幕解析器"""

    @staticmethod
    def parse(srt_content: str) -> List[SubtitleEntry]:
        """解析SRT字幕文件内容"""
        entries = []
        # 按空行分割块
        blocks = re.split(r"\n\s*\n", srt_content.strip())

        for block in blocks:
            lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
            if len(lines) < 3:
                continue

            try:
                index = int(lines[0])
                timestamp = lines[1]
                text = "\n".join(lines[2:])  # 可能有多个文本行

                entries.append(
                    SubtitleEntry(
                        index=index,
                        timestamp=timestamp,
                        text=text,
                        original_lines=lines,
                    )
                )
            except (ValueError, IndexError):
                continue

        return entries

    @staticmethod
    def entries_to_text(entries: List[SubtitleEntry]) -> str:
        """将字幕条目转换为文本格式供翻译"""
        lines = []
        for entry in entries:
            lines.append(f"[{entry.index}] {entry.text}")
        return "\n".join(lines)


class Translator:
    """翻译器"""

    def __init__(self, config: Config, glossary: Glossary):
        self.config = config
        self.glossary = glossary
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        }

    def translate_chunk(self, entries: List[SubtitleEntry]) -> List[SubtitleEntry]:
        """翻译一个字块的字幕"""
        content = SubtitleParser.entries_to_text(entries)
        glossary_text = self.glossary.format_for_prompt()

        if self.config.mode == "bilingual":
            prompt = self.config.bilingual_prompt.format(
                glossary=glossary_text, content=content
            )
        else:
            prompt = self.config.translation_prompt.format(
                glossary=glossary_text, content=content
            )

        messages = [
            {"role": "system", "content": self.config.system_prompt},
            {"role": "user", "content": prompt},
        ]

        payload = {
            "model": self.config.model_id,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

        try:
            response = requests.post(
                self.config.api_url,
                headers=self.headers,
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            result = response.json()

            if "choices" in result and len(result["choices"]) > 0:
                translated_text = result["choices"][0]["message"]["content"]
                return self._parse_translation_result(entries, translated_text)
            else:
                print(f"API返回异常: {result}")
                return entries

        except requests.exceptions.RequestException as e:
            print(f"翻译请求失败: {e}")
            return entries
        except Exception as e:
            print(f"翻译处理失败: {e}")
            return entries

    def _parse_translation_result(
        self, original_entries: List[SubtitleEntry], translated_text: str
    ) -> List[SubtitleEntry]:
        """解析翻译结果"""
        result_entries = []
        lines = translated_text.strip().split("\n")

        current_idx = 0
        i = 0
        while i < len(lines) and current_idx < len(original_entries):
            line = lines[i].strip()

            # 双语模式: 查找 [序号] 开头的行
            match = re.match(r"^\[(\d+)\]\s*(.+)", line)
            if match:
                idx = int(match.group(1))
                jp_text = match.group(2)

                # 找到对应的原文条目
                orig_entry = None
                for entry in original_entries:
                    if entry.index == idx:
                        orig_entry = entry
                        break

                if orig_entry:
                    if self.config.mode == "bilingual":
                        # 查找下一行是否是 <i>...</i> 格式的翻译
                        translated_line = ""
                        if i + 1 < len(lines):
                            next_line = lines[i + 1].strip()
                            italic_match = re.match(r"^<i>(.+?)</i>$", next_line)
                            if italic_match:
                                translated_line = italic_match.group(1)
                                i += 1
                            elif not next_line.startswith("["):
                                # 可能是翻译行但没有标签
                                translated_line = next_line
                                i += 1

                        new_text = f"{orig_entry.text}\n<i>{translated_line}</i>"
                        result_entries.append(
                            SubtitleEntry(
                                index=orig_entry.index,
                                timestamp=orig_entry.timestamp,
                                text=new_text,
                                original_lines=orig_entry.original_lines,
                            )
                        )
                    else:
                        # 纯中文模式，直接替换
                        result_entries.append(
                            SubtitleEntry(
                                index=orig_entry.index,
                                timestamp=orig_entry.timestamp,
                                text=jp_text,
                                original_lines=orig_entry.original_lines,
                            )
                        )
                current_idx += 1
            i += 1

        # 如果有缺失的条目，保留原文
        if len(result_entries) < len(original_entries):
            existing_indices = {e.index for e in result_entries}
            for entry in original_entries:
                if entry.index not in existing_indices:
                    result_entries.append(entry)

        return sorted(result_entries, key=lambda x: x.index)


class SubtitleTranslator:
    """字幕翻译主类"""

    def __init__(self, config_path: str = "config.json", glossary_path: str = "glossary.json"):
        self.config = Config.from_file(config_path)
        self.glossary = Glossary(glossary_path)
        self.translator = Translator(self.config, self.glossary)
        self.parser = SubtitleParser()

    def translate_file(self, input_path: str, output_path: str):
        """翻译字幕文件"""
        print(f"正在读取字幕文件: {input_path}")

        with open(input_path, "r", encoding="utf-8") as f:
            content = f.read()

        entries = self.parser.parse(content)
        print(f"解析到 {len(entries)} 条字幕")

        # 分块
        chunks = self._chunk_entries(entries, self.config.chunk_size)
        print(f"分块数: {len(chunks)}，每块最多 {self.config.chunk_size} 条")

        # 并行翻译
        translated_entries = []
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            future_to_chunk = {
                executor.submit(self.translator.translate_chunk, chunk): i
                for i, chunk in enumerate(chunks)
            }

            for future in as_completed(future_to_chunk):
                chunk_idx = future_to_chunk[future]
                try:
                    result = future.result()
                    translated_entries.extend(result)
                    print(f"  第 {chunk_idx + 1}/{len(chunks)} 块翻译完成 ({len(result)} 条)")
                except Exception as e:
                    print(f"  第 {chunk_idx + 1} 块翻译失败: {e}")

        # 排序并生成输出
        translated_entries.sort(key=lambda x: x.index)
        self._write_srt(translated_entries, output_path)

        print(f"\n翻译完成！已保存到: {output_path}")
        print(f"翻译模式: {'双语' if self.config.mode == 'bilingual' else '仅中文'}")

    def _chunk_entries(
        self, entries: List[SubtitleEntry], chunk_size: int
    ) -> List[List[SubtitleEntry]]:
        """将字幕条目分块"""
        chunks = []
        for i in range(0, len(entries), chunk_size):
            chunks.append(entries[i : i + chunk_size])
        return chunks

    def _write_srt(self, entries: List[SubtitleEntry], output_path: str):
        """写入SRT文件"""
        with open(output_path, "w", encoding="utf-8") as f:
            for i, entry in enumerate(entries):
                if i > 0:
                    f.write("\n")
                f.write(f"{entry.index}\n")
                f.write(f"{entry.timestamp}\n")
                f.write(f"{entry.text}\n")


def main():
    if len(sys.argv) < 2:
        print("用法: python translate_subtitles.py <输入字幕文件> [输出字幕文件]")
        print("示例: python translate_subtitles.py subtitles_jp.srt")
        print("示例: python translate_subtitles.py subtitles_jp.srt subtitles_cn.srt")
        sys.exit(1)

    input_file = sys.argv[1]
    if len(sys.argv) >= 3:
        output_file = sys.argv[2]
    else:
        # 自动生成输出文件名
        input_path = Path(input_file)
        if input_path.stem.endswith("_jp"):
            output_file = str(input_path.with_name(input_path.stem[:-3] + "_cn.srt"))
        else:
            output_file = str(input_path.with_stem(input_path.stem + "_translated"))

    if not Path(input_file).exists():
        print(f"错误: 文件不存在 - {input_file}")
        sys.exit(1)

    # 检查配置文件
    if not Path("config.json").exists():
        print("错误: 配置文件 config.json 不存在")
        sys.exit(1)

    translator = SubtitleTranslator()
    translator.translate_file(input_file, output_file)


if __name__ == "__main__":
    main()
