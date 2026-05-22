with open(r'D:\liepin-agent-workbench\liepin_agent\agent\brain.py', 'r', encoding='utf-8') as f:
    content = f.read()

start = content.find('        prompt = """请从 JD 中提取')
end = content.find('.format(jd=jd_text or "", notes=user_notes or "")', start)
actual = content[start:end+len('.format(jd=jd_text or "", notes=user_notes or "")')]

with open(r'D:\liepin-agent-workbench\actual_bc.txt', 'w', encoding='utf-8') as f:
    f.write(actual)
print('Wrote actual_bc.txt')
