from langchain_ollama import OllamaLLM

llm = OllamaLLM(model="deepseek-r1:8b")

response = llm.invoke("안녕! 넌 누구야? 한국어로 대답해줘")
print(response) 