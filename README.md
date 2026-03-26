# 字幕翻译工具

一个基于AI的日语字幕翻译工具，支持将日文字幕文件翻译成中文。提供双语模式和纯中文模式，支持自定义词汇表和批量并行翻译。

## 功能特点

- 支持SRT格式字幕文件的解析和翻译
- 双语模式：日文原文 + 中文翻译（中文部分用斜体显示）
- 纯中文模式：仅输出中文翻译
- 自定义词汇表：确保专有名词翻译的一致性
- 并发翻译：支持多线程批量处理，提高翻译效率
- 自动分块：将长字幕文件分块处理，避免超出API限制
- JSON格式输出：确保翻译结果的准确性和可解析性

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
cd /home/ubuntu/workspaces/oneself/subtitiles

# 使用 uv 同步依赖
uv sync
```

### 运行程序

```bash
# 使用 uv 运行脚本
uv run python translate_subtitles.py <输入字幕文件> [输出字幕文件]
```

## 配置文件说明

### config.json - 主配置文件

```json
{
  "api": {
    "api_key": "your-api-key",
    "api_url": "https://api.sensenova.cn/compatible-mode/v2/chat/completions",
    "model_id": "SenseChat-Turbo-1202",
    "temperature": 0.3,
    "max_tokens": 4096
  },
  "translation": {
    "mode": "chinese_only",
    "chunk_size": 20,
    "max_workers": 3,
    "rpm_limit": 60,
    "source_language": "日语",
    "target_language": "中文"
  },
  "prompts": {
    "system_prompt": "...",
    "translation_prompt": "...",
    "bilingual_prompt": "..."
  }
}
```

#### API 配置 (api)

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `api_key` | API密钥 | 必填 |
| `api_url` | API接口地址 | 必填 |
| `model_id` | 使用的模型名称 | 必填 |
| `temperature` | 温度参数，控制输出的随机性（0-1） | 0.3 |
| `max_tokens` | 单次请求的最大token数 | 4096 |

#### 翻译配置 (translation)

| 参数 | 说明 | 可选值 | 默认值 |
|------|------|--------|--------|
| `mode` | 翻译模式 | `chinese_only` / `bilingual` | `chinese_only` |
| `chunk_size` | 每次翻译的字幕条目数量 | 正整数 | 20 |
| `max_workers` | 并发线程数 | 正整数 | 3 |
| `rpm_limit` | 每分钟请求数限制（Requests Per Minute） | 正整数 | 60 |
| `source_language` | 源语言 | 任意语言名称 | 日语 |
| `target_language` | 目标语言 | 任意语言名称 | 中文 |

#### 提示词配置 (prompts)

| 参数 | 说明 |
|------|------|
| `system_prompt` | 系统提示词，定义AI的角色和任务 |
| `translation_prompt` | 纯中文模式的翻译提示词 |
| `bilingual_prompt` | 双语模式的翻译提示词 |

### glossary.json - 词汇表文件

用于定义需要固定翻译的专有名词，确保翻译一致性。

```json
{
  "フリーレン": "芙莉莲",
  "フェルン": "菲伦",
  "シュタルク": "修塔尔克",
  "ヒンメル": "辛美尔"
}
```

格式：`"原文": "译文"`

## 使用方法

### 基本用法

```bash
uv run python translate_subtitles.py <输入字幕文件> [输出字幕文件]
```

### 示例

1. **使用自动输出文件名**（推荐）
   ```bash
    translate_subtitles.py subtitles_jp.srt
   # 输出: subtitles_cn.srt
   ```

2. **指定输出文件名**
   ```bash
   uv run python translate_subtitles.py subtitles_jp.srt my_subtitles.srt
   ```

### 输出文件名规则

如果不指定输出文件名，工具会根据输入文件名自动生成：
- 如果输入文件名以 `_jp` 结尾（如 `subtitles_jp.srt`），输出文件名为 `subtitles_cn.srt`
- 否则，输出文件名为 `原文件名_translated.srt`

## 输出格式

### 纯中文模式 (mode: "chinese_only")

```
1
00:00:00,000 --> 00:00:02,000
这是第一句字幕的中文翻译

2
00:00:02,500 --> 00:00:04,000
这是第二句字幕的中文翻译
```

### 双语模式 (mode: "bilingual")

```
1
00:00:00,000 --> 00:00:02,000
日文原文
<i>这是中文翻译</i>

2
00:00:02,500 --> 00:00:04,000
第二句日文原文
<i>第二句中文翻译</i>
```

## 性能调优建议

1. **调整 chunk_size**
   - 较大的 `chunk_size` 可以减少API调用次数，但可能超出单次请求的token限制
   - 较小的 `chunk_size` 更稳定，但会增加API调用次数
   - 建议值：10-30

2. **调整 max_workers**
   - 较大的 `max_workers` 可以提高翻译速度，但会增加API并发压力
   - 较小的 `max_workers` 更稳定，但翻译速度较慢
   - 建议值：3-5

3. **调整 temperature**
   - 较低的值（0.1-0.3）：翻译更一致，适合需要标准翻译的场景
   - 较高的值（0.5-0.7）：翻译更灵活，但可能不够一致

## 文件结构

```
.
├── translate_subtitles.py  # 主程序文件
├── config.json             # 配置文件
├── glossary.json           # 词汇表文件
└── README.md               # 本文档
```

## 注意事项

1. 确保 `config.json` 文件存在且配置正确
2. API密钥需要有效且有足够的调用额度
3. 翻译结果依赖于AI模型的质量，建议人工复核重要内容
4. 词汇表可以提高专有名词的翻译一致性，建议为作品准备专门的词汇表

## 许可证

MIT License