#!/usr/bin/env python3
"""测试打招呼 + 索要简历全流程（支持手动登录后执行）"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from liepin_agent.tools.real_liepin import RealLiepinTool


def main():
    tool = RealLiepinTool()

    print("=" * 60)
    print("猎聘打招呼 + 索要简历 全流程测试")
    print("=" * 60)

    # 步骤 1：打开浏览器
    print("\n[1/3] 正在打开浏览器...")
    tool.open_browser()
    print("浏览器已打开。")

    # 步骤 2：检查登录状态
    print("\n[2/3] 检查登录状态...")
    if not tool.check_login():
        print("\n[!] 检测到未登录，请在弹出的浏览器窗口中手动登录猎聘。")
        input("    登录完成后，请回到这里按回车键继续...")

        if not tool.check_login():
            print("[X] 仍然未登录，测试终止。")
            tool.close_browser()
            return

    print("[OK] 已登录，准备执行测试。")
    import time
    time.sleep(10)  # 给浏览器留时间完全恢复登录状态

    # 步骤 3：执行打招呼 + 索要简历
    print("\n[3/3] 开始执行打招呼 + 索要简历...")

    candidate = {
        "name": "易先生",
        "profile_url": (
            "https://h.liepin.com/resume/showresumedetail/"
            "?showsearchfeedback=1&res_id_encode=ef2def6681Y113210659c20"
            "&index=0&position=0&cur_page=0&pageSize=30"
            "&ck_id=41ace375-737a-458e-8a65-7fa586d24107"
            "&sk_id=41ace375-737a-458e-8a65-7fa586d24107"
            "&fk_id=41ace375-737a-458e-8a65-7fa586d24107"
            "&sfrom=RES_SEARCH&res_source=1&type=normal"
            "&sss=a1fbe31560e9e7759c1586dd9c04c0c8"
            "&sScene=49382s7WS"
        ),
        "current_company": "AInvest",
        "current_title": "Product Manager",
        "is_gold_collar": True,
        "skip_gold_check": True,
    }

    message = (
        "您好，我是猎头顾问，目前有个base上海的高级投资经理机会，"
        "负责资产配置及投资策略，团队氛围好，年薪80-150万可谈，"
        "方便的话能发一份您的简历看看吗？"
    )

    print(f"\n候选人: {candidate['name']}")
    print(f"职位: {candidate['current_title']} @ {candidate['current_company']}")
    print(f"自定义消息: {message}")
    print("-" * 60)

    try:
        result = tool.greet_candidate(
            candidate,
            message_template=message,
            request_resume=True,
        )

        print("\n" + "=" * 60)
        print("执行结果")
        print("=" * 60)
        for key, value in result.items():
            print(f"  {key:25s}: {value}")

        status = result.get("status")
        if status == "success":
            print("\n[PASS] 打招呼成功")
            resume_status = result.get("request_resume_status", "")
            if resume_status == "已发送索要简历":
                print("[PASS] 索要简历成功")
            elif resume_status:
                print(f"[WARN] 索要简历状态: {resume_status}")
        elif status == "already_greeted":
            print("\n[INFO] 该候选人已打过招呼（之前测试过），这是预期内的。")
            print("       如果需要完整测试，请换一个新的候选人 profile_url")
        elif status == "skipped":
            print("\n[INFO] 候选人被跳过（非金领）")
        else:
            print(f"\n[FAIL] 测试失败: {result.get('error')}")

    except Exception as exc:
        print(f"\n[FAIL] 执行异常: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n关闭浏览器...")
        tool.close_browser()
        print("测试结束。")


if __name__ == "__main__":
    main()
