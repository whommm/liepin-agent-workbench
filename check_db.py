import sqlite3
conn = sqlite3.connect('liepin_agent_workbench.db')
cursor = conn.cursor()

# 查看所有任务/岗位
cursor.execute("SELECT id, title, status, jd_text, created_at FROM search_sessions ORDER BY created_at DESC")
rows = cursor.fetchall()

with open('db_output.txt', 'w', encoding='utf-8') as f:
    f.write(f"共有 {len(rows)} 个寻访任务:\n\n")
    for r in rows:
        f.write(f"ID: {r[0]}\n")
        f.write(f"岗位名称: {r[1]}\n")
        f.write(f"状态: {r[2]}\n")
        jd = r[3] or 'None'
        f.write(f"JD: {jd[:800]}...\n")
        f.write(f"创建时间: {r[4]}\n")
        f.write("-" * 60 + "\n")

print("Done")
