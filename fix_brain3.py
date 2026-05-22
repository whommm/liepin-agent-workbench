path = r'D:\liepin-agent-workbench\liepin_agent\agent\brain.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'def observe_round' in line:
        print(f'observe_round at line {i+1}')
        print('First 20 lines of body:')
        for j in range(i, min(i+20, len(lines))):
            print(f'{j+1}: {lines[j]}', end='')
        print('---')
    if 'def decide_fetch' in line and i > 300:
        print(f'decide_fetch at line {i+1}')
        print('First 20 lines of body:')
        for j in range(i, min(i+20, len(lines))):
            print(f'{j+1}: {lines[j]}', end='')
        print('---')
