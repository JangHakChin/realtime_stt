from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

# ── 도구 정의 ──────────────────────────────────────────────
@tool
def calculator(expression: str) -> str:
    """수학 계산을 해줍니다. 예: '2 + 2', '100 * 3.14'"""
    try:
        return f"계산 결과: {eval(expression)}"
    except Exception as e:
        return f"계산 오류: {e}"

@tool
def save_to_file(content: str) -> str:
    """텍스트 내용을 result.txt 파일로 저장합니다."""
    with open("result.txt", "w", encoding="utf-8") as f:
        f.write(content)
    return "result.txt 파일로 저장 완료!"

# ── Agent 생성 & 실행 ──────────────────────────────────────
llm = ChatOllama(model="qwen2.5:7b")   # tool calling 지원 모델
tools = [calculator, save_to_file]
agent = create_react_agent(llm, tools)

result = agent.invoke({
    "messages": [("human", "이거 영어로 번역해줘. '24년 5월 25일의 미세먼지 농도는?'")]
    # "messages": [("human", "지구 둘레(40075km)를 빛의 속도(초속 299792km)로 나누면 몇 초인지 계산하고, 결과를 파일로 저장해줘")]
})

for message in result["messages"]:
    print(f"\n[{message.type.upper()}]")
    print(message.content)