import sys
import os
import json
from pathlib import Path

def parse_srt(filename):
    with open(filename, 'r', encoding='utf-8-sig') as f:
        lines = f.readlines()
        
    subtitles = []
    current_sub = None
    state = 0
    
    for line in lines:
        line = line.strip()
        if not line:
            if current_sub:
                subtitles.append(current_sub)
                current_sub = None
            state = 0
            continue
            
        if state == 0:
            if line.isdigit():
                current_sub = {'index': line, 'text': []}
                state = 1
        elif state == 1:
            if '-->' in line:
                current_sub['timecode'] = line
                state = 2
        elif state == 2:
            current_sub['text'].append(line)
            
    if current_sub:
        subtitles.append(current_sub)
        
    return subtitles

def main():
    if len(sys.argv) != 3:
        print("Usage: python merge_srt.py <source_srt> <target_srt>")
        sys.exit(1)
        
    jp_srt = sys.argv[1]
    cn_srt = sys.argv[2]
    
    print(f"Parsing source subtitles: {jp_srt}")
    jp_subs = parse_srt(jp_srt)
    
    print(f"Parsing target subtitles: {cn_srt}")
    cn_subs = parse_srt(cn_srt)
    
    cn_dict = {sub['index']: sub for sub in cn_subs}
    
    config_path = Path("config.json")
    source_code = "jp"
    target_code = "cn"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            translation_cfg = config.get("translation", {})
            source_code = translation_cfg.get("source_lang_code", source_code)
            target_code = translation_cfg.get("target_lang_code", target_code)
        except Exception as e:
            print(f"Warning: could not read config.json: {e}")
            
    suffix = f"_{source_code}_{target_code}.srt"
    
    if jp_srt.lower().endswith('.srt'):
        out_filename = jp_srt[:-4] + suffix
    else:
        out_filename = jp_srt + suffix
        
    print(f"Merging into: {out_filename}")
    with open(out_filename, 'w', encoding='utf-8') as f:
        for jp_sub in jp_subs:
            f.write(jp_sub['index'] + '\n')
            f.write(jp_sub['timecode'] + '\n')
            for text_line in jp_sub['text']:
                f.write(text_line + '\n')
                
            idx = jp_sub['index']
            if idx in cn_dict:
                # Concatenate Chinese text lines directly
                cn_text = "".join(cn_dict[idx]['text'])
                if cn_text:
                    f.write(f"<i>{cn_text}</i>\n")
            
            f.write('\n')
            
    print("Merge complete!")

if __name__ == '__main__':
    main()
