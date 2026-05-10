import subprocess

content = subprocess.check_output(['git', 'show', '3acfaa1:liepin_agent/core/liepin_search_service.py'], encoding='utf-8')
lines = content.split('\n')

constant_names = ['_AGE_PATTERN', '_EDUCATION_PATTERN', '_WORK_YEARS_PATTERN', '_SALARY_PATTERN', '_COMPANY_MARKERS', '_JOB_KEYWORDS', '_PERSONAL_TAGS', '_INVALID_CITY_WORDS', '_COMPANY_TITLE_SEPARATORS', 'FILTER_CARD_MARKERS', 'CANDIDATE_NOISE_MARKERS']

starts = []
for i, line in enumerate(lines):
    stripped = line.rstrip()
    if any(stripped.startswith('    ' + name) for name in constant_names):
        starts.append(i)

# Find ends: next line that is a def, @, or another constant, or empty line followed by non-indented
ends = []
for idx, s in enumerate(starts):
    e = s + 1
    while e < len(lines):
        stripped = lines[e].rstrip()
        if stripped.startswith('    def ') or stripped.startswith('    @'):
            break
        if any(stripped.startswith('    ' + name) for name in constant_names):
            break
        if stripped and not stripped.startswith(' ') and not stripped.startswith('\t'):
            break
        e += 1
    ends.append(e)

all_lines = []
for s, e in zip(starts, ends):
    all_lines.extend(lines[s:e])
    all_lines.append('\n')

with open('_extraction_constants.txt', 'w', encoding='utf-8') as f:
    f.write(''.join(all_lines))

print(f"Extracted {len(starts)} constants")
