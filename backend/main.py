from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .rag_engine import RAGEngine
import shutil
import os

app = FastAPI()
rag_engine = RAGEngine()

# Allow frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 프론트엔드 정적 파일 설정
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")

@app.get("/")
async def read_index():
    return FileResponse("frontend/index.html")

# app.js 같은 파일들을 루트에서 찾을 수 있게 추가 (선택 사항)
@app.get("/app.js")
async def read_app_js():
    return FileResponse("frontend/app.js")

@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    status = rag_engine.process_document(file_path)
    return {"message": status, "filename": file.filename}

@app.get("/chat")
async def chat_with_bot(query: str):
    response = rag_engine.ask_chatbot(query)
    return {"response": response}

@app.get("/generate-questions")
async def generate_questions():
    questions = rag_engine.generate_questions()
    return {"questions": questions}

@app.get("/stats")
async def get_stats():
    # Mock data for demonstration
    return {
        "correct_rate": 78,
        "progress": 65,
        "total_problems": 120,
        "solved_problems": 80
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
