import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "linear"))
import activities, graphql  # noqa: E402


def test_activity_mappings():
    assert activities.thought("x") == (graphql.THOUGHT, "x")
    t, body = activities.tool_action("read_file", {"path": "/a", "limit": 5})
    assert t == graphql.ACTION and "read_file" in body and "limit" in body and "path" in body
    assert activities.final_response("done")[0] == graphql.RESPONSE
    assert activities.error_activity("boom")[0] == graphql.ERROR
    assert activities.ack_thought()[0] == graphql.THOUGHT


def test_graphql_input_builders():
    inp = graphql.agent_activity_input("sess_1", graphql.RESPONSE, "hi")["input"]
    assert inp["agentSessionId"] == "sess_1" and inp["content"]["type"] == "response"
    c = graphql.comment_input("issue_1", "body", parent_id="p1")["input"]
    assert c["issueId"] == "issue_1" and c["parentId"] == "p1"


def test_session_registry():
    r = activities.SessionRegistry()
    assert r.get("k") is None
    r.bind("k", agent_session_id="as1", issue_id="i1")
    assert r.agent_session_id("k") == "as1"
    assert r.get("k")["issue_id"] == "i1"
    r.unbind("k")
    assert r.get("k") is None
