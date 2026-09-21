from __future__ import annotations

import os
import stat
import textwrap

from standalonecad_web.providers.app_server import AppServerClient
from standalonecad_web.web_agent import PLAN_OUTPUT_SCHEMA


FAKE_SERVER = r'''#!/usr/bin/env python3
import json, sys

account = {"type":"chatgpt","email":"owner@example.com","planType":"plus"}

def send(value):
    print(json.dumps(value, separators=(",",":")), flush=True)

for raw in sys.stdin:
    if not raw.strip():
        continue
    message=json.loads(raw)
    request_id=message.get("id")
    method=message.get("method")
    if request_id is None:
        continue
    if method == "initialize":
        send({"id":request_id,"result":{"userAgent":"fake","platformFamily":"unix","platformOs":"linux"}})
    elif method == "account/read":
        send({"id":request_id,"result":{"account":account,"requiresOpenaiAuth":True}})
    elif method == "account/login/start":
        send({"id":request_id,"result":{"type":"chatgptDeviceCode","loginId":"login-1","verificationUrl":"https://auth.openai.com/codex/device","userCode":"TEST-1234"}})
        send({"method":"account/login/completed","params":{"loginId":"login-1","success":True,"error":None}})
    elif method == "account/logout":
        send({"id":request_id,"result":{}})
    elif method == "account/rateLimits/read":
        send({"id":request_id,"result":{"rateLimits":{"limitId":"codex","primary":{"usedPercent":12,"windowDurationMins":300,"resetsAt":1900000000}}}})
    elif method == "model/list":
        send({"id":request_id,"result":{"data":[{"id":"model-default","model":"model-default","displayName":"Default Model","isDefault":True,"defaultReasoningEffort":"medium","supportedReasoningEfforts":[{"reasoningEffort":"low"},{"reasoningEffort":"medium"}]}],"nextCursor":None}})
    elif method == "thread/start":
        send({"id":request_id,"result":{"thread":{"id":"thr-1","sessionId":"thr-1"}}})
    elif method == "turn/start":
        send({"id":request_id,"result":{"turn":{"id":"turn-1","status":"inProgress","items":[],"error":None}}})
        text=json.dumps({"calls":[{"tool":"cad_create_box","arguments":{"length_mm":10,"width_mm":10,"height_mm":10,"origin_mm":[0,0,0],"centered":False,"operation":"new","replace":False,"name":"Box1"}}],"note":"fake"},separators=(",",":"))
        send({"method":"item/completed","params":{"threadId":"thr-1","turnId":"turn-1","item":{"id":"item-1","type":"agentMessage","text":text}}})
        send({"method":"turn/completed","params":{"threadId":"thr-1","turn":{"id":"turn-1","threadId":"thr-1","status":"completed","items":[],"error":None}}})
    elif method == "turn/interrupt":
        send({"id":request_id,"result":{}})
    elif method == "thread/delete":
        send({"id":request_id,"result":{}})
    else:
        send({"id":request_id,"error":{"code":-32601,"message":"not implemented: "+str(method)}})
'''


def test_stdio_handshake_auth_models_and_structured_turn(tmp_path):
    executable = tmp_path / "fake-codex"
    executable.write_text(textwrap.dedent(FAKE_SERVER), encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    client = AppServerClient("user-1", tmp_path / "codex-home", tmp_path / "workspace", str(executable))
    try:
        account = client.account()
        assert account["account"]["planType"] == "plus"
        login = client.start_device_login()
        assert login["userCode"] == "TEST-1234"
        for _ in range(20):
            status = client.login_status(login["loginId"])
            if not status["pending"]:
                break
            __import__("time").sleep(0.01)
        assert status["success"] is True
        assert client.rate_limits()["rateLimits"]["primary"]["usedPercent"] == 12
        assert client.models()[0]["model"] == "model-default"
        plan = client.complete_json("make a box", output_schema=PLAN_OUTPUT_SCHEMA, effort="high")
        assert plan["calls"][0]["tool"] == "cad_create_box"
        assert plan["note"] == "fake"
    finally:
        client.stop()
    assert not client.running
