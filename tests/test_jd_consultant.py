from liepin_agent.core.config import ConfigManager
from liepin_agent.services.jd_consultant import JDConsultant


class StubLLMClient:
    def __init__(self, reply="顾问回复"):
        self.calls = []
        self._reply = reply

    def chat(self, prompt, system_message=""):
        self.calls.append({"prompt": prompt, "system_message": system_message})
        return self._reply


def _history():
    return [
        {"role": "user", "content": "这是JD：跨境电商运营经理，负责亚马逊店铺。"},
        {"role": "assistant", "content": "我的分析与反问：经验年限？base哪里？"},
        {"role": "user", "content": "要求5年经验，base深圳"},
    ]


def test_reply_formats_transcript_with_role_labels():
    client = StubLLMClient()
    consultant = JDConsultant(client)

    result = consultant.reply(_history())

    assert result == "顾问回复"
    call = client.calls[0]
    assert call["system_message"]
    prompt = call["prompt"]
    assert "用户：" in prompt
    assert "顾问：" in prompt
    assert prompt.index("跨境电商运营经理") < prompt.index("我的分析与反问")
    assert prompt.index("我的分析与反问") < prompt.index("base深圳")


def test_finalize_plan_uses_template_and_transcript():
    client = StubLLMClient(reply="一、岗位概述\n测试方案")
    consultant = JDConsultant(client)

    result = consultant.finalize_plan(_history())

    assert result.startswith("一、岗位概述")
    prompt = client.calls[0]["prompt"]
    assert "寻访方案" in prompt
    assert "七、建议寻访方向" in prompt
    assert "跨境电商运营经理" in prompt  # transcript embedded


def test_from_config_falls_back_to_default_model(tmp_path):
    manager = ConfigManager(str(tmp_path / "config.json"))
    manager.update(
        api_base_url="https://api.example.com/v1",
        api_key="k",
        model_name="agent-model",
    )

    consultant = JDConsultant.from_config(manager)

    assert consultant.llm_client.model_name == "agent-model"
    assert consultant.llm_client.temperature == 0.4  # chat_llm_temperature 默认值


def test_from_config_prefers_chat_model(tmp_path):
    manager = ConfigManager(str(tmp_path / "config.json"))
    manager.update(
        api_base_url="https://api.example.com/v1",
        api_key="k",
        model_name="agent-model",
        chat_model_name="chat-pro",
    )

    consultant = JDConsultant.from_config(manager)

    assert consultant.llm_client.model_name == "chat-pro"
