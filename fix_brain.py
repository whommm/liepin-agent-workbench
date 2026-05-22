path = r'D:\liepin-agent-workbench\liepin_agent\agent\brain.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find line numbers for LLMAgentBrain methods (0-indexed)
for i, line in enumerate(lines):
    if 'class LLMAgentBrain' in line:
        print(f'LLMAgentBrain starts at line {i+1}')
    if 'def build_criteria' in line and i > 100:
        print(f'LLM build_criteria at line {i+1}')
    if 'def initial_plan' in line and i > 100:
        print(f'LLM initial_plan at line {i+1}')
    if 'def observe_round' in line and i > 100:
        print(f'LLM observe_round at line {i+1}')
    if 'def decide_fetch' in line and i > 100:
        print(f'LLM decide_fetch at line {i+1}')
    if 'def review_round' in line and i > 100:
        print(f'LLM review_round at line {i+1}')
