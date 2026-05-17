from openpyxl import Workbook, load_workbook

from liepin_agent.tools.excel_greeting import ExcelGreetingService
from liepin_agent.tools.greeting_text import GreetingTextGenerationService
from liepin_agent.tools.real_liepin import RealLiepinTool


def _make_excel(path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "候选人"
    sheet.append([
        "姓名",
        "公司",
        "职位",
        "金领",
        "匹配档位",
        "打招呼状态",
        "简历链接",
    ])
    sheet.append(["A候选人", "能源公司", "销售总监", "是", "A", "", "https://h.liepin.com/resume/showresumedetail/?res_id=a"])
    sheet.append(["B候选人", "能源公司", "销售经理", "是", "B", "", "/resume/showresumedetail/?res_id=b"])
    sheet.append(["C候选人", "能源公司", "销售", "是", "C", "", "https://h.liepin.com/resume/showresumedetail/?res_id=c"])
    sheet.append(["非金领", "能源公司", "销售", "否", "A", "", "https://h.liepin.com/resume/showresumedetail/?res_id=d"])
    sheet.append(["已打过", "能源公司", "销售", "是", "A", "已发送", "https://h.liepin.com/resume/showresumedetail/?res_id=e"])
    sheet.append(["外站", "能源公司", "销售", "是", "A", "", "https://example.com/resume/showresumedetail/?res_id=f"])
    workbook.save(path)
    workbook.close()


def test_excel_greeting_loads_only_ab_gold_candidates(tmp_path):
    path = tmp_path / "candidates.xlsx"
    _make_excel(path)

    candidates = ExcelGreetingService.load_greetable_candidates(path)

    assert [item.name for item in candidates] == ["A候选人", "B候选人"]
    assert candidates[1].profile_url == "https://h.liepin.com/resume/showresumedetail/?res_id=b"


def test_excel_greeting_dry_run_does_not_send_or_write(tmp_path):
    path = tmp_path / "candidates.xlsx"
    _make_excel(path)

    class Tool:
        def greet_candidate(self, candidate, message_template=""):
            raise AssertionError("dry-run should not send greetings")

    results = ExcelGreetingService(Tool()).greet_from_excel(path, dry_run=True)

    assert [item["status"] for item in results] == ["dry_run", "dry_run"]
    workbook = load_workbook(path)
    sheet = workbook["候选人"]
    headers = {cell.value: index for index, cell in enumerate(sheet[1], start=1)}
    assert sheet.cell(row=2, column=headers["打招呼状态"]).value in {None, ""}
    workbook.close()


def test_excel_greeting_rechecks_gold_by_default(tmp_path):
    path = tmp_path / "candidates.xlsx"
    _make_excel(path)
    payloads = []

    class Tool:
        def greet_candidate(self, candidate, message_template=""):
            payloads.append(candidate)
            return {"status": "success", "message": "ok", "error": ""}

    ExcelGreetingService(Tool()).greet_from_excel(path, delay_min=0, delay_max=0)

    assert [payload["skip_gold_check"] for payload in payloads] == [False, False]


def test_excel_greeting_can_explicitly_trust_excel_gold(tmp_path):
    path = tmp_path / "candidates.xlsx"
    _make_excel(path)
    payloads = []

    class Tool:
        def greet_candidate(self, candidate, message_template=""):
            payloads.append(candidate)
            return {"status": "success", "message": "ok", "error": ""}

    ExcelGreetingService(Tool()).greet_from_excel(
        path,
        delay_min=0,
        delay_max=0,
        verify_gold_on_page=False,
    )

    assert [payload["skip_gold_check"] for payload in payloads] == [True, True]


def test_liepin_profile_url_validation():
    assert RealLiepinTool._ensure_absolute_url("/resume/showresumedetail/?res_id=1") == "https://h.liepin.com/resume/showresumedetail/?res_id=1"
    assert RealLiepinTool._ensure_absolute_url("https://h.liepin.com/resume/showresumedetail/?res_id=1") == "https://h.liepin.com/resume/showresumedetail/?res_id=1"
    assert RealLiepinTool._ensure_absolute_url("https://example.com/resume/showresumedetail/?res_id=1") == ""
    assert RealLiepinTool._ensure_absolute_url("javascript:alert(1)") == ""
    assert RealLiepinTool._ensure_absolute_url("https://h.liepin.com/") == ""


def test_excel_greeting_writes_result_back(tmp_path):
    path = tmp_path / "candidates.xlsx"
    _make_excel(path)

    ExcelGreetingService.write_greeting_result(path, 2, "success", "已发送打招呼")
    workbook = load_workbook(path)
    sheet = workbook["候选人"]
    headers = {cell.value: index for index, cell in enumerate(sheet[1], start=1)}

    assert sheet.cell(row=2, column=headers["打招呼状态"]).value == "已发送"
    assert sheet.cell(row=2, column=headers["打招呼消息"]).value == "已发送打招呼"
    workbook.close()


def test_greeting_text_fallback_masks_salary():
    class FailingClient:
        def chat(self, prompt, system_message=""):
            raise RuntimeError("no api")

    service = GreetingTextGenerationService(FailingClient())
    text = service.generate(
        "销售总监",
        "深圳市南山区",
        "负责天然气设备客户开发和销售团队管理。薪资30-50万。",
        salary_range="30-50万",
    )

    assert text.startswith("您好，我是猎头顾问")
    assert "base深圳" in text
    assert "销售总监" in text
    assert "薪资可谈" in text
    assert "30-50" not in text
