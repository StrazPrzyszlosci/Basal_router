#!/home/krzysiek/basal-env/bin/python
"""LangGraph orchestration: route -> run -> verify, with escalation to a stronger model.

The graph routes the task through basal, executes it on the chosen free model, and verifies the
answer with a basal `noul` question (the compress -> generate -> verify pattern from Basal's
docs/TASKS.md). A rejected answer is retried once on the strongest free reasoning model.
"""
import json
import sys
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

import router

MAX_ATTEMPTS = 2
ESCALATION_MODEL = "nemotron-ultra"


class RouterState(TypedDict, total=False):
    task: str
    decision: dict
    answer: str
    verified: bool
    attempts: int


def route_node(state: RouterState) -> RouterState:
    decision = router.route(state["task"])
    return {"decision": decision}


def run_node(state: RouterState) -> RouterState:
    answer = router.execute(state["decision"]["model"], state["task"], 600, runner="hermes")
    return {"answer": answer}


def verify_node(state: RouterState) -> RouterState:
    request = {
        "state": f"Task:\n{state['task']}\n\nAnswer:\n{state['answer'][-3000:]}",
        "lang": "en",
        "questions": {"ok": {
            "type": "noul",
            "instructions": "Does the answer above properly address the task?",
            "criteria": {"true": "yes", "false": "no"},
        }},
    }
    response = httpx.post(f"{router.BASAL_URL}/v1/systemone", json=request, timeout=180)
    response.raise_for_status()
    p_ok = response.json()["answers"]["ok"]["noul"]
    return {"verified": p_ok >= 0.6, "attempts": state.get("attempts", 0) + 1}


def escalate_node(state: RouterState) -> RouterState:
    answer = router.execute(ESCALATION_MODEL, state["task"], 600, runner="hermes")
    return {"answer": answer, "verified": True}


def after_verify(state: RouterState) -> str:
    if state.get("verified") or state.get("attempts", 0) >= MAX_ATTEMPTS:
        return END
    return "escalate"


def build_graph():
    graph = StateGraph(RouterState)
    graph.add_node("route", route_node)
    graph.add_node("run", run_node)
    graph.add_node("verify", verify_node)
    graph.add_node("escalate", escalate_node)
    graph.add_edge(START, "route")
    graph.add_edge("route", "run")
    graph.add_edge("run", "verify")
    graph.add_conditional_edges("verify", after_verify, {"escalate": "escalate", END: END})
    graph.add_edge("escalate", END)
    return graph.compile()


def main():
    task = " ".join(sys.argv[1:]).strip()
    if not task:
        raise SystemExit('usage: router_graph.py "task"')
    result = build_graph().invoke({"task": task})
    print(json.dumps({
        "task": task,
        "decision": result.get("decision"),
        "answer": result.get("answer"),
        "verified": result.get("verified"),
        "attempts": result.get("attempts"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
