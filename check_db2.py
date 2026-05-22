import sqlite3
conn = sqlite3.connect('liepin_agent_workbench.db')
cursor = conn.cursor()

# 1. 查看所有 session 的 jd_text
cursor.execute("SELECT id, title, jd_text FROM search_sessions ORDER BY created_at DESC")
print("=== 所有 Session 的 JD ===")
for r in cursor.fetchall():
    print(f"ID: {r[0][:16]}... | 标题: {r[1]} | JD前80字: {r[2][:80] if r[2] else 'None'}...")

print("\n=== 最新 3 个 criteria_version ===")
cursor.execute("SELECT id, session_id, source_jd_text, keywords_text FROM match_criteria_versions ORDER BY created_at DESC LIMIT 3")
for r in cursor.fetchall():
    print(f"ID: {r[0][:16]}... | session: {r[1][:16]}... | JD前80字: {r[2][:80] if r[2] else 'None'}... | 关键词: {r[3][:60] if r[3] else 'None'}...")

print("\n=== 查找包含'文创'或'潮玩'的记录 ===")
cursor.execute("SELECT id, title, jd_text FROM search_sessions WHERE jd_text LIKE '%文创%' OR jd_text LIKE '%潮玩%'")
for r in cursor.fetchall():
    print(f"ID: {r[0]} | 标题: {r[1]} | JD: {r[2][:200]}...")

print("\n=== 查找 agent_decisions 中是否有硬编码 session_id ===")
cursor.execute("SELECT id, session_id, round_id, decision_type, payload FROM agent_decisions ORDER BY created_at DESC LIMIT 5")
for r in cursor.fetchall():
    print(f"ID: {r[0][:16]}... | session: {r[1][:16]}... | type: {r[3]} | payload: {str(r[4])[:150] if r[4] else 'None'}...")
