# AI Subtitle Translation Tool

An AI-powered multilingual subtitle translation tool that translates subtitle files from any language to a target language. Supports bilingual mode and translation-only mode, with custom glossary and batch parallel translation capabilities.

## Features

- Support for SRT format subtitle parsing and translation
- Automatic source language detection by the model
- **Bilingual mode**: Original text + translation (translation displayed in italics)
- **Translation-only mode**: Outputs only the translated content
- **Custom glossary**: Ensures consistent translation of proper nouns
- **Concurrent translation**: Supports multi-threaded batch processing for improved efficiency
- **Automatic chunking**: Splits long subtitle files into chunks to avoid API limits
- **JSON format output**: Ensures accuracy and parseability of translation results

## Installation

This project uses [uv](https://github.com/astral-sh/uv) as the package manager.

### Install uv

If you don't have uv installed:

```bash
# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Install Dependencies

```bash
# Navigate to project directory
cd /home/ubuntu/workspaces/oneself/subtitiles

# Sync dependencies with uv
uv sync
```

### Run the Program

```bash
# Run script with uv
uv run python translate_subtitles.py <input_subtitle_file> [output_subtitle_file]
```

**Before running**, configure your API key (choose one of the two methods):

**Method 1: Environment Variable (Recommended)**
```bash
export SUBTITLE_API_KEY="your-api-key"
```

**Method 2: Configuration File**
Fill in the key in the `api.api_key` field of `config.json` (not recommended due to security risks)

## Configuration

### config.json - Main Configuration File

```json
{
  "api": {
    "api_key": "",
    "api_url": "https://api.sensenova.cn/compatible-mode/v2/chat/completions",
    "model_id": "SenseChat-Turbo-1202",
    "temperature": 0.3,
    "max_tokens": 8192
  },
  "translation": {
    "mode": "translation_only",
    "chunk_size": 15,
    "max_workers": 5,
    "rpm_limit": 60,
    "target_language": "Chinese"
  },
  "prompts": {
    "system_prompt": "...",
    "translation_prompt": "...",
    "bilingual_prompt": "..."
  }
}
```

#### API Configuration (api)

| Parameter | Description | Default |
|-----------|-------------|---------|
| `api_key` | API key placeholder, recommended to provide via `SUBTITLE_API_KEY` environment variable | Empty string |
| `api_url` | API endpoint URL | Required |
| `model_id` | Model name to use | Required |
| `temperature` | Temperature parameter controlling output randomness (0-1) | 0.3 |
| `max_tokens` | Maximum tokens per request | 8192 |

#### Translation Configuration (translation)

| Parameter | Description | Options | Default |
|-----------|-------------|---------|---------|
| `mode` | Translation mode | `bilingual` / `translation_only` | `translation_only` |
| `chunk_size` | Number of subtitle entries per translation batch | Positive integer | 15 |
| `max_workers` | Number of concurrent threads | Positive integer | 5 |
| `rpm_limit` | Requests Per Minute limit | Positive integer | 60 |
| `target_language` | Target language | Any language name | Chinese |

#### Prompt Configuration (prompts)

| Parameter | Description |
|-----------|-------------|
| `system_prompt` | System prompt defining the AI's role and task |
| `translation_prompt` | Prompt for translation-only mode |
| `bilingual_prompt` | Prompt for bilingual mode |

### glossary.json - Glossary File

Used to define proper nouns that need consistent translation.

```json
{
  "フリーレン": "芙莉莲",
  "フェルン": "菲伦",
  "シュタルク": "修塔尔ク",
  "ヒンメル": "辛美尔"
}
```

Format: `"original": "translation"`

## Usage

### Basic Usage

```bash
uv run python translate_subtitles.py <input_subtitle_file> [output_subtitle_file]
```

### Examples

1. **Auto-generated output filename** (Recommended)
   ```bash
   uv run python translate_subtitles.py subtitles.srt
   # If target language is "Chinese", output: subtitles_cn.srt
   ```

2. **Specify output filename**
   ```bash
   uv run python translate_subtitles.py subtitles.srt my_subtitles.srt
   ```

### Output Filename Rules

If no output filename is specified, the tool automatically generates one based on the input filename and target language:
- Output filename = `original_filename_language_code.srt`
- For example: if target language is "Chinese", the output filename gets `_cn` suffix; if target language is "English", it gets `_en` suffix.

## Output Format

### Translation-Only Mode (mode: "translation_only")

```
1
00:00:00,000 --> 00:00:02,000
This is the translated content of the first subtitle

2
00:00:02,500 --> 00:00:04,000
This is the translated content of the second subtitle
```

### Bilingual Mode (mode: "bilingual")

```
1
00:00:00,000 --> 00:00:02,000
Original content
<i>This is the translation</i>

2
00:00:02,500 --> 00:00:04,000
Second original content
<i>Second translation</i>
```

## Performance Tuning

1. **Adjust chunk_size**
   - Larger `chunk_size` reduces API calls but may exceed token limits per request
   - Recommended value: 10-30

2. **Adjust max_workers**
   - Larger `max_workers` increases translation speed but adds API concurrency pressure
   - Recommended value: 3-10 (depends on API limits)

3. **Adjust temperature**
   - Lower values (0.1-0.3): More consistent translation, suitable for standard translation scenarios
   - Higher values (0.5-0.7): More flexible translation but may be less consistent

## Project Structure

```
.
├── translate_subtitles.py  # Main program file
├── config.json             # Configuration file
├── glossary.json           # Glossary file
├── README.md               # English documentation (this file)
└── README_CN.md            # Chinese documentation
```

## API Key Configuration

This project supports two API key configuration methods:

### Method 1: Environment Variable (Recommended)

Set the environment variable before running:

**Linux/macOS:**
```bash
export SUBTITLE_API_KEY="your-api-key"
uv run python translate_subtitles.py input.srt
```

**Windows PowerShell:**
```powershell
$env:SUBTITLE_API_KEY="your-api-key"
uv run python translate_subtitles.py input.srt
```

**Permanent configuration (Linux/macOS):**
```bash
# Add to ~/.bashrc or ~/.zshrc
echo 'export SUBTITLE_API_KEY="your-api-key"' >> ~/.bashrc
source ~/.bashrc
```

### Method 2: Configuration File

Fill in the key directly in the `api.api_key` field of `config.json`:

```json
{
  "api": {
    "api_key": "your-api-key",
    ...
  }
}
```

> ⚠️ **Security Note**: The configuration file method has security risks as config.json may be accidentally committed to version control. It's recommended to use the environment variable method and add `.env` or any files containing keys to `.gitignore`.

## Notes

1. Ensure `config.json` exists and is configured correctly
2. It's recommended to provide a valid API key via the `SUBTITLE_API_KEY` environment variable
3. Translation results depend on AI model quality; important content should be reviewed manually
4. Glossary can improve proper noun translation consistency; consider preparing a specific glossary for each work

## Documentation

- [中文文档 (Chinese Documentation)](README_CN.md)

## License

MIT License