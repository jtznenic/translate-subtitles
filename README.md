# translate-subtitles

`translate-subtitles` 是一个多功能的字幕处理与翻译工具集，包含了基于大语言模型（LLM）的多语言字幕翻译工具，以及本地字幕合并工具。

## 工具集概览

本项目目前提供以下两个主要脚本：

1. **`translate_subtitles.py`**：基于 AI 接口的自动字幕翻译工具。支持分块并发、词汇表、纯翻译/双语模式等。
2. **`merge_srt.py`**：本地双语字幕合并工具。可将单独的原文和译文字幕文件，合并为带 `<i>` 标签的双语字幕。

---

## 安装依赖

本项目使用 [uv](https://github.com/astral-sh/uv) 作为包管理工具。

### 安装 uv

如果尚未安装 uv：

```bash
# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 安装项目依赖

```bash
# 进入项目目录
cd translate-subtitles

# 使用 uv 同步依赖
uv sync
```

---

## 工具 1：自动字幕翻译 (`translate_subtitles.py`)

支持将任意语言的 SRT 字幕文件翻译成目标语言，支持双语模式、纯翻译模式、自定义词汇表和批量并发请求。

### 运行程序

```bash
# 基本用法
uv run translate_subtitles.py <输入字幕文件> [输出字幕文件]
```

运行前请先配置 API 密钥（推荐使用环境变量）：

```bash
# Linux/macOS
export SUBTITLE_API_KEY="your-api-key"

# Windows PowerShell
$env:SUBTITLE_API_KEY="your-api-key"
```

### 配置文件说明 (`config.json`)

```json
{
  "api": {
    "api_key": "",
    "api_url": "https://api.openai.com/v1/chat/completions",
    "model_id": "gpt-4.1-mini",
    "temperature": 0.3,
    "max_tokens": 4096
  },
  "translation": {
    "mode": "bilingual",
    "chunk_size": 10,
    "max_workers": 8,
    "rpm_limit": 60,
    "target_language": "中文",
    "source_lang_code": "jp",
    "target_lang_code": "cn"
  },
  ...
}
```

#### API 配置

本项目统一使用 OpenAI Chat Completions 兼容格式，适用于 OpenAI、Azure OpenAI、DeepSeek、Groq、GitHub Models 以及各类兼容 API（包括通过中转服务访问的 Anthropic、Gemini 等）。

- **`api_key`**: API 密钥。推荐优先使用环境变量 `SUBTITLE_API_KEY`。
- **`api_url`**: API 端点地址，填写完整的 Chat Completions URL（如 `https://api.openai.com/v1/chat/completions`）。
- **`model_id`**: 调用的模型 ID。
- **`temperature`**: 生成温度，数值越低越稳定。
- **`max_tokens`**: 最大输出 token 数。设为 `0` 时按 `ceil(input_tokens × 1.5)` 自动计算；留空或删除则不限制。

#### 配置示例

```json
{
  "api_key": "sk-...",
  "api_url": "https://api.openai.com/v1/chat/completions",
  "model_id": "gpt-4.1-mini",
  "temperature": 0.3,
  "max_tokens": 4096
}
```

#### 翻译配置

- **`mode`**: 翻译模式，支持 `bilingual`（双语：原文 + *译文*）和 `translation_only`（纯翻译）。
- **`chunk_size`**: 每次请求发送的字幕条目数。
- **`max_workers`**: 并发请求的线程数。
- **`rpm_limit`**: 每分钟最大请求数限制。
- **`source_lang_code` & `target_lang_code`**: 源语言和目标语言代码，用于生成字幕文件名。常用国家代码包括：中文 `cn`，日文 `jp`，英文 `en`，韩文 `ko`。

### 词汇表文件 (`glossary.json`)

用于定义需要固定翻译的专有名词，确保翻译一致性：

```json
{
  "フリーレン": "芙莉莲",
  "フェルン": "菲伦"
}
```

---

## 工具 2：双语字幕合并 (`merge_srt.py`)

如果你已经拥有了外文字幕和中文字幕（或任意两种语言的字幕），希望将它们合并为一个双语字幕文件，可以使用该脚本。合并后的字幕默认会在译文处包裹 `<i>` 标签。

### 运行程序

```bash
uv run merge_srt.py <原文字幕文件.srt> <翻译字幕文件.srt>
```

### 示例

```bash
uv run merge_srt.py S03E11.srt S03E11_chinese.srt
```

**输出：**
脚本会读取 `config.json` 中的 `source_lang_code` 和 `target_lang_code`，自动生成名为 `S03E11_jp_cn.srt` 的合并文件，内容类似：

```srt
3
00:00:21,438 --> 00:00:24,941
（綾小路の父）
生まれながらの天才では意味がない
<i>（绫小路之父）天生的天才是没有意义的</i>
```

### 合并规则
1. 以输入的第一个文件（原文字幕）的时间轴和序号为基准。
2. 查找第二个文件（翻译字幕）中对应序号的文本，将其合并为单行，并添加 `<i>` 标签，附在原文字幕的最下方。

---

## 文件结构

```
.
├── translate_subtitles.py  # 自动字幕翻译脚本
├── merge_srt.py            # 双语字幕合并脚本
├── config.json             # 翻译脚本配置文件
├── glossary.json           # 词汇表（可选）
└── README.md               # 项目文档
```

## 注意事项

1. `translate_subtitles.py` 翻译结果依赖于 AI 模型的质量，建议人工复核。
2. 请不要将带有真实 API Key 的 `config.json` 提交到公开仓库，优先使用环境变量 `SUBTITLE_API_KEY`。

## 许可证

MIT License
