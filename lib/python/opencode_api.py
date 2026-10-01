"""The HTTP and event contracts used by Cerebro's private OpenCode servers."""

import base64
import json
import os
import urllib.request


class OpenCodeAPI:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip('/')
        self.health_path = '/api/info'
        self.event_path = '/api/event'
        self.headers = {'content-type': 'application/json'}
        password = os.environ.get('OPENCODE_PASSWORD', '')
        if password:
            credential = base64.b64encode(('opencode:' + password).encode()).decode()
            self.headers['authorization'] = 'Basic ' + credential

    def request(self, path, payload=None, timeout=30):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, headers=self.headers)
        return urllib.request.urlopen(request, timeout=timeout)

    def create(self, title, directory):
        payload = {'title': title, 'location': {'directory': directory}}
        with self.request('/api/session', payload, timeout=10) as response:
            return json.load(response)['data']['id']

    def prompt_requests(self, session_id, agent, model, text):
        path = "/api/session/" + session_id
        selection = None
        if model:
            provider, separator, model_id = model.partition("/")
            if not separator or not provider.strip() or not model_id.strip():
                raise ValueError("expected model in provider/model format")
            selection = {"providerID": provider.strip(), "modelID": model_id.strip()}
        requests = [(path + "/agent", {"agent": agent})]
        if selection:
            model_id, separator, variant = selection.pop("modelID").partition("#")
            selection["id"] = model_id
            if separator:
                selection["variant"] = variant
            requests.append((path + "/model", {"model": selection}))
        requests.append((path + "/prompt", {"text": text}))
        return requests

    def abort_path(self, session_id):
        return "/api/session/" + session_id + "/interrupt"


class V2Events:
    """Translate native v2 events to the run-json format our consumers use."""

    def __init__(self, session_id):
        self.session_id = session_id
        self.tools = {}

    def translate(self, event):
        data = event.get("data") or {}
        if data.get("sessionID") != self.session_id:
            return [], False
        kind = event.get("type")
        part = {"sessionID": self.session_id, "messageID": data.get("assistantMessageID")}
        terminal = kind in ("session.execution.succeeded", "session.execution.failed",
                            "session.execution.interrupted")
        output_type = "progress"
        if kind == "session.step.started":
            output_type = "step_start"
            part.update(type="step-start")
        elif kind == "session.text.ended":
            output_type = "text"
            part.update(type="text", text=data["text"])
        elif kind in ("session.step.ended", "session.execution.succeeded"):
            output_type = "step_finish"
            part.update(type="step-finish", reason=data.get("finish", "stop"))
        elif kind in ("session.step.failed", "session.execution.failed", "session.execution.interrupted"):
            error = data.get("error") or {"message": "Session interrupted: " + data.get("reason", "unknown")}
            return [{"type": "error", "sessionID": self.session_id, "error": error}], terminal
        elif kind.startswith("session.tool."):
            key = (data.get("assistantMessageID"), data.get("id"))
            tool = self.tools.setdefault(key, {})
            if kind == "session.tool.input.started":
                tool["name"] = data["name"]
            elif kind == "session.tool.called":
                tool["input"] = data["input"]
            elif kind in ("session.tool.success", "session.tool.failed"):
                output_type = "tool_use"
                state = {"input": tool.get("input", {}), "title": (data.get("metadata") or {}).get("title", "")}
                if kind == "session.tool.success":
                    state.update(status="completed", output="".join(
                        content.get("text", "") for content in data["content"] if content.get("type") == "text"))
                else:
                    state.update(status="error", error=data["error"].get("message", "unknown error"))
                part.update(type="tool", tool=tool.get("name", "?"), callID=data["id"], state=state)
                self.tools.pop(key, None)
        return [{"type": output_type, "sessionID": self.session_id, "part": part}], terminal
