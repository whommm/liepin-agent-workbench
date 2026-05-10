import os
import re

with open('liepin_agent/agent/brain.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find all triple-quoted strings
matches = list(re.finditer(r'"""(.*?)"""', content, re.DOTALL))

os.makedirs('liepin_agent/prompts', exist_ok=True)

# Heuristic: prompts are long strings with Chinese or structured instructions
prompt_files = []
for i, m in enumerate(matches):
    text = m.group(1)
    if len(text) < 100:
        continue
    # Determine filename by first non-empty line
    first_line = text.strip().split('\n')[0].strip()
    # Clean up filename
    fname = re.sub(r'[^\w\u4e00-\u9fff]+', '_', first_line)[:40].strip('_')
    if not fname:
        fname = f"prompt_{i}"
    fpath = f'liepin_agent/prompts/{fname}.md'
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(text.strip())
    prompt_files.append(fpath)
    print(f'Wrote {fpath} ({len(text)} chars)')

print(f"Extracted {len(prompt_files)} prompts")
